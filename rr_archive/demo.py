"""`python scraper.py --seed-demo` - build a tiny synthetic archive so anyone
who clones the repo can open the full Patefon UI in seconds, with no cookies
and no contact with russian-records.com.

Everything here is generated locally: audio is pure sine tones (public domain),
label art is drawn with Pillow. Titles are invented placeholders. Output goes
to ./demo_archive/ (git-ignored); nothing touches the real archive or DB.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import config
from .crawler import _sha256, target_path
from .state import State, now
from .tagger import tag_flac, tag_mp3

DEMO_ROOT = config.PROJECT_ROOT / "demo_archive"

# (item_id, media_type, language, method, category, artist, title, year, fmt, freq, colour)
DEMO_ITEMS = [
    ("demo-1", "MUSIC", "Russian", "DEFAULT_CYRILLIC", "Demo Records", "Demo Singer",
     "Вечерний звон (Evening Bells), romance", "1912", "mp3", 196.0, (196, 72, 52)),
    ("demo-2", "MUSIC", "Instrumental", "INSTRUMENTAL", "Demo Records", "Demo Orchestra",
     "Waltz in A, orchestral", "1908", "flac", 220.0, (40, 60, 90)),
    ("demo-3", "MUSIC", "Ukrainian", "SCRIPT_NLP", "Demo Records", "Demo Kobzar",
     "Ніч яка місячна (Moonlit Night), song", "1910", "mp3", 262.0, (60, 110, 70)),
    ("demo-4", "RADIO_SPEECH", "Russian", "EXPLICIT_METADATA", "Demo Radio", "Demo Announcer",
     "Radio address (Выступление по радио)", "1950", "mp3", 330.0, (120, 70, 40)),
    ("demo-5", "PRINT", "Russian", "EXPLICIT_METADATA", "Demo Press", "",
     "Gramophone Gazette (Граммофонный вестник), 1910", "1910", "jpg", 0.0, (90, 80, 70)),
]


def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _tone(path: Path, seconds: int, freq: float, fmt: str, ffmpeg: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    codec = ["-codec:a", "libmp3lame", "-q:a", "6"] if fmt == "mp3" else ["-codec:a", "flac"]
    subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}", *codec, str(path)],
                   check=True, stdin=subprocess.DEVNULL)


def _label(path: Path, title: str, colour: tuple[int, int, int], size: int = 900) -> None:
    """An original, simple record-label image (concentric rings + a title band)."""
    from PIL import Image, ImageDraw
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (size, size), (18, 16, 14))
    d = ImageDraw.Draw(img)
    c = size // 2
    for r in range(c - 10, int(size * 0.17), -7):          # grooves
        shade = 24 + (r % 14)
        d.ellipse([c - r, c - r, c + r, c + r], outline=(shade, shade - 2, shade - 4), width=2)
    lr = int(size * 0.17)
    d.ellipse([c - lr, c - lr, c + lr, c + lr], fill=colour)   # paper label
    d.ellipse([c - int(size * 0.33), c - 10, c + int(size * 0.33), c + 10], fill=(0, 0, 0))  # title band hint
    for i, word in enumerate(title.split()[:4]):
        d.text((c - int(size * 0.3), c - 24 + i * 14), word[:22], fill=(240, 220, 180))
    d.ellipse([c - 7, c - 7, c + 7, c + 7], fill=(18, 16, 14))  # spindle hole
    img.save(path, "JPEG", quality=85)


def seed_demo(dest: Path = DEMO_ROOT) -> dict:
    arch = dest / "russian_records_archive"
    db = dest / "archive_state.db"
    arch.mkdir(parents=True, exist_ok=True)
    for p in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
        p.unlink(missing_ok=True)
    ffmpeg = _ffmpeg()
    state = State(db)
    try:
        for item_id, media, lang, method, cat, artist, title, year, fmt, freq, colour in DEMO_ITEMS:
            row = {"item_id": item_id, "media_type": media, "language": lang, "category": cat,
                   "artist": artist, "title": title, "year": year}
            raw = {"description": f"{title}\nDemo label transcription line\n{cat}",
                   "raw_fields": {"Recording Place": "Demo City", "Recording Date": year}}
            meta = {"source_url": "", "audio_url": "", "media_type": media, "language": lang,
                    "language_detection_method": method, "language_note": "demo item",
                    "category": cat, "category_path": cat, "artist": artist, "composer": "",
                    "title": title, "year": year, "record_label": cat, "catalog_number": item_id.upper(),
                    "matrix_number": "", "label_image_urls": "[]", "pdf_url": None,
                    "raw_metadata_json": json.dumps(raw, ensure_ascii=False)}
            state.upsert_track(item_id, meta, "PENDING")

            if media == "PRINT":                              # image-only scan
                main = target_path(row, arch, ".jpg")
                _label(main, title, colour)
                labels, pdf = [], None
            else:
                main = target_path(row, arch, f".{fmt}")
                _tone(main, 3, freq, fmt, ffmpeg)
                label = main.with_name(main.stem + " (label).jpg")
                _label(label, title, colour)
                cover = (label.read_bytes(), "image/jpeg")
                tagmeta = {"title": title, "artist": artist, "album": cat, "genre": cat, "year": year,
                           "language_iso": {"Russian": "rus", "Ukrainian": "ukr", "Instrumental": "zxx"}.get(lang, "und"),
                           "language": lang, "language_method": method, "publisher": cat,
                           "catalog_number": item_id.upper(), "source_url": "", "notes": raw["description"],
                           "item_id": item_id, "category_path": cat}
                (tag_flac if fmt == "flac" else tag_mp3)(main, tagmeta, cover)
                labels = [str(label)]
                pdf = None
            state.mark_completed(item_id, str(main), _sha256(main), main.stat().st_size, labels, pdf)

        from .export import STATIC_DIR, export_catalog
        try:                       # public demo always uses the neutral example dedication
            ded = json.loads((STATIC_DIR / "dedication.example.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ded = {"text": "A gift of old records"}
        out = export_catalog(state, arch, seed={}, rebuild_indexes=False, dedication=ded)
    finally:
        state.close()
    return {"items": len(DEMO_ITEMS), "index": out, "archive": arch, "db": db}


if __name__ == "__main__":
    info = seed_demo()
    print(f"demo archive: {info['items']} items -> {info['index']}")
