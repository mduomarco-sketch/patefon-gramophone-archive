"""PDF downloads, {Branch}/{Language}/{Category} for every branch, queue
policies (audio-first / audio-only / all) and batch limits."""

import json
from pathlib import Path

import pytest

from conftest import BASE, tiny_pdf
from rr_archive import classify, config
from rr_archive.crawler import Crawler, target_path
from rr_archive.media import InvalidMedia, sniff_pdf
from rr_archive.parser import parse_details
from rr_archive.reclassify import refile_from_cache
from rr_archive.state import State
from rr_archive.web.jobs import JobManager


def crawl(tmp_path, make_client, site=None, **kw):
    state = State(tmp_path / "s.db")
    client = make_client(site)
    crawler = Crawler(client, state, tmp_path / "archive", cache_dir=tmp_path / "cache",
                      control_dir=tmp_path / "ctl")
    crawler.seed()
    crawler.run(**kw)
    return state, crawler, client


# --- PDF ------------------------------------------------------------------------

def test_pdf_url_parsed(fake_site):
    item = parse_details(fake_site[BASE + "details.php?image_id=105"][2].decode("cp1251"),
                         BASE + "details.php?image_id=105")
    assert item.extra["pdf_urls"] == [BASE + "view_pdf.php?image_id=105&image=2"]


def test_pdf_downloaded_next_to_cover(tmp_path, make_client):
    state, _c, _cl = crawl(tmp_path, make_client)
    r = state.get_track("105")
    assert r["status"] == "COMPLETED"
    cover, pdf = Path(r["local_filepath"]), Path(r["pdf_local_path"])
    rel = cover.relative_to(tmp_path / "archive").as_posix()
    assert rel.startswith("Newspapers_and_Print/Russian/Газеты/1913 - ")
    assert pdf == cover.with_suffix(".pdf") and pdf.read_bytes() == tiny_pdf()
    assert not list((tmp_path / "archive").rglob("*.part"))
    state.close()


def test_truncated_or_html_pdf_rejected(tmp_path, make_client, fake_site):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4\nno end marker")
    with pytest.raises(InvalidMedia):
        sniff_pdf(p)
    site = dict(fake_site)
    site[BASE + "view_pdf.php?image_id=105&image=2"] = (200, {"Content-Type": "application/pdf"},
                                                        b"%PDF-1.4\ncut off")
    state, crawler, _ = crawl(tmp_path, make_client, site)
    r = state.get_track("105")
    assert r["status"] == "FAILED" and "PDF" in r["last_error"]
    assert Path(target_path(r, tmp_path / "archive", ".jpg")).exists()  # cover kept for retry
    # Retry fetches only the PDF: cover adopted, no image re-download.
    site[BASE + "view_pdf.php?image_id=105&image=2"] = (200, {"Content-Type": "application/pdf"}, tiny_pdf())
    before = len(crawler.client.request_log)
    crawler.run(retry_failed=True)
    new = crawler.client.request_log[before:]
    assert state.get_track("105")["status"] == "COMPLETED"
    assert any("view_pdf.php" in u for u in new) and not any("gazeta.jpg" in u for u in new)
    state.close()


def test_refile_backfills_pdf_for_old_print_items(tmp_path, make_client):
    state, crawler, _ = crawl(tmp_path, make_client)
    r = state.get_track("105")
    pdf = Path(r["pdf_local_path"])
    pdf.unlink()  # as if downloaded before PDF support existed
    state.conn.execute("UPDATE tracks SET pdf_url=NULL, pdf_local_path=NULL WHERE item_id='105'")
    state.conn.commit()
    stats = refile_from_cache(state, tmp_path / "archive", tmp_path / "cache")
    assert stats.get("pdf_pending") == 1, stats
    assert state.get_track("105")["status"] == "PENDING"
    crawler.run()
    r = state.get_track("105")
    assert r["status"] == "COMPLETED" and Path(r["pdf_local_path"]).exists()
    state.close()


# --- taxonomy -------------------------------------------------------------------

def test_every_branch_has_language_level(tmp_path):
    row = {"language": "Russian", "category": "Cat", "artist": "A", "year": "1910",
           "title": "T", "item_id": "9"}
    root = tmp_path
    rels = {mt: target_path({**row, "media_type": mt}, root, ".x").relative_to(root).parts
            for mt in (classify.MUSIC, classify.RADIO, classify.PRINT, classify.IMAGE)}
    assert rels[classify.MUSIC][:2] == ("Russian", "Cat")
    assert rels[classify.RADIO][:3] == ("Radio_and_Speech", "Russian", "Cat")
    assert rels[classify.PRINT][:3] == ("Newspapers_and_Print", "Russian", "Cat")
    assert rels[classify.IMAGE][:3] == ("Images_and_Scans", "Russian", "Cat")


def test_refile_moves_images_into_language_folder(tmp_path):
    from rr_archive.reclassify import _relocate, _full_retag
    root = tmp_path / "archive"
    old = root / "Images_and_Scans" / "Cat" / "Unknown_Year - T [9].jpg"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"\xff\xd8\xff" + b"\x00" * 16)
    state = State(tmp_path / "s.db")
    state.upsert_track("9", {"source_url": "u", "media_type": "IMAGE", "language": "Russian",
                             "category": "Cat", "title": "T"}, "PENDING")
    state.mark_completed("9", str(old), "x", 19, [])
    row = state.get_track("9")
    new = _relocate(state, row, dict(row), root, _full_retag(dict(row)), {})
    assert Path(new["local_filepath"]).relative_to(root).parts[:3] == ("Images_and_Scans", "Russian", "Cat")
    assert not old.exists() and not old.parent.exists()
    state.close()


# --- queue policies -------------------------------------------------------------

def _completion_order(state):
    return [r["item_id"] for r in state.conn.execute(
        "SELECT item_id FROM tracks WHERE status='COMPLETED' ORDER BY downloaded_at, rowid")]


def test_audio_first_downloads_audio_before_print(tmp_path, make_client):
    state, _c, client = crawl(tmp_path, make_client, queue="audio-first")
    log = client.request_log
    audio_reqs = [i for i, u in enumerate(log) if u.endswith(".mp3")]
    print_reqs = [i for i, u in enumerate(log) if "details.php?image_id=105" in u or "view_pdf" in u]
    assert len(audio_reqs) == 5 and print_reqs
    assert max(audio_reqs) < min(print_reqs)          # every track before the newspaper is even visited
    assert state.get_track("105")["status"] == "COMPLETED"
    state.close()


def test_audio_only_never_touches_audioless_items(tmp_path, make_client):
    state, crawler, client = crawl(tmp_path, make_client, queue="audio-only")
    assert not any("image_id=105" in u or "view_pdf" in u or "gazeta" in u for u in client.request_log)
    done = {r["item_id"] for r in state.conn.execute("SELECT item_id FROM tracks WHERE status='COMPLETED'")}
    assert done == {"101", "102", "103", "104", "106"}
    assert state.deferred_counts()["details_pages_without_audio"] == 1
    # Later: switch to audio-first and the deferred newspaper is fetched.
    crawler.run(queue="audio-first")
    assert state.get_track("105")["status"] == "COMPLETED"
    state.close()


def test_batch_limit_and_zero_unlimited(tmp_path, make_client):
    state, crawler, _ = crawl(tmp_path, make_client, queue="audio-first", limit=2)
    assert crawler.completed_this_run == 2
    crawler.completed_this_run = 0
    crawler.run(queue="audio-first", limit=0)        # 0 = unlimited
    assert crawler.completed_this_run == 4
    state.close()


def test_invalid_queue_rejected(tmp_path, make_client):
    state = State(tmp_path / "s.db")
    with pytest.raises(ValueError):
        Crawler(make_client(), state, tmp_path / "a", cache_dir=None).run(queue="bogus")
    state.close()


def test_dashboard_job_command(tmp_path):
    jm = JobManager(tmp_path)
    cookies = tmp_path / "cookies.txt"
    crawl_cmd = jm.command("crawl", cookies, queue="audio-only", limit=500)
    assert crawl_cmd[-4:] == ["--queue", "audio-only", "--limit", "500"]
    assert "--limit" not in jm.command("crawl", cookies, limit=0)        # unlimited
    assert "--queue" not in jm.command("test", cookies, queue="audio-only", limit=9)
    with pytest.raises(ValueError):
        jm.start("crawl", cookies, queue="nope")
    with pytest.raises(ValueError):
        jm.start("crawl", cookies, limit=-1)
