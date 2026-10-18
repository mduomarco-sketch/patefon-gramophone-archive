"""Findings from the first live test run (2026-10-17): ethnicity-style
language field, multi-value fields, FLAC audio, and re-filing items that
an older parser classified."""

import json
import struct
from pathlib import Path

import pytest
from mutagen.flac import FLAC

from conftest import BASE, audio_tag, details_page, label_img, row, sub, tiny_jpeg
from rr_archive import classify
from rr_archive.crawler import Crawler
from rr_archive.reclassify import refile_from_cache
from rr_archive.state import State
from rr_archive.tagger import tag_flac, verify_flac


def minimal_flac(seconds: int = 2, rate: int = 44100) -> bytes:
    """Valid STREAMINFO-only FLAC: enough for mutagen and the magic-byte check."""
    packed = (rate << 44) | (0 << 41) | (15 << 36) | (rate * seconds)
    info = struct.pack(">HH", 4096, 4096) + b"\x00" * 6 + packed.to_bytes(8, "big") + b"\x00" * 16
    return b"fLaC" + bytes([0x80]) + len(info).to_bytes(3, "big") + info + b"\x00" * 256


def ctx(title, lang="", cat="", genre="", artist="", desc=""):
    return classify.ItemContext(title=title, original_title="", category_path=["Records"],
                                description=desc, keywords="", language_field=lang, artist=artist,
                                accompaniment="", genre=genre, lyricist="", has_audio=True,
                                has_images=True, catalog_category=cat)


@pytest.mark.parametrize("c,expected", [
    # 'Language(s) or Ethnics' is an origin tag for instrumentals
    (ctx("Morning piece, chamber piece", "italian", "Violin with Piano", "Chamber piece"), "Instrumental"),
    (ctx("Test mazurka, solo piece", "polish, american", "Violin Solo with Piano", "Solo piece"), "Instrumental"),
    (ctx("Test waltz", "spanish", "Dance Orchestra", "Waltz", "Test Orchestra"), "Instrumental"),
    # vocal records keep the field's (first listed) language
    (ctx("Test song", "polish, american", "Vocal with Piano", "Song"), "Polish"),
    (ctx("Test aria", "italian", "Tenor with Orchestra", "Aria"), "Italian"),
    (ctx("Test periodical", "russian, german"), "Russian"),
    (ctx("Test song", "german / russian", "Vocal", "Song"), "German"),
])
def test_ethnic_field_and_multi_values(c, expected):
    assert classify.classify_language(c, classify.MUSIC).language == expected


def test_no_false_instrument_match():
    # 'harp' must not match inside other words
    c = ctx("Sharp tongue song", "russian", "Vocal", "Song")
    assert classify.classify_language(c, classify.MUSIC).language == "Russian"


def test_flac_tag_roundtrip(tmp_path):
    p = tmp_path / "x.flac"
    p.write_bytes(minimal_flac())
    tag_flac(p, {"title": "Тест", "artist": "Певец", "language_iso": "rus", "year": "ок. 1912",
                 "publisher": "Label, cat. 1", "source_url": BASE + "details.php?image_id=1",
                 "notes": "n", "item_id": "1", "language": "Russian",
                 "language_method": "EXPLICIT_METADATA"}, (tiny_jpeg(), "image/jpeg"))
    v = verify_flac(p)
    assert v["TLAN"] == "rus" and v["TIT2"] == "Тест" and v["APIC"] and v["length"] == 2.0
    f = FLAC(p)
    assert f["DATE"] == ["1912"] and f["ORGANIZATION"] == ["Label, cat. 1"]


def _flac_site(fake_site):
    site = dict(fake_site)
    site[BASE + "details.php?image_id=107"] = (200, {"Content-Type": "text/html"}, details_page(
        107, [(2, "Украинские песни")], "Flac song (Пісня), song", ["107"],
        [row("Title Name", "Flac song"), row("Language(s) or Ethnics", "ukrainian" + sub("Catalog category", "Vocal")),
         row("Artist(s)", "Тестовий Співак", "row2")],
        media_html='<audio src="./data/media/2/song.flac"></audio>' + label_img(2, "song")))
    site[BASE + "data/media/2/song.flac"] = (200, {"Content-Type": "audio/flac"}, minimal_flac())
    site[BASE + "data/media/2/song.jpg"] = (200, {"Content-Type": "image/jpeg"}, tiny_jpeg())
    return site


def test_flac_download_is_tagged(tmp_path, make_client, fake_site):
    state = State(tmp_path / "s.db")
    crawler = Crawler(make_client(_flac_site(fake_site)), state, tmp_path / "a", cache_dir=None,
                      control_dir=tmp_path / "ctl")
    state.add_page(BASE + "details.php?image_id=107", "details")
    crawler.run()
    r = state.get_track("107")
    assert r["status"] == "COMPLETED" and r["local_filepath"].endswith("[107].flac")
    f = FLAC(r["local_filepath"])
    assert f["LANGUAGE"] == ["ukr"] and f["ARTIST"] == ["Тестовий Співак"] and f.pictures
    state.close()


@pytest.fixture
def cached_archive(tmp_path, make_client):
    state = State(tmp_path / "s.db")
    crawler = Crawler(make_client(), state, tmp_path / "archive", cache_dir=tmp_path / "cache",
                      control_dir=tmp_path / "ctl")
    crawler.seed()
    crawler.run()
    return state, tmp_path / "archive", tmp_path / "cache"


def test_refile_moves_and_retags_stale_items(cached_archive):
    state, root, cache = cached_archive
    good = dict(state.get_track("103"))
    # Simulate an item filed by an older parser: wrong language/category on disk.
    stale_dir = root / "Unknown_Language" / "Old category" / "Unknown_Artist"
    stale_dir.mkdir(parents=True)
    stale = stale_dir / "Unknown_Year - old [103].mp3"
    Path(good["local_filepath"]).replace(stale)
    state.conn.execute("UPDATE tracks SET language='Unknown_Language', category='Old category', "
                       "artist='', language_detection_method='UNKNOWN', local_filepath=?, "
                       "label_local_paths='[]' WHERE item_id='103'", (str(stale),))
    state.conn.commit()
    # A manual override must survive the refile.
    state.conn.execute("UPDATE tracks SET language_detection_method='MANUAL', language='Polish' "
                       "WHERE item_id='102'")
    state.conn.commit()

    stats = refile_from_cache(state, root, cache)
    assert stats["refiled"] == 1 and not stats["errors"], stats
    r = state.get_track("103")
    assert r["language"] == "Instrumental" and r["local_filepath"] == good["local_filepath"]
    assert Path(r["local_filepath"]).exists() and not stale.exists()
    assert not (root / "Unknown_Language").exists()            # emptied folders pruned
    from mutagen.id3 import ID3
    assert str(ID3(r["local_filepath"])["TLAN"]) == "zxx"
    assert state.get_track("102")["language"] == "Polish"      # MANUAL untouched
    state.close()


def test_refile_requeues_when_media_changed(cached_archive):
    state, root, cache = cached_archive
    before = state.get_track("101")
    files = [Path(before["local_filepath"])] + [Path(p) for p in json.loads(before["label_local_paths"])]
    # The old parser had picked up a userpic as an extra label scan.
    state.conn.execute("UPDATE tracks SET label_image_urls=? WHERE item_id='101'",
                       (json.dumps([BASE + "data/media/1/zvon.jpg", BASE + "data/userpic/1.jpg"]),))
    state.conn.commit()
    stats = refile_from_cache(state, root, cache)
    assert stats["requeued"] == 1, stats
    r = state.get_track("101")
    assert r["status"] == "PENDING" and r["local_filepath"] is None
    assert json.loads(r["label_image_urls"]) == [BASE + "data/media/1/zvon.jpg"]
    quarantine = root.parent / "_superseded" / "101"
    assert all((quarantine / f.name).exists() for f in files)  # moved, never deleted
    assert not any(f.exists() for f in files)
    state.close()
