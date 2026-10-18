"""The Patefon UI: dashboard shell, admin drawer and the gift export.

These tests never touch the real archive, database or _control/ (see
conftest.isolated_control_dir), so they are safe to run during a live crawl."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rr_archive.export import STATIC_DIR, export_catalog
from rr_archive.state import State
from rr_archive.web.app import create_app

STATIC = Path(STATIC_DIR)


@pytest.fixture
def client(built_archive, tmp_path):
    db, root = built_archive
    return TestClient(create_app(db, root, tmp_path / "cookies.txt"), base_url="http://127.0.0.1")


def test_dashboard_serves_app_shell_and_admin_drawer(client):
    html = client.get("/").text
    assert 'id="patefon"' in html and 'id="admin-drawer"' in html
    assert "/static/catalog.js" in html and "/static/dashboard.js" in html
    for asset in ("catalog.js", "catalog.css", "dashboard.js", "dashboard.css"):
        r = client.get(f"/static/{asset}")
        assert r.status_code == 200 and len(r.text) > 1000, asset


def test_admin_controls_all_live_in_the_drawer():
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    drawer = html.split('id="admin-drawer"', 1)[1]
    for el_id in sorted(set(re.findall(r'\$\("#([\w-]+)"\)', js))):
        assert f'id="{el_id}"' in html, f"dashboard.js uses #{el_id} which is missing"
    for crawler_control in ("session-form", "btn-crawl", "btn-kill", "audio-only", "batch-limit",
                            "captcha-banner", "recon", "reclassify"):
        assert f'id="{crawler_control}"' in drawer, crawler_control


def test_frontend_uses_only_existing_api_routes(client):
    """The running dashboard is never restarted during a crawl, so the UI may
    only call routes that already exist in the server process."""
    routes = {r.path for r in client.app.routes}
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8") + (STATIC / "catalog.js").read_text(encoding="utf-8")
    used = set(re.findall(r'["`](/(?:api|media)/[^"`$?]*)', js))
    for path in used:
        base = path.rstrip("/")
        assert any(base == r or base.startswith(r.split("{")[0].rstrip("/")) for r in routes), path


def test_gift_export_is_the_player_without_admin(built_archive):
    db, root = built_archive
    state = State(db)
    out = export_catalog(state, root)
    html = out.read_text(encoding="utf-8")
    state.close()
    assert 'id="patefon"' in html and "RRApp.mount" in html
    assert "admin-drawer" not in html and "/api/" not in html and "X-RR-Dashboard" not in html
    data = json.loads(html.split('<script id="rr-data" type="application/json">', 1)[1].split("</script>", 1)[0])
    playable = [i for i in data["items"] if i["branch"] in ("MUSIC", "RADIO_SPEECH")]
    assert playable and all((root / i["file"]).exists() for i in data["items"])
    assert any(i.get("pdf") for i in data["items"])           # the PDF reader has something to open
    for key in ("Kazakh & Central Asian spotlight", "Қазақстан және Орталық Азия", "Казахстан и Центральная Азия"):
        assert key in html                                    # EN / KZ / RU interface strings


def test_template_contract_unchanged():
    """export.py in an already-running dashboard fills these four placeholders,
    so the template must keep them (and the assets must not contain them)."""
    tpl = (STATIC / "static_catalog.html").read_text(encoding="utf-8")
    for ph in ("/*__CATALOG_CSS__*/", "/*__CATALOG_JS__*/", "__CATALOG_DATA__", "__COUNT__"):
        assert ph in tpl
    for asset in ("catalog.js", "catalog.css"):
        text = (STATIC / asset).read_text(encoding="utf-8")
        assert "__CATALOG_DATA__" not in text and "__COUNT__" not in text


def test_design_tokens_locked():
    css = (STATIC / "catalog.css").read_text(encoding="utf-8")
    # USSR edition palette
    for token in ("--canvas: #0F0E0E", "--velvet: #1A1211", "--surface-2: #241A18", "--text: #F0E6D2",
                  "--text-2: #C2B29F", "--red: #CC2929", "--brass: #E5B84C", "--steel: #4A6B82"):
        assert token in css, token
    for rule in (".pf-eye", ".pf-telegram", ".pf-stamp", ".pf-hero-kz", "--font-poster", "--font-type"):
        assert rule in css, rule
    assert "prefers-reduced-motion" in css and "pointer-events: none; z-index: 100; opacity: 0.035" in css
