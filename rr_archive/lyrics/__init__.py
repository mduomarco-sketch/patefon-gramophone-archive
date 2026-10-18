"""Lyrics for Patefon: storage, provenance and the index the UI reads.

Sources, in order of trust:
  manual       a .lrc/.txt you add yourself (in _lyrics/ or next to the audio)
  wikisource   public-domain text from ru.wikisource.org (licence-checked)
  whisper      local speech-to-text of the recording (marked auto-transcribed)

Files live in russian_records_archive/_lyrics/{item_id}.lrc|.txt so they
survive re-classification moves; provenance is kept in _lyrics/_meta.json.
build_index() writes rr_archive/web/static/lyrics_index.json, which the
dashboard loads as a static file and the gift export embeds.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .. import config

LYRICS_DIR = config.ARCHIVE_ROOT / "_lyrics"
META_PATH = LYRICS_DIR / "_meta.json"
INDEX_PATH = Path(__file__).resolve().parent.parent / "web" / "static" / "lyrics_index.json"
SOURCE_RANK = {"manual": 3, "wikisource": 2, "whisper": 1}

_LRC_LINE = re.compile(r"^\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\](.*)$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_meta(path: Path | None = None) -> dict:
    try:
        return json.loads((path or META_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_meta(meta: dict, path: Path | None = None) -> None:
    _atomic_write(path or META_PATH, json.dumps(meta, ensure_ascii=False, indent=1))


def existing_source(item_id: str, meta: dict) -> str | None:
    """Source of the lyrics already stored for an item (None if none)."""
    if item_id in meta and (LYRICS_DIR / meta[item_id]["file"]).exists():
        return meta[item_id]["source"]
    for ext in (".lrc", ".txt"):
        if (LYRICS_DIR / f"{item_id}{ext}").exists():
            return "manual"
    return None


def store_lyrics(item_id: str, text: str, kind: str, source: str, *, meta: dict,
                 url: str | None = None, license: str | None = None, confidence: float | None = None,
                 language: str | None = None, extra: dict | None = None) -> bool:
    """Save lyrics unless a more trusted source already exists. Returns True if written."""
    current = existing_source(item_id, meta)
    if current and SOURCE_RANK.get(current, 0) >= SOURCE_RANK[source]:
        return False
    name = f"{item_id}.{kind}"
    for ext in ("lrc", "txt"):  # one file per item
        old = LYRICS_DIR / f"{item_id}.{ext}"
        if ext != kind and old.exists() and current != "manual":
            old.unlink()
    _atomic_write(LYRICS_DIR / name, text.strip() + "\n")
    meta[item_id] = {"file": name, "kind": kind, "source": source, "url": url, "license": license,
                     "confidence": None if confidence is None else round(float(confidence), 3),
                     "language": language, "created": now(), **(extra or {})}
    return True


def parse_lrc(text: str) -> list[list]:
    """[[seconds, line], ...] sorted; ignores [ar:]/[ti:] style tags."""
    out = []
    for raw in text.splitlines():
        m = _LRC_LINE.match(raw.strip())
        if m:
            frac = m.group(3) or "0"
            t = int(m.group(1)) * 60 + int(m.group(2)) + int(frac) / (10 ** len(frac))
            out.append([round(t, 2), m.group(4).strip()])
    return sorted(out, key=lambda x: x[0])


def _sidecar_for(row) -> Path | None:
    """A .lrc/.txt you placed next to the audio file (same stem)."""
    p = Path(row["local_filepath"] or "")
    for ext in (".lrc", ".txt"):
        cand = p.with_suffix(ext)
        if cand.exists():
            return cand
    return None


def build_index(conn, index_path: Path | None = None) -> dict:
    """Collect every item's lyrics into one JSON the UI can read.
    Read-only on conn (safe with a read-only connection during a crawl)."""
    meta = load_meta()
    rows = conn.execute("SELECT item_id, local_filepath FROM tracks WHERE status='COMPLETED' "
                        "AND media_type IN ('MUSIC','RADIO_SPEECH')").fetchall()
    index, counts = {}, {"manual": 0, "wikisource": 0, "whisper": 0}
    for row in rows:
        item_id = row[0]
        entry, path = meta.get(item_id), None
        if entry and (LYRICS_DIR / entry["file"]).exists():
            path = LYRICS_DIR / entry["file"]
        else:
            entry = None
            for ext in (".lrc", ".txt"):
                if (LYRICS_DIR / f"{item_id}{ext}").exists():
                    path = LYRICS_DIR / f"{item_id}{ext}"
        side = _sidecar_for({"local_filepath": row[1]})
        if side is not None:          # your own file next to the audio always wins
            path, entry = side, None
        if path is None:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        kind = "lrc" if path.suffix.lower() == ".lrc" and parse_lrc(text) else "txt"
        source = entry["source"] if entry else "manual"
        rec = {"kind": kind, "source": source, "url": entry.get("url") if entry else None,
               "license": entry.get("license") if entry else None,
               "confidence": entry.get("confidence") if entry else None}
        if kind == "lrc":
            rec["lines"] = parse_lrc(text)
        else:
            rec["text"] = text.strip()
        index[item_id] = rec
        counts[source] = counts.get(source, 0) + 1
    _atomic_write(index_path or INDEX_PATH, json.dumps(index, ensure_ascii=False, separators=(",", ":")))
    return {"tracks_with_lyrics": len(index), **counts}


def load_index(index_path: Path | None = None) -> dict:
    try:
        return json.loads((index_path or INDEX_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
