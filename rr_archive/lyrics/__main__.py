"""python -m rr_archive.lyrics wikisource [--limit N] | index | status

Every command only reads archive_state.db (read-only connection), so it is
safe to run while a crawl is writing to it."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys

from .. import config
from . import INDEX_PATH, build_index, load_meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rr_archive.lyrics", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wikisource", help="match vocal records against public-domain texts on ru.wikisource.org")
    w.add_argument("--limit", type=int, help="check at most N records this run")
    sub.add_parser("index", help="rebuild the lyrics index the UI reads")
    sub.add_parser("status", help="counts by source")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    conn = sqlite3.connect(f"file:{config.DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        if args.cmd == "wikisource":
            from .wikisource import run
            print("wikisource:", run(conn, limit=args.limit))
            print("index:", build_index(conn))
        elif args.cmd == "index":
            print("index:", build_index(conn))
        else:
            meta = load_meta()
            by = {}
            for m in meta.values():
                by[m["source"]] = by.get(m["source"], 0) + 1
            idx = json.loads(INDEX_PATH.read_text(encoding="utf-8")) if INDEX_PATH.exists() else {}
            print(f"stored: {by}; in UI index: {len(idx)}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
