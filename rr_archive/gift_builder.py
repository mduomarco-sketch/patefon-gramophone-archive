"""Parallel, incremental "gift" builder.

Reads COMPLETED items from the live master archive (strictly read-only) and
produces a separate, much smaller, self-contained copy in a brand-new folder:

    patefon_gift/
      russian_records_archive/   compacted media + standalone index.html
      archive_state.db           clean clone (new paths, sizes, sha256; no secrets)
      start_patefon.bat          double-click to open the gift offline
      serve_patefon.py           optional stdlib server on 127.0.0.1:8001

Safety:
  * the source DB is opened with ?mode=ro and only status='COMPLETED' is read;
  * nothing in the source tree is written, moved or deleted;
  * cookies.txt and the _page_cache scrape caches are never copied.

Compaction (CPU-bound work runs in a process pool, cpu_count-2 workers so the
live crawler keeps its cores):
  * audio  .flac/.mp3 -> mono MP3 (libmp3lame -q:a 4), ID3v2.4 tags copied,
           a 600x600 label cover embedded as APIC;
  * images .jpg       -> max 900x900, JPEG q82, progressive, optimised;
  * PDFs   copied when under --gift-pdf-max MB (unless --gift-skip-pdfs).

Incremental: an item already present in the gift DB with its file on disk is
skipped, so the build can be re-run safely while the crawler keeps adding
tracks. Killed mid-run? Re-running continues - a row is written only after its
files are complete.
"""

from __future__ import annotations

import io
import os
import shutil
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from . import config
from .crawler import Crawler, _sha256
from .state import SCHEMA, TRACK_FIELDS, State, now

DEST_ROOT = Path.home() / "Desktop" / "patefon_gift"
PDF_MAX_MB = 15
AUDIO_BRANCHES = ("MUSIC", "RADIO_SPEECH")


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


# ---------------------------------------------------------------------------
# Worker-side compaction (runs in a separate process; no DB access here)
# ---------------------------------------------------------------------------

def _make_cover(label_path: str, size: int = 600, quality: int = 80) -> tuple[bytes, str] | None:
    from PIL import Image
    try:
        with Image.open(label_path) as im:
            im = im.convert("RGB")
            im.thumbnail((size, size), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality, optimize=True)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        return None


def _resize_image(src: str, dst: str, box: int = 900, quality: int = 82) -> None:
    from PIL import Image
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    tmp = dst + ".part"
    with Image.open(src) as im:
        im = im.convert("RGB")
        im.thumbnail((box, box), Image.LANCZOS)
        im.save(tmp, "JPEG", quality=quality, optimize=True, progressive=True)
    os.replace(tmp, dst)


def _transcode_audio(src: str, dst: str, ffmpeg: str) -> None:
    import subprocess
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    tmp = dst + ".part.mp3"
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
           "-ac", "1", "-codec:a", "libmp3lame", "-q:a", "4", "-map_metadata", "-1", tmp]
    subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL,
                   creationflags=getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
    os.replace(tmp, dst)


def compact_one(job: dict) -> dict:
    """Produce one item's gift files. Returns a result dict for the main
    process to record. Never raises - failures come back as {'ok': False}."""
    from .tagger import tag_mp3
    r = {"item_id": job["item_id"], "ok": False, "labels": [], "pdf": None,
         "src_bytes": 0, "dst_bytes": 0}
    try:
        for p in [job["src_main"]] + job["src_labels"] + ([job["src_pdf"]] if job.get("src_pdf") else []):
            if p and os.path.exists(p):
                r["src_bytes"] += os.path.getsize(p)
        # resized label sidecars (shared by audio and image items)
        labels = []
        for s, d in zip(job["src_labels"], job["dst_labels"]):
            if os.path.exists(s):
                _resize_image(s, d)
                labels.append(d)
        if job["kind"] == "audio":
            _transcode_audio(job["src_main"], job["dst_main"], job["ffmpeg"])
            cover = _make_cover(job["cover_src"]) if job.get("cover_src") else None
            tag_mp3(Path(job["dst_main"]), job["meta"], cover)
        else:  # image / print cover
            _resize_image(job["src_main"], job["dst_main"])
        # PDF: copy only (no re-compression without ghostscript)
        if job.get("src_pdf") and not job.get("skip_pdfs"):
            if os.path.getsize(job["src_pdf"]) <= job["pdf_max_bytes"]:
                Path(job["dst_pdf"]).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(job["src_pdf"], job["dst_pdf"])
                r["pdf"] = job["dst_pdf"]
        r["labels"] = labels
        r["local_filepath"] = job["dst_main"]
        r["file_size"] = os.path.getsize(job["dst_main"])
        r["file_sha256"] = _sha256(Path(job["dst_main"]))
        r["dst_bytes"] = r["file_size"] + sum(os.path.getsize(p) for p in labels if os.path.exists(p))
        if r["pdf"]:
            r["dst_bytes"] += os.path.getsize(r["pdf"])
        r["ok"] = True
    except Exception as exc:
        r["error"] = f"{type(exc).__name__}: {exc}"
    return r


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def _rel(path: str, root: Path) -> Path | None:
    try:
        return Path(path).resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None


def _open_source_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _build_job(row, src_arch: Path, dst_arch: Path, ffmpeg: str, skip_pdfs: bool, pdf_max_bytes: int):
    import json
    main = row["local_filepath"]
    rel = _rel(main, src_arch)
    if not main or rel is None or not os.path.exists(main):
        return None
    labels = [p for p in json.loads(row["label_local_paths"] or "[]") if _rel(p, src_arch)]
    dst_labels = [str(dst_arch / _rel(p, src_arch)) for p in labels]
    audio = row["media_type"] in AUDIO_BRANCHES
    dst_main = dst_arch / (rel.with_suffix(".mp3") if audio else rel)
    pdf = row["pdf_local_path"] if "pdf_local_path" in row.keys() else None
    job = {
        "item_id": row["item_id"], "kind": "audio" if audio else "image",
        "src_main": main, "dst_main": str(dst_main),
        "src_labels": labels, "dst_labels": dst_labels,
        "cover_src": labels[0] if labels else None,
        "src_pdf": pdf if pdf and os.path.exists(pdf) else None,
        "dst_pdf": str(dst_arch / _rel(pdf, src_arch)) if pdf and _rel(pdf, src_arch) else None,
        "skip_pdfs": skip_pdfs, "pdf_max_bytes": pdf_max_bytes, "ffmpeg": ffmpeg,
        "meta": Crawler._tag_meta(row) if audio else {},
    }
    return job


def _existing(gift_db: Path) -> set[str]:
    if not gift_db.exists():
        return set()
    c = sqlite3.connect(gift_db)
    try:
        done = set()
        for item_id, path in c.execute("SELECT item_id, local_filepath FROM tracks WHERE status='COMPLETED'"):
            if path and os.path.exists(path):
                done.add(item_id)
        return done
    finally:
        c.close()


def build_gift(source_db: Path = config.DB_PATH, source_archive: Path = config.ARCHIVE_ROOT,
               dest_root: Path = DEST_ROOT, workers: int | None = None, skip_pdfs: bool = False,
               pdf_max_mb: int = PDF_MAX_MB, limit: int | None = None, progress=None) -> dict:
    import json
    workers = workers or max(1, (os.cpu_count() or 2) - 2)
    dst_arch = dest_root / "russian_records_archive"
    dst_arch.mkdir(parents=True, exist_ok=True)
    gift_db = dest_root / "archive_state.db"
    ffmpeg = ffmpeg_exe()
    pdf_max_bytes = pdf_max_mb * 1024 * 1024

    done_ids = _existing(gift_db)
    src = _open_source_ro(source_db)
    rows = [r for r in src.execute("SELECT * FROM tracks WHERE status='COMPLETED' ORDER BY downloaded_at")
            if r["item_id"] not in done_ids]
    src.close()
    if limit:
        rows = rows[:limit]

    jobs = [j for j in (_build_job(r, source_archive, dst_arch, ffmpeg, skip_pdfs, pdf_max_bytes)
                        for r in rows) if j]
    rows_by_id = {r["item_id"]: r for r in rows}
    stats = {"total": len(jobs), "synced": 0, "failed": 0, "skipped_existing": len(done_ids),
             "src_bytes": 0, "dst_bytes": 0, "workers": workers}

    gift = State(gift_db)   # creates the schema in the destination DB
    try:
        if not jobs:
            _finalize(gift, dst_arch, dest_root)
            return stats

        def handle(r):
            stats["src_bytes"] += r["src_bytes"]
            stats["dst_bytes"] += r["dst_bytes"]
            if not r["ok"]:
                stats["failed"] += 1
                return
            _record(gift, rows_by_id[r["item_id"]], r)
            stats["synced"] += 1
            if progress:
                progress(stats)

        if workers == 1:                         # serial (also the test path)
            for j in jobs:
                handle(compact_one(j))
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                for fut in as_completed({pool.submit(compact_one, j): 1 for j in jobs}):
                    handle(fut.result())
        _finalize(gift, dst_arch, dest_root)
    finally:
        gift.close()
    return stats


def _record(gift: State, row, r: dict) -> None:
    import json
    cols = {k: row[k] for k in TRACK_FIELDS}
    cols["local_filepath"] = r["local_filepath"]
    cols["file_sha256"] = r["file_sha256"]
    cols["file_size"] = r["file_size"]
    extra = {"label_local_paths": json.dumps(r["labels"], ensure_ascii=False),
             "pdf_local_path": r["pdf"], "status": "COMPLETED",
             "downloaded_at": row["downloaded_at"] or now(), "updated_at": now()}
    allcols = {"item_id": row["item_id"], **cols, **extra}
    names = ", ".join(allcols)
    marks = ", ".join("?" for _ in allcols)
    with gift.conn:
        gift.conn.execute(f"INSERT OR REPLACE INTO tracks({names}) VALUES ({marks})", tuple(allcols.values()))


def _finalize(gift: State, dst_arch: Path, dest_root: Path) -> None:
    """Regenerate the standalone index.html and write the run-anywhere wrappers."""
    from .export import export_catalog
    export_catalog(gift, dst_arch, rebuild_indexes=False)   # reuses source indexes read-only
    _write_launchers(dest_root)


LAUNCHER_BAT = """@echo off
rem Patefon - a gift. Double-click to open offline in your browser.
cd /d "%~dp0"
start "" "russian_records_archive\\index.html"
"""

SERVE_PY = '''"""Optional: serve the gift at http://127.0.0.1:8001 (needs only Python).
Double-clicking russian_records_archive\\index.html works without this."""
import http.server, socketserver, webbrowser, functools
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "russian_records_archive"
PORT = 8001
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
print(f"Patefon gift: http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
webbrowser.open(f"http://127.0.0.1:{PORT}/index.html")
with socketserver.TCPServer(("127.0.0.1", PORT), handler) as httpd:
    httpd.serve_forever()
'''


def _write_launchers(dest_root: Path) -> None:
    (dest_root / "start_patefon.bat").write_text(LAUNCHER_BAT, encoding="utf-8")
    (dest_root / "serve_patefon.py").write_text(SERVE_PY, encoding="utf-8")
    # bundled offline fonts (the HTML also embeds them, but ship the files too)
    src_fonts = config.PROJECT_ROOT / "rr_archive" / "web" / "static" / "fonts"
    if src_fonts.exists():
        dst_fonts = dest_root / "russian_records_archive" / "_assets" / "fonts"
        dst_fonts.mkdir(parents=True, exist_ok=True)
        for f in src_fonts.glob("*.woff2"):
            shutil.copy2(f, dst_fonts / f.name)
    # never leak secrets
    for secret in ("cookies.txt",):
        leaked = dest_root / secret
        if leaked.exists():
            leaked.unlink()


def human_gb(n: int) -> float:
    return round(n / 1024 ** 3, 2)
