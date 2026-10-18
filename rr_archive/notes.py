"""The archive's own texts for each record, extracted from cached pages.

russian-records.com record pages carry three kinds of text (surveyed over 432
cached pages; none has a lyrics row - "Lyrics By" only names the lyricist):
  label    the text printed on the physical label (the unlabelled first row of
           the info table), often multilingual, in the original spelling
  liner    the "Additional information" row: liner-note prose or verse
  work     the "Larger Composition Name" row: the opera/suite a piece is from

build_index() parses _page_cache/ with the current parser - no network - and
writes rr_archive/web/static/notes_index.json ({item_id: {...}}), which the
dashboard loads as a static file and the gift export embeds. The database is
only read. It is incremental: a sidecar notes_index.meta.json remembers each
cached page's mtime, so only new or re-fetched pages are parsed again (bump
NOTES_VERSION whenever extract() changes, or pass full=True).
"""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

from . import config

INDEX_PATH = Path(__file__).resolve().parent / "web" / "static" / "notes_index.json"
NOTES_VERSION = 1   # bump when extract() output changes: forces one full rebuild


def extract(html: str, url: str) -> dict:
    from .parser import parse_details

    item = parse_details(html, url)
    out = {}
    label = item.description.strip() if item.description else ""
    if label:
        out["label"] = label
    liner = (item.fields.get("notes") or "").strip()
    if liner:
        out["liner"] = liner
    work = (item.fields.get("work") or "").strip()
    if work:
        out["work"] = work
    return out


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def build_index(conn, cache_dir: Path = config.PAGE_CACHE_DIR, index_path: Path = INDEX_PATH,
                *, full: bool = False) -> dict:
    from .crawler import cache_path
    from .net import decode_html

    meta_path = index_path.with_name(index_path.stem + ".meta.json")
    old_index, seen = {}, {}
    if not full:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("version") == NOTES_VERSION:
                seen = meta.get("mtimes", {})
                old_index = load_index(index_path)
        except (OSError, ValueError):
            pass

    rows = conn.execute("SELECT item_id, source_url FROM tracks WHERE status='COMPLETED'").fetchall()
    index, mtimes, missing, parsed = {}, {}, 0, 0
    for item_id, url in rows:
        cached = cache_path(cache_dir, url)
        try:
            mtime = cached.stat().st_mtime_ns
        except OSError:
            missing += 1
            continue
        mtimes[item_id] = mtime
        if seen.get(item_id) == mtime:               # page unchanged since the last build
            if item_id in old_index:
                index[item_id] = old_index[item_id]
            continue
        parsed += 1
        try:
            entry = extract(decode_html(gzip.decompress(cached.read_bytes())), url)
        except Exception:
            entry = None                             # remembered via mtimes: not retried
        if entry:
            index[item_id] = entry
    _write_json(index_path, index)
    _write_json(meta_path, {"version": NOTES_VERSION, "mtimes": mtimes})
    return {"records": len(rows), "parsed": parsed, "with_text": len(index), "no_cached_page": missing,
            "label": sum(1 for e in index.values() if "label" in e),
            "liner": sum(1 for e in index.values() if "liner" in e),
            "work": sum(1 for e in index.values() if "work" in e)}


def load_index(index_path: Path = INDEX_PATH) -> dict:
    try:
        return json.loads(Path(index_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


if __name__ == "__main__":
    import sqlite3

    c = sqlite3.connect(f"file:{config.DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)
    print(build_index(c))
    c.close()
