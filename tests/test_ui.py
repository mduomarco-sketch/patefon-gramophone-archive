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


def test_hardware_layer_is_wired_up():
    """Icon rail, hardware tray, phone layouts, locate, dial, tonearm, magic eye."""
    js = (STATIC / "catalog.js").read_text(encoding="utf-8")
    css = (STATIC / "catalog.css").read_text(encoding="utf-8")
    for s in ("function toggleRail", 'e.key === "["', 'store.set("rail"', "(max-width: 1149.98px)", "(max-width: 767.98px)",
              "function locateCurrent", 'k === "l"', "pf-located", "function tonearm", "function updateArms",
              "function dialStep", "getByteTimeDomainData", "function toggleTray", "pf-tray-only", "pf-rail-tip",
              "ПАТЕФОН · ГОСТ 5289-56"):
        assert s in js, s
    # Web Audio would silence file:// playback (CORS), so it must stay http(s)-only
    assert "!/^https?:$/.test(location.protocol)) return;" in js
    for key in ("railCollapse", "railExpand", "locate", "nothingPlaying", "tray"):
        assert js.count(f'{key}: "') == 3, key                  # EN, RU and KZ dictionary entries
    for s in (".pf-app.rail { --sidebar-w: 68px; }", "cubic-bezier(0.22, 1, 0.36, 1)", "@media (max-width: 1040px)",
              "@media (max-width: 767.98px)", "@media (max-width: 480px)", ".pf-drawer.open { transform: none; }",
              ".pf-arm", ".pf-eye-shine", "pf-ember", ".pf-plate", "@keyframes pf-locate", "translateY(1px)"):
        assert s in css, s
    assert css.count("{") == css.count("}")


def test_tonearm_geometry_tracks_the_groove():
    """The JS solves the pivot-centre-stylus triangle; check the same maths lands
    the stylus on the lead-in groove, moves it steadily inward to the run-out
    groove, and parks it off the record."""
    import math
    js = (STATIC / "catalog.js").read_text(encoding="utf-8")
    m = re.search(r"const ARM = \{ px: ([\d.]+), py: ([\d.]+), len: ([\d.]+), rest: ([\d.]+), rIn: ([\d.]+), rOut: ([\d.]+) \}", js)
    px, py, length, rest, r_in, r_out = map(float, m.groups())
    d, base = math.hypot(50 - px, 50 - py), math.atan2(50 - py, 50 - px)

    def angle(p):                                             # mirrors armAngle() in catalog.js
        r = r_in + (r_out - r_in) * p
        return math.degrees(base - math.acos((length ** 2 + d ** 2 - r ** 2) / (2 * length * d)))

    def stylus(theta):
        t = math.radians(theta)
        return px + length * math.cos(t), py + length * math.sin(t)

    angles = [angle(i / 10) for i in range(11)]
    for i, theta in enumerate(angles):
        x, y = stylus(theta)
        assert abs(math.hypot(x - 50, y - 50) - (r_in + (r_out - r_in) * i / 10)) < 1e-9
    assert all(b > a for a, b in zip(angles, angles[1:]))   # steadily inward
    assert r_in < 50 and r_out > 18                          # inside the rim, outside the label hole
    x, y = stylus(rest)
    assert math.hypot(x - 50, y - 50) > 55                   # the rest post is beside the record
