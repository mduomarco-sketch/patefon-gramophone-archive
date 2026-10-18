"""Gift builder: read-only source isolation, transcoding, tag preservation,
image resizing, incremental delta-sync, and no cookies.txt leakage."""

import json
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("PIL")
ffmpeg_mod = pytest.importorskip("imageio_ffmpeg")
from mutagen.mp3 import MP3
from mutagen.id3 import ID3
from PIL import Image

from rr_archive import gift_builder as gb
from rr_archive.state import State

FFMPEG = ffmpeg_mod.get_ffmpeg_exe()


def _sine(path: Path, codec_args, seconds=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", *codec_args, str(path)],
                   check=True, stdin=subprocess.DEVNULL)


def _big_jpeg(path: Path, size=(1600, 1500)):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size)
    for x in range(0, size[0], 4):                 # some detail so it isn't trivially tiny
        for y in range(0, size[1], 4):
            img.putpixel((x, y), ((x * 7) % 256, (y * 5) % 256, (x + y) % 256))
    img.save(path, "JPEG", quality=95)


@pytest.fixture
def source(tmp_path):
    """A tiny but realistic master archive (stereo MP3, FLAC, big JPEGs, a PDF)."""
    arch = tmp_path / "russian_records_archive"
    rows = []

    mp3 = arch / "Russian" / "Cat" / "Artist" / "1950 - Song [1].mp3"
    _sine(mp3, ["-ac", "2", "-codec:a", "libmp3lame", "-q:a", "2"])
    lab1 = mp3.with_name(mp3.stem + " (label).jpg")
    _big_jpeg(lab1)
    rows.append(dict(item_id="1", media_type="MUSIC", language="Russian", category="Cat", artist="Artist",
                     composer="Comp", title="Song", year="1950", record_label="Aprelevka", catalog_number="123",
                     local_filepath=str(mp3), label_local_paths=json.dumps([str(lab1)]), pdf_local_path=None))

    flac = arch / "Instrumental" / "Cat" / "Band" / "1940 - Waltz [2].flac"
    _sine(flac, ["-ac", "2", "-codec:a", "flac"])
    lab2 = flac.with_name(flac.stem + " (label).jpg")
    _big_jpeg(lab2)
    rows.append(dict(item_id="2", media_type="MUSIC", language="Instrumental", category="Cat", artist="Band",
                     composer="", title="Waltz", year="1940", record_label="", catalog_number="",
                     local_filepath=str(flac), label_local_paths=json.dumps([str(lab2)]), pdf_local_path=None))

    scan = arch / "Newspapers_and_Print" / "Russian" / "Cat" / "1913 - Paper [3].jpg"
    _big_jpeg(scan)
    pdf = scan.with_suffix(".pdf")
    pdf.write_bytes(b"%PDF-1.4\n" + b"0" * 5000 + b"\n%%EOF\n")
    rows.append(dict(item_id="3", media_type="PRINT", language="Russian", category="Cat", artist="",
                     composer="", title="Paper", year="1913", record_label="", catalog_number="",
                     local_filepath=str(scan), label_local_paths="[]", pdf_local_path=str(pdf)))

    db = tmp_path / "archive_state.db"
    st = State(db)
    for r in rows:
        r.setdefault("source_url", "https://www.russian-records.com/details.php?image_id=" + r["item_id"])
        r["raw_metadata_json"] = json.dumps({"description": "label line", "raw_fields": {}})
        st.upsert_track(r["item_id"], r, "PENDING")
        st.mark_completed(r["item_id"], r["local_filepath"], "src_sha_" + r["item_id"], 999,
                          json.loads(r["label_local_paths"]), r["pdf_local_path"])
    st.close()
    (tmp_path / "cookies.txt").write_text("sessionid\tSECRET", encoding="utf-8")
    return tmp_path, arch, db


def test_build_gift_transcodes_resizes_and_preserves_tags(source, tmp_path):
    root, arch, db = source
    dest = tmp_path / "patefon_gift"
    before = {p: p.stat().st_size for p in arch.rglob("*") if p.is_file()}

    stats = gb.build_gift(source_db=db, source_archive=arch, dest_root=dest, workers=1)
    assert stats["synced"] == 3 and stats["failed"] == 0
    assert stats["dst_bytes"] < stats["src_bytes"]          # it actually got smaller

    # source is untouched
    assert {p: p.stat().st_size for p in arch.rglob("*") if p.is_file()} == before

    g = State(dest / "archive_state.db")
    gift = {r["item_id"]: r for r in g.conn.execute("SELECT * FROM tracks")}
    g.close()
    assert set(gift) == {"1", "2", "3"}

    # 1: stereo MP3 -> mono MP3, tags + embedded cover preserved
    m1 = Path(gift["1"]["local_filepath"])
    assert m1.suffix == ".mp3" and m1.exists() and str(dest) in str(m1)
    info = MP3(m1).info
    assert info.channels == 1 if hasattr(info, "channels") else info.mode == 3
    tags = ID3(m1)
    assert str(tags["TIT2"]) == "Song" and str(tags["TPE1"]) == "Artist"
    assert str(tags["TLAN"]) == "rus" and tags.getall("APIC")
    assert gift["1"]["file_sha256"] != "src_sha_1" and gift["1"]["file_size"] > 0

    # 2: FLAC -> mono MP3
    m2 = Path(gift["2"]["local_filepath"])
    assert m2.suffix == ".mp3" and m2.exists()
    assert str(ID3(m2)["TLAN"]) == "zxx"

    # label scans resized to <=900 and smaller
    lab = Path(json.loads(gift["1"]["label_local_paths"])[0])
    assert lab.exists() and max(Image.open(lab).size) <= 900
    src_lab = arch / "Russian" / "Cat" / "Artist" / "1950 - Song [1] (label).jpg"
    assert lab.stat().st_size < src_lab.stat().st_size

    # 3: print scan resized, PDF copied (under 15 MB)
    assert max(Image.open(gift["3"]["local_filepath"]).size) <= 900
    assert gift["3"]["pdf_local_path"] and Path(gift["3"]["pdf_local_path"]).exists()

    # standalone catalogue + launchers, and no secret leakage
    assert (dest / "russian_records_archive" / "index.html").exists()
    assert (dest / "start_patefon.bat").exists()
    assert not (dest / "cookies.txt").exists()
    assert not list(dest.rglob("cookies.txt"))


def test_incremental_delta_sync_skips_done(source, tmp_path):
    root, arch, db = source
    dest = tmp_path / "patefon_gift"
    first = gb.build_gift(source_db=db, source_archive=arch, dest_root=dest, workers=1)
    assert first["synced"] == 3
    again = gb.build_gift(source_db=db, source_archive=arch, dest_root=dest, workers=1)
    assert again["synced"] == 0 and again["skipped_existing"] == 3 and again["total"] == 0


def test_skip_pdfs_flag(source, tmp_path):
    root, arch, db = source
    dest = tmp_path / "patefon_gift"
    gb.build_gift(source_db=db, source_archive=arch, dest_root=dest, workers=1, skip_pdfs=True)
    g = State(dest / "archive_state.db")
    row = g.conn.execute("SELECT pdf_local_path FROM tracks WHERE item_id='3'").fetchone()
    g.close()
    assert row[0] is None
    assert not list((dest / "russian_records_archive").rglob("*.pdf"))


def test_source_db_opened_read_only(source, tmp_path):
    root, arch, db = source
    conn = gb._open_source_ro(db)
    with pytest.raises(Exception):
        conn.execute("UPDATE tracks SET title='x' WHERE item_id='1'")
    conn.close()


def test_only_completed_items_are_built(source, tmp_path):
    root, arch, db = source
    st = State(db)                       # add a PENDING item that must be ignored
    extra = dict(item_id="4", media_type="MUSIC", language="Russian", category="Cat", artist="Z",
                 title="Pending", local_filepath=str(arch / "x.mp3"), source_url="u",
                 label_image_urls="[]", raw_metadata_json="{}")
    st.upsert_track("4", extra, "PENDING")
    st.close()
    gb.build_gift(source_db=db, source_archive=arch, dest_root=tmp_path / "g", workers=1)
    g = State(tmp_path / "g" / "archive_state.db")
    ids = {r[0] for r in g.conn.execute("SELECT item_id FROM tracks")}
    g.close()
    assert "4" not in ids and ids == {"1", "2", "3"}
