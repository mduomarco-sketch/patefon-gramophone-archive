"""Catalog serialization (shared by the dashboard API) and the standalone
russian_records_archive/index.html exporter."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .parser import notes_fields
from .tagger import first_year

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


def _rel(path: str | None, root: Path) -> str | None:
    if not path:
        return None
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None  # outside the archive root: never exposed


def notes_text(row) -> str:
    raw = json.loads(row["raw_metadata_json"] or "{}")
    parts = []
    if raw.get("description"):
        parts.append(raw["description"])
    extra = [f"{k}: {v}" for k, v in notes_fields(raw.get("raw_fields") or {})]
    if extra:
        parts.append("\n".join(extra))
    return "\n\n".join(parts)


def item_dict(row, root: Path, with_notes: bool = False) -> dict:
    labels = json.loads(row["label_local_paths"] or "[]")
    d = {
        "id": row["item_id"], "title": row["title"] or "", "artist": row["artist"] or "",
        "composer": row["composer"] or "", "year": first_year(row["year"]) or "",
        "year_raw": row["year"] or "", "branch": row["media_type"] or "NONE",
        "language": row["language"] or "", "method": row["language_detection_method"] or "",
        "note": row["language_note"] or "", "category": row["category"] or "",
        "category_path": row["category_path"] or "", "record_label": row["record_label"] or "",
        "catalog_number": row["catalog_number"] or "", "matrix_number": row["matrix_number"] or "",
        "status": row["status"], "downloaded_at": row["downloaded_at"] or "",
        "source_url": row["source_url"] or "", "last_error": row["last_error"] or "",
        "file": _rel(row["local_filepath"], root),
        "labels": [r for r in (_rel(p, root) for p in labels) if r],
        "pdf": _rel(row["pdf_local_path"], root) if "pdf_local_path" in row.keys() else None,
    }
    if with_notes:
        d["description"] = notes_text(row)
    return d


USER_PLAYLISTS = config.PROJECT_ROOT / "user_playlists.json"


def clean_seed(raw: dict | None) -> dict | None:
    """Validate curated playlists/likes (from the dashboard or a Patefon
    backup file). Returns {"playlists": [...], "liked": [...]} or None."""
    if not isinstance(raw, dict):
        return None
    playlists = []
    for pl in (raw.get("playlists") or [])[:500]:
        if not isinstance(pl, dict) or not isinstance(pl.get("name"), str):
            continue
        tracks = [str(t) for t in (pl.get("tracks") or []) if isinstance(t, (str, int))][:20000]
        playlists.append({"id": str(pl.get("id") or pl["name"])[:80], "name": pl["name"].strip()[:80] or "Playlist",
                          "tracks": list(dict.fromkeys(tracks)), "created": str(pl.get("created") or "")[:40]})
    liked = list(dict.fromkeys(str(x) for x in (raw.get("liked") or [])[:50000] if isinstance(x, (str, int))))
    if not playlists and not liked:
        return None
    return {"playlists": playlists, "liked": liked}


def save_user_playlists(seed: dict, path: Path = USER_PLAYLISTS) -> None:
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps({"app": "patefon", "version": 1, **seed}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def load_user_playlists(path: Path = USER_PLAYLISTS) -> dict | None:
    try:
        return clean_seed(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def export_catalog(state, archive_root: Path = config.ARCHIVE_ROOT, seed: dict | None = None,
                   rebuild_indexes: bool = True, dedication: dict | None = None) -> Path:
    """Write the gift index.html. seed = curated playlists/likes that open as
    her starting library; defaults to user_playlists.json when present.

    rebuild_indexes=False reuses the already-built spindle-fit and notes indexes
    and never writes into rr_archive/web/static - used by the gift builder, which
    must not modify anything in the source tree."""
    root = Path(archive_root)
    root.mkdir(parents=True, exist_ok=True)
    rows = state.conn.execute(
        "SELECT * FROM tracks WHERE status='COMPLETED' ORDER BY title").fetchall()
    items = [d for d in (item_dict(r, root, with_notes=True) for r in rows) if d["file"]]
    # Spindle-hole offsets so off-centre scans spin true (see labelfit.py).
    # Only reads from the database; measures labels not yet in the cache.
    from .labelfit import load_fits, update_fits
    if rebuild_indexes:
        try:
            update_fits(state.conn)
        except Exception:
            pass  # Pillow/numpy missing or an unreadable scan: discs stay photo-centred
    fits = load_fits()
    for d in items:
        if d["id"] in fits:
            d["fit"] = fits[d["id"]]
    # Lyrics already stored for a record (public-domain Wikisource texts or files
    # you added; see rr_archive/lyrics). Only index entries are embedded.
    try:
        from .lyrics import load_index
        lyrics = load_index()
    except Exception:
        lyrics = {}
    for d in items:
        if d["id"] in lyrics:
            d["lyrics"] = lyrics[d["id"]]
    # The archive's own texts (label transcription, liner notes, parent work),
    # re-extracted from cached pages so line breaks survive.
    if rebuild_indexes:
        try:
            from .notes import build_index as build_notes
            build_notes(state.conn)
        except Exception:
            pass
    from .notes import load_index as load_notes
    archive_notes = load_notes()
    for d in items:
        if d["id"] in archive_notes:
            d["notes"] = archive_notes[d["id"]]
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    payload = {"generated_at": generated, "items": items}
    # The dedication shown on first open. An explicit override wins (the demo
    # forces the neutral example); otherwise the personal dedication.json is
    # used, and a fresh clone falls back to the committed example.
    if dedication is not None:
        payload["dedication"] = dedication
    else:
        for ded_name in ("dedication.json", "dedication.example.json"):
            try:
                payload["dedication"] = json.loads((STATIC_DIR / ded_name).read_text(encoding="utf-8"))
                break
            except (OSError, ValueError):
                continue
    seed = clean_seed(seed) if seed is not None else load_user_playlists()
    if seed:
        known = {i["id"] for i in items}
        payload["seed"] = {
            # The version changes whenever the curation changes, so a re-exported
            # gift merges new picks without resurrecting what she removed.
            "version": hashlib.sha256(json.dumps(seed, sort_keys=True).encode()).hexdigest()[:16],
            "playlists": [{**pl, "tracks": [t for t in pl["tracks"] if t in known]} for pl in seed["playlists"]],
            "liked": [t for t in seed["liked"] if t in known],
        }
    data = json.dumps(payload, ensure_ascii=False)
    data = data.replace("</", "<\\/")  # cannot terminate the <script> element

    template = (STATIC_DIR / "static_catalog.html").read_text(encoding="utf-8")
    html = (template
            .replace("/*__CATALOG_CSS__*/", (STATIC_DIR / "catalog.css").read_text(encoding="utf-8"))
            .replace("/*__CATALOG_JS__*/", (STATIC_DIR / "catalog.js").read_text(encoding="utf-8"))
            .replace("__CATALOG_DATA__", data)
            .replace("__COUNT__", f"{len(items):,}"))
    out = root / "index.html"
    tmp = out.with_suffix(".html.part")
    tmp.write_text(html, encoding="utf-8")
    os.replace(tmp, out)
    return out
