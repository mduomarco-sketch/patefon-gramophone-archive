import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mutagen.id3 import ID3

import rr_archive.reclassify as rc
from rr_archive.crawler import Crawler, category_from_path
from rr_archive.export import export_catalog
from rr_archive.net import load_cookies
from rr_archive.reclassify import ReclassifyError, reclassify
from rr_archive.state import State
from rr_archive.web.app import create_app

H = {"X-RR-Dashboard": "1"}


def test_category_modes():
    assert category_from_path(["Песни", "Романсы", "Тестов"]) == "Песни - Тестов"
    assert category_from_path(["Песни", "Тестов"], "leaf") == "Тестов"
    assert category_from_path(["Песни", "Тестов"], "top") == "Песни"
    assert category_from_path(["Газеты"]) == "Газеты"
    assert category_from_path([]) == ""


def test_stop_file_halts_before_any_request(tmp_path, make_client):
    state = State(tmp_path / "s.db")
    ctl = tmp_path / "ctl"
    ctl.mkdir()
    (ctl / "STOP").touch()
    crawler = Crawler(make_client(), state, tmp_path / "a", cache_dir=None, control_dir=ctl)
    crawler.seed()
    crawler.run()
    assert crawler.pages_fetched == 0 and crawler.client.request_log == []
    state.close()


def test_reclassify_moves_and_retags(built_archive):
    db, root = built_archive
    state = State(db)
    before = state.get_track("101")
    old_main = Path(before["local_filepath"])
    old_label = Path(json.loads(before["label_local_paths"])[0])
    row = reclassify(state, "101", "Ukrainian", "Тест - Новая", root)
    new_main = Path(row["local_filepath"])
    assert row["language_detection_method"] == "MANUAL"
    assert new_main.relative_to(root).parts[:3] == ("Ukrainian", "Тест - Новая", "Тестовый Певец")
    assert new_main.exists() and not old_main.exists() and not old_label.exists()
    new_label = Path(json.loads(row["label_local_paths"])[0])
    assert new_label.exists() and new_label.name == new_main.stem + " (label).jpg"
    tags = ID3(new_main)
    assert str(tags["TLAN"]) == "ukr" and str(tags["TALB"]) == "Тест - Новая"
    assert str(tags["TXXX:LANGUAGE_DETECTION_METHOD"]) == "MANUAL"
    assert tags.getall("APIC")  # other frames untouched
    assert len(row["file_sha256"]) == 64 and row["file_sha256"] != before["file_sha256"]
    assert not old_main.parent.exists()  # emptied folders pruned
    # A later re-crawl/retry cannot undo the manual choice.
    state.conn.execute("UPDATE tracks SET status='FAILED' WHERE item_id='101'")
    state.upsert_track("101", {"source_url": "x", "language": "Russian", "category": "Z",
                               "language_detection_method": "SCRIPT_NLP"})
    r = state.get_track("101")
    assert (r["language"], r["category"], r["language_detection_method"]) == \
        ("Ukrainian", "Тест - Новая", "MANUAL")
    state.close()


def test_reclassify_rejects_and_rolls_back(built_archive, monkeypatch):
    db, root = built_archive
    state = State(db)
    with pytest.raises(ReclassifyError):
        reclassify(state, "101", "Klingon", "x", root)
    before = state.get_track("102")

    def boom(_p):
        raise OSError("disk full")
    monkeypatch.setattr(rc, "_sha256", boom)
    with pytest.raises(ReclassifyError):
        reclassify(state, "102", "Polish", "Elsewhere", root)
    after = state.get_track("102")
    assert after["local_filepath"] == before["local_filepath"]
    assert Path(before["local_filepath"]).exists()
    assert str(ID3(before["local_filepath"])["TLAN"]) == "ukr"  # tag restored
    state.close()


def test_static_export(built_archive):
    db, root = built_archive
    state = State(db)
    state.conn.execute("UPDATE tracks SET title='</script><b>x' WHERE item_id='103'")
    state.conn.commit()
    out = export_catalog(state, root)
    html = out.read_text(encoding="utf-8")
    data = html.split('<script id="rr-data" type="application/json">', 1)[1].split("</script>", 1)[0]
    items = {i["id"]: i for i in json.loads(data)["items"]}
    assert len(items) == 6
    assert items["101"]["file"] == ("Russian/Русские пластинки - Русские романсы/Тестовый Певец/"
                                    "1912 - Evening bells (Вечерний звон), romance [101].mp3")
    assert (root / items["101"]["file"]).exists()
    assert items["101"]["labels"] and "Вечерній звонъ." in items["101"]["description"]
    assert "Hits" not in items["101"]["description"]
    assert items["103"]["title"] == "</script><b>x"  # escaped, did not break out of the JSON block
    # Self-contained: no external scripts; the only links are optional web fonts
    # (Georgia / Segoe UI take over offline).
    assert "<script src" not in html
    import re
    assert all("fonts.googleapis.com" in l or "fonts.gstatic.com" in l
               for l in re.findall(r"<link[^>]+>", html))
    state.close()


@pytest.fixture
def client(built_archive, tmp_path):
    db, root = built_archive
    return TestClient(create_app(db, root, tmp_path / "cookies.txt"), base_url="http://127.0.0.1")


def test_api_guards(client):
    assert client.post("/api/session", json={"sessionid": "abc"}).status_code == 403  # no CSRF header
    rebinding = TestClient(client.app, base_url="http://evil.example")
    assert rebinding.get("/api/catalog").status_code == 400


def test_api_session_masks_and_writes(client, tmp_path):
    r = client.post("/api/session", headers=H,
                    json={"cookie_header": "Cookie: sessionid=abcdef123456; 4images_userid=-1"})
    assert r.status_code == 200
    assert "abcdef123456" not in json.dumps(r.json())
    jar = load_cookies(str(tmp_path / "cookies.txt"))
    assert jar.get("sessionid") == "abcdef123456" and jar.get("4images_userid") == "-1"
    client.post("/api/session", headers=H, json={"sessionid": "fresh999999"})
    jar = load_cookies(str(tmp_path / "cookies.txt"))
    assert jar.get("sessionid") == "fresh999999" and jar.get("4images_userid") == "-1"  # merged
    assert client.post("/api/session", headers=H, json={"sessionid": "a\tb"}).status_code == 400


def test_api_catalog_detail_media_reclassify(client):
    ids = {i["id"] for i in client.get("/api/catalog").json()}
    assert ids >= {"101", "102", "103", "104", "105", "106"}
    d = client.get("/api/items/101").json()
    assert d["method"] == "EXPLICIT_METADATA" and "Вечерній звонъ." in d["description"]
    m = client.get("/media/101")
    assert m.status_code == 200 and m.content[:3] == b"ID3"
    assert client.get("/media/101", headers={"Range": "bytes=0-9"}).status_code == 206
    assert client.get("/media/101/label/0").content[:3] == b"\xff\xd8\xff"
    assert client.get("/media/101/label/5").status_code == 404
    insp = client.get("/api/items/101/inspect").json()
    assert insp["valid"] and insp["tags"]["TLAN"] == "rus" and insp["id3_version"] == "2.4"
    r = client.post("/api/items/103/reclassify", headers=H,
                    json={"language": "Instrumental", "category": "Марши"})
    assert r.status_code == 200 and r.json()["method"] == "MANUAL"
    assert r.json()["file"].startswith("Instrumental/Марши/")
    assert client.get("/media/103").status_code == 200


def test_api_media_refuses_paths_outside_archive(client, built_archive, tmp_path):
    db, _ = built_archive
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    c = sqlite3.connect(db)
    c.execute("UPDATE tracks SET local_filepath=? WHERE item_id='104'", (str(outside),))
    c.commit()
    c.close()
    assert client.get("/media/104").status_code == 404


def test_api_export_and_job_guards(client):
    assert client.post("/api/export", headers=H).status_code == 200
    r = client.post("/api/job/start", headers=H, json={"mode": "test"})
    assert r.status_code == 409 and "cookies" in r.json()["detail"]  # no session saved yet
    assert client.post("/api/job/pause", headers=H).status_code == 409
    assert client.get("/").status_code == 200


def test_progress_exposes_speedometer_totals(client):
    p = client.get("/api/job").json()["progress"]
    # running totals the dashboard turns into tracks/min, pages/min and MB/s
    for k in ("bytes_total", "pages_done", "completed_total"):
        assert k in p and isinstance(p[k], int)
    assert p["completed_total"] == p["items"].get("COMPLETED", 0)
    assert p["bytes_total"] >= 0


def test_build_gift_endpoint_starts_one_background_update(client, monkeypatch):
    import rr_archive.web.app as app_mod
    calls = []

    class FakePopen:                      # never launch a real build from the test suite
        def __init__(self, args, **kwargs):
            calls.append(args)

        def poll(self):
            return None                   # still running

    monkeypatch.setattr(app_mod.subprocess, "Popen", FakePopen)
    r = client.post("/api/build-gift", headers=H)
    assert r.status_code == 200 and r.json()["started"] is True
    assert "--build-gift" in calls[0] and calls[0][-2:] == ["--gift-workers", "4"]
    again = client.post("/api/build-gift", headers=H)   # double click: no second build
    assert again.status_code == 409 and len(calls) == 1
