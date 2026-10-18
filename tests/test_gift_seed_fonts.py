"""Curated playlists/likes baked into the gift, and fully offline fonts."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import rr_archive.export as export_mod
from rr_archive.export import STATIC_DIR, clean_seed, export_catalog
from rr_archive.state import State
from rr_archive.web.app import create_app

STATIC = Path(STATIC_DIR)
H = {"X-RR-Dashboard": "1"}


@pytest.fixture(autouse=True)
def isolated_user_playlists(tmp_path, monkeypatch):
    """Never touch the real user_playlists.json."""
    p = tmp_path / "user_playlists.json"
    monkeypatch.setattr(export_mod, "USER_PLAYLISTS", p)
    monkeypatch.setattr(export_mod.save_user_playlists, "__defaults__", (p,))
    monkeypatch.setattr(export_mod.load_user_playlists, "__defaults__", (p,))
    return p


def gift_data(path: Path) -> dict:
    html = path.read_text(encoding="utf-8")
    return json.loads(html.split('<script id="rr-data" type="application/json">', 1)[1].split("</script>", 1)[0])


def test_clean_seed_validates():
    assert clean_seed(None) is None and clean_seed({"playlists": [], "liked": []}) is None
    s = clean_seed({"playlists": [{"id": "pl-1", "name": " For you ", "tracks": ["101", "101", 102]},
                                  {"bogus": True}], "liked": ["103", {"x": 1}]})
    assert s == {"playlists": [{"id": "pl-1", "name": "For you", "tracks": ["101", "102"], "created": ""}],
                 "liked": ["103"]}


def test_seed_embedded_and_filtered_to_exported_items(built_archive):
    db, root = built_archive
    state = State(db)
    out = export_catalog(state, root, seed={"playlists": [{"id": "pl-gift", "name": "Для тебя",
                                                           "tracks": ["101", "999999", "103"]}],
                                            "liked": ["102", "888888"]})
    state.close()
    seed = gift_data(out)["seed"]
    assert seed["playlists"] == [{"id": "pl-gift", "name": "Для тебя", "tracks": ["101", "103"], "created": ""}]
    assert seed["liked"] == ["102"] and len(seed["version"]) == 16


def test_no_seed_means_no_seed_key(built_archive):
    db, root = built_archive
    state = State(db)
    out = export_catalog(state, root)
    state.close()
    assert "seed" not in gift_data(out)


def test_dashboard_export_bakes_its_library(built_archive, tmp_path, isolated_user_playlists):
    db, root = built_archive
    client = TestClient(create_app(db, root, tmp_path / "cookies.txt"), base_url="http://127.0.0.1")
    r = client.post("/api/export", headers=H, json={
        "playlists": [{"id": "pl-a", "name": "Evening", "tracks": ["101", "102"]}], "liked": ["104"]})
    assert r.status_code == 200
    body = r.json()
    assert body["seed_supported"] and body["playlists_embedded"] == 1 and body["liked_embedded"] == 1
    assert json.loads(isolated_user_playlists.read_text(encoding="utf-8"))["playlists"][0]["name"] == "Evening"
    assert gift_data(root / "index.html")["seed"]["liked"] == ["104"]
    # An export without a body keeps the saved curation.
    client.post("/api/export", headers=H)
    assert gift_data(root / "index.html")["seed"]["playlists"][0]["id"] == "pl-a"


def test_cli_export_with_backup_file(built_archive, tmp_path, monkeypatch, isolated_user_playlists):
    import scraper
    db, root = built_archive
    backup = tmp_path / "patefon-library.json"
    backup.write_text(json.dumps({"app": "patefon", "version": 1, "liked": ["101"],
                                  "playlists": [{"id": "pl-z", "name": "Road trip", "tracks": ["103"]}]}),
                      encoding="utf-8")
    monkeypatch.setattr(scraper.config, "LOG_PATH", tmp_path / "scraper.log")
    rc = scraper.main(["--export-html", "--playlists", str(backup), "--db", str(db), "--archive-root", str(root)])
    assert rc == 0
    seed = gift_data(root / "index.html")["seed"]
    assert seed["playlists"][0]["name"] == "Road trip" and seed["liked"] == ["101"]
    assert isolated_user_playlists.exists()


def test_gift_template_embeds_all_fonts_offline():
    tpl = (STATIC / "static_catalog.html").read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in tpl and "fonts.gstatic.com" not in tpl
    families = set(re.findall(r"font-family: '([^']+)'", tpl))
    assert {"Oswald", "PT Serif", "PT Mono", "Inter", "JetBrains Mono"} <= families
    faces = re.findall(r"@font-face \{[^}]*\}", tpl)
    assert len(faces) >= 12 and all("data:font/woff2;base64," in f for f in faces)
    # cyrillic-ext covers the Kazakh letters (U+0492 Ғ, U+049A Қ, U+04A2 Ң, U+04AE Ү, U+04B0 Ұ, U+04BA Һ, U+04D8 Ә, U+04E8 Ө)
    assert "/* cyrillic-ext */" in tpl


def test_dashboard_uses_local_fonts(built_archive, tmp_path):
    db, root = built_archive
    client = TestClient(create_app(db, root, tmp_path / "cookies.txt"), base_url="http://127.0.0.1")
    html = client.get("/").text
    assert "fonts.googleapis.com" not in html and "/static/fonts/fonts.css" in html
    css = client.get("/static/fonts/fonts.css").text
    files = re.findall(r"url\(/static/fonts/([^)]+\.woff2)\)", css)
    assert files
    r = client.get(f"/static/fonts/{files[0]}")
    assert r.status_code == 200 and r.content[:4] == b"wOF2"
