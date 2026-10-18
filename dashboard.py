"""Local dashboard: python dashboard.py  ->  http://127.0.0.1:8000

Bound to 127.0.0.1 only; your session cookies never leave this machine
except in requests to russian-records.com made by scraper.py."""

import argparse
import sys

import uvicorn

from rr_archive.web.app import create_app


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    print(f"Dashboard: http://127.0.0.1:{args.port}")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
