"""FastAPI dashboard: session input, job control, recon/test reports,
archive explorer with re-classification, static catalog export."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import config
from ..export import clean_seed, export_catalog, item_dict, load_user_playlists, notes_text, save_user_playlists
from ..media import InvalidMedia, sniff_audio, sniff_image, sniff_pdf
from ..reclassify import LANGUAGES, ReclassifyError, reclassify
from ..state import State
from ..tagger import verify_flac, verify_mp3
from .jobs import JobManager

STATIC = Path(__file__).resolve().parent / "static"
CSRF_HEADER = "x-rr-dashboard"


# Request bodies live at module level so FastAPI can resolve the
# postponed annotations (from __future__ import annotations).
class SessionIn(BaseModel):
    sessionid: str = ""
    userid: str = ""
    cookie_header: str = ""
    user_agent: str = ""


class ExportIn(BaseModel):
    playlists: list[dict] = []
    liked: list[str] = []


class StartIn(BaseModel):
    mode: str
    retry_failed: bool = False
    queue: str = "audio-first"   # audio-first | audio-only | all (full crawl only)
    limit: int = 0               # stop after N completed items; 0 = unlimited
    workers: int = 1             # parallel downloads (full crawl only)


class ReclassifyIn(BaseModel):
    language: str
    category: str



def create_app(db_path: Path = config.DB_PATH, archive_root: Path = config.ARCHIVE_ROOT,
               cookie_file: Path = config.COOKIE_FILE) -> FastAPI:
    app = FastAPI(title="Russian Records Archive", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    jobs = JobManager()
    archive_root = Path(archive_root)
    State(db_path).close()  # ensure schema exists

    def db() -> sqlite3.Connection:
        conn = sqlite3.connect(db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        # Cross-site pages cannot set custom headers without a CORS preflight,
        # which this app never approves.
        if request.method not in ("GET", "HEAD") and request.headers.get(CSRF_HEADER) != "1":
            return JSONResponse({"detail": "missing dashboard header"}, status_code=403)
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "dashboard.html")

    # -- session ---------------------------------------------------------------
    def _settings() -> dict:
        try:
            return json.loads((config.CONTROL_DIR / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _read_cookies() -> list[tuple[str, str]]:
        if not cookie_file.exists():
            return []
        out = []
        for line in cookie_file.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) >= 7 and not line.startswith("# "):
                out.append((parts[5], parts[6]))
        return out

    def _mask(v: str) -> str:
        return (v[:4] + "…" + f" ({len(v)} chars)") if len(v) > 6 else "•" * len(v)

    @app.get("/api/session")
    def get_session():
        cookies = _read_cookies()
        return {
            "exists": bool(cookies),
            "cookies": [{"name": n, "masked": _mask(v)} for n, v in cookies],
            "updated_at": cookie_file.stat().st_mtime if cookie_file.exists() else None,
            "user_agent": _settings().get("user_agent", ""),
            "cookie_name_hint": config.SESSION_COOKIE_NAME,
        }

    @app.post("/api/session")
    def save_session(body: SessionIn):
        pairs: dict[str, str] = dict(_read_cookies()) if not body.cookie_header else {}
        header = re.sub(r"^\s*cookie\s*:\s*", "", body.cookie_header.strip(), flags=re.I)
        for chunk in header.split(";"):
            if "=" in chunk:
                k, v = chunk.split("=", 1)
                if k.strip():
                    pairs[k.strip()] = v.strip()
        if body.sessionid.strip():
            pairs[config.SESSION_COOKIE_NAME] = body.sessionid.strip()
        if body.userid.strip():
            pairs["4images_userid"] = body.userid.strip()
        if not pairs:
            raise HTTPException(400, "paste a sessionid or a Cookie header")
        for k, v in pairs.items():
            if any(c in k + v for c in "\t\r\n"):
                raise HTTPException(400, f"invalid characters in cookie {k}")
        lines = ["# Netscape HTTP Cookie File", "# written by the local dashboard"]
        lines += [f"{config.CANONICAL_HOST}\tFALSE\t/\tTRUE\t0\t{k}\t{v}" for k, v in pairs.items()]
        tmp = cookie_file.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(cookie_file)
        settings = _settings()
        if body.user_agent.strip():
            settings["user_agent"] = body.user_agent.strip()
        config.CONTROL_DIR.mkdir(exist_ok=True)
        (config.CONTROL_DIR / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        return get_session()

    # -- jobs --------------------------------------------------------------------
    def _progress() -> dict:
        with db() as conn:
            items = {r["status"]: r["n"] for r in conn.execute(
                "SELECT status, COUNT(*) n FROM tracks GROUP BY status")}
            pages = {r["status"]: r["n"] for r in conn.execute(
                "SELECT status, COUNT(*) n FROM pages GROUP BY status")}
            recent = [dict(r) for r in conn.execute(
                "SELECT item_id, title, language, language_detection_method AS method, downloaded_at "
                "FROM tracks WHERE status='COMPLETED' ORDER BY downloaded_at DESC LIMIT 8")]
            # Running totals; the dashboard turns successive readings into rates.
            bytes_total = conn.execute(
                "SELECT COALESCE(SUM(file_size), 0) FROM tracks WHERE status='COMPLETED'").fetchone()[0]
            pages_done = conn.execute(
                "SELECT COUNT(*) FROM pages WHERE status='DONE'").fetchone()[0]
        return {"items": items, "pages": pages, "recent": recent,
                "bytes_total": bytes_total, "pages_done": pages_done,
                "completed_total": items.get("COMPLETED", 0)}

    @app.get("/api/job")
    def job_status():
        return {"job": jobs.status(), "progress": _progress()}

    @app.post("/api/job/start")
    def job_start(body: StartIn):
        try:
            jobs.start(body.mode, cookie_file, _settings().get("user_agent") or None, body.retry_failed,
                       queue=body.queue, limit=body.limit, workers=body.workers)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(409, str(exc))
        return job_status()

    @app.post("/api/job/{action}")
    def job_action(action: str):
        fn = {"pause": jobs.pause, "resume": jobs.resume, "stop": jobs.stop, "kill": jobs.kill}.get(action)
        if fn is None:
            raise HTTPException(404, "unknown action")
        try:
            fn()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))
        return job_status()

    @app.get("/api/recon")
    def recon_report():
        f = config.RECON_DIR / "report.json"
        if not f.exists():
            raise HTTPException(404, "no recon report yet")
        return json.loads(f.read_text(encoding="utf-8"))

    def _inspect(row) -> dict:
        path = Path(row["local_filepath"] or "")
        info: dict = {"exists": path.exists()}
        if not path.exists():
            return info
        try:
            if row["media_type"] in ("MUSIC", "RADIO_SPEECH"):
                info["container"] = sniff_audio(path)
                if path.suffix.lower() == ".mp3":
                    from mutagen.id3 import ID3
                    info.update(verify_mp3(path))
                    tags = ID3(path)
                    info["tags"] = {k: str(tags[k])[:200] for k in
                                    ("TIT2", "TPE1", "TCOM", "TALB", "TDRC", "TLAN", "TPUB") if k in tags}
                    info["id3_version"] = ".".join(map(str, tags.version[:2]))
                elif path.suffix.lower() == ".flac":
                    from mutagen.flac import FLAC
                    info.update(verify_flac(path))
                    vc = FLAC(path)
                    # Report Vorbis comments under their ID3 equivalents for one UI.
                    for k, v in (("TIT2", "TITLE"), ("TPE1", "ARTIST"), ("TCOM", "COMPOSER"),
                                 ("TALB", "ALBUM"), ("TDRC", "DATE"), ("TLAN", "LANGUAGE"),
                                 ("TPUB", "ORGANIZATION")):
                        if vc.get(v):
                            info.setdefault("tags", {})[k] = vc[v][0][:200]
                    info["id3_version"] = "n/a (FLAC Vorbis comments)"
            else:
                info["container"] = sniff_image(path)[0]
                if row["pdf_local_path"]:
                    pdf_path = Path(row["pdf_local_path"])
                    sniff_pdf(pdf_path)
                    info["pdf"] = f"valid, {pdf_path.stat().st_size / 1e6:.1f} MB"
            info["valid"] = True
        except (InvalidMedia, ValueError, Exception) as exc:  # report, don't crash the panel
            info["valid"] = False
            info["error"] = str(exc)
        return info

    @app.get("/api/job/items")
    def job_items():
        st = jobs.status()
        since = st["started_at"] if st else "0"
        with db() as conn:
            rows = conn.execute("SELECT * FROM tracks WHERE updated_at >= ? ORDER BY updated_at",
                                (since,)).fetchall()
        return [{**item_dict(r, archive_root), "inspect": _inspect(r) if r["status"] == "COMPLETED" else None}
                for r in rows]

    # -- archive -------------------------------------------------------------------
    @app.get("/api/catalog")
    def catalog():
        with db() as conn:
            rows = conn.execute("SELECT * FROM tracks").fetchall()
        return [item_dict(r, archive_root) for r in rows]

    def _row(item_id: str):
        with db() as conn:
            row = conn.execute("SELECT * FROM tracks WHERE item_id=?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "unknown item")
        return row

    @app.get("/api/items/{item_id}")
    def item(item_id: str):
        row = _row(item_id)
        return {**item_dict(row, archive_root), "description": notes_text(row)}

    @app.get("/api/items/{item_id}/inspect")
    def inspect(item_id: str):
        return _inspect(_row(item_id))

    @app.post("/api/items/{item_id}/reclassify")
    def do_reclassify(item_id: str, body: ReclassifyIn):
        state = State(db_path)
        try:
            row = reclassify(state, item_id, body.language, body.category, archive_root)
        except ReclassifyError as exc:
            raise HTTPException(400, str(exc))
        finally:
            state.close()
        return item_dict(row, archive_root)

    @app.get("/api/options")
    def options():
        return {"languages": LANGUAGES}

    @app.post("/api/export")
    def export(body: ExportIn | None = None):
        """Write the gift index.html. When the dashboard sends its playlists and
        likes, they are saved to user_playlists.json and baked into the gift as
        her starting library."""
        seed = clean_seed(body.model_dump()) if body else None
        if seed:
            save_user_playlists(seed)
        state = State(db_path)
        try:
            out = export_catalog(state, archive_root)  # reads user_playlists.json
        finally:
            state.close()
        saved = load_user_playlists()
        return {"path": str(out), "seed_supported": True,
                "playlists_embedded": len(saved["playlists"]) if saved else 0,
                "liked_embedded": len(saved["liked"]) if saved else 0}

    @app.post("/api/build-gift")
    def build_gift_now():
        """Sync new COMPLETED tracks into ~/Desktop/patefon_gift as a detached,
        incremental subprocess (4 workers, leaving the crawler its cores). It
        reads the archive read-only, so it is safe to run during a crawl."""
        import subprocess
        import sys as _sys
        from ..gift_builder import DEST_ROOT
        args = [_sys.executable, "-u", str(config.PROJECT_ROOT / "scraper.py"),
                "--build-gift", "--gift-workers", "4"]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        log = (config.CONTROL_DIR); log.mkdir(exist_ok=True)
        out = open(log / "gift_build.log", "ab")
        subprocess.Popen(args, cwd=config.PROJECT_ROOT, env=env, stdout=out, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, creationflags=flags)
        return {"started": True, "dest": str(DEST_ROOT), "log": str(log / "gift_build.log")}

    # -- media ---------------------------------------------------------------------
    def _safe(path_str: str | None) -> Path:
        if not path_str:
            raise HTTPException(404, "no file")
        p = Path(path_str).resolve()
        if archive_root.resolve() not in p.parents or not p.is_file():
            raise HTTPException(404, "file not available")
        return p

    @app.get("/media/{item_id}")
    def media(item_id: str):
        return FileResponse(_safe(_row(item_id)["local_filepath"]))

    @app.get("/media/{item_id}/pdf")
    def pdf(item_id: str):
        return FileResponse(_safe(_row(item_id)["pdf_local_path"]), media_type="application/pdf")

    @app.get("/media/{item_id}/label/{n}")
    def label(item_id: str, n: int):
        labels = json.loads(_row(item_id)["label_local_paths"] or "[]")
        if not 0 <= n < len(labels):
            raise HTTPException(404, "no such label")
        return FileResponse(_safe(labels[n]))

    return app
