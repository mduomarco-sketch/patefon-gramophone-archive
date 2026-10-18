"""Parallel crawling: shared adaptive rate limiter and the worker coordinator."""

import collections
from pathlib import Path

import pytest
import requests

from conftest import BASE, FakeAdapter
from rr_archive import config
from rr_archive.crawler import Crawler
from rr_archive.net import CaptchaRequired, PoliteClient, RateLimiter
from rr_archive.state import State
from rr_archive.web.jobs import JobManager


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


# --- limiter ------------------------------------------------------------------------

def test_limiter_spaces_request_starts_site_wide():
    clock = FakeClock()
    lim = RateLimiter(0.5, 0.5, max_workers=4, sleep=clock.sleep, clock=clock)
    starts = []
    for _ in range(5):
        lim.wait()
        starts.append(clock.t)
    gaps = [round(b - a, 3) for a, b in zip(starts, starts[1:])]
    assert gaps == [0.5, 0.5, 0.5, 0.5]


def test_green_runs_full_speed_with_no_artificial_delay():
    clock = FakeClock()
    lim = RateLimiter(config.PARALLEL_MIN_INTERVAL, 0.0, max_workers=16, sleep=clock.sleep, clock=clock)
    assert lim.allowed == 16 and lim.state == "green"        # starts at the selected count
    t0 = clock.t
    for _ in range(50):
        lim.wait()
        lim.success(0.4)
    assert clock.t == t0 and lim.allowed == 16               # no pacing while healthy


def test_yellow_needs_three_slow_responses_in_a_row():
    clock = FakeClock()
    lim = RateLimiter(0.0, 0.02, max_workers=16, sleep=clock.sleep, clock=clock)
    lim.success(RateLimiter.SLOW_TTFB + 1)
    lim.success(RateLimiter.SLOW_TTFB + 1)
    lim.success(0.5)                                          # streak broken
    lim.success(RateLimiter.SLOW_TTFB + 1)
    lim.success(RateLimiter.SLOW_TTFB + 1)
    assert lim.allowed == 16 and lim.trouble_events == 0
    lim.success(RateLimiter.SLOW_TTFB + 1)                    # third in a row
    assert lim.allowed == 12 and lim.state == "yellow"        # cut by 25 %


def test_errors_cut_25_percent_for_15s_then_ramp_back_by_4():
    clock = FakeClock()
    lim = RateLimiter(0.0, 0.02, max_workers=16, sleep=clock.sleep, clock=clock)
    lim.trouble("HTTP 503")
    assert lim.allowed == 12
    lim.trouble("timeout")
    assert lim.allowed == 9
    for _ in range(RateLimiter.RAMP_AFTER * 3):                # no ramp-up inside the penalty window
        lim.success(0.3)
    assert lim.allowed == 9
    clock.t += RateLimiter.PENALTY_SECONDS + 0.1
    for _ in range(RateLimiter.RAMP_AFTER):
        lim.success(0.3)
    assert lim.allowed == 13
    for _ in range(RateLimiter.RAMP_AFTER):
        lim.success(0.3)
    assert lim.allowed == 16 and lim.state == "green"         # back to full, never above the cap
    for _ in range(RateLimiter.RAMP_AFTER * 2):
        lim.success(0.3)
    assert lim.allowed == 16


def test_middling_responses_hold_the_level():
    clock = FakeClock()
    lim = RateLimiter(0.0, 0.02, max_workers=16, sleep=clock.sleep, clock=clock)
    lim.trouble("429")
    clock.t += RateLimiter.PENALTY_SECONDS + 1
    for _ in range(RateLimiter.RAMP_AFTER * 3):                # 3-8 s: neither healthy nor strained
        lim.success((RateLimiter.HEALTHY_TTFB + RateLimiter.SLOW_TTFB) / 2)
    assert lim.allowed == 12


def test_limiter_bounds():
    with pytest.raises(ValueError):
        RateLimiter(-0.1, 1.0)
    with pytest.raises(ValueError):
        RateLimiter(0.0, 1.0, max_workers=config.PARALLEL_MAX_WORKERS + 1)
    assert config.PARALLEL_MAX_WORKERS == 32 and config.PARALLEL_DEFAULT_WORKERS == 1   # polite default
    assert RateLimiter(0.0, 0.02, max_workers=32).allowed == 32


def test_client_reports_server_errors_to_limiter(monkeypatch):
    monkeypatch.setattr(config, "MAX_RETRIES", 1)
    clock = FakeClock()
    lim = RateLimiter(0.0, 0.02, max_workers=4, sleep=clock.sleep, clock=clock)
    s = requests.Session()
    s.mount("https://", FakeAdapter({BASE + "index.php": (503, {}, b"")}, []))
    client = PoliteClient(session=s, limiter=lim, sleep=clock.sleep, clock=clock)
    with pytest.raises(Exception):
        client.fetch_html(BASE + "index.php")
    assert lim.trouble_events == 2 and lim.allowed == 2       # 4 -> 3 -> 2


def test_default_session_uses_a_large_keepalive_pool():
    c = PoliteClient(limiter=RateLimiter(0.0, 0.02, max_workers=2))
    adapter = c.session.get_adapter("https://www.russian-records.com/")
    assert adapter._pool_maxsize == config.HTTP_POOL_SIZE


# --- coordinator -------------------------------------------------------------------------

def parallel_setup(tmp_path, site, workers=3):
    log = []
    lim = RateLimiter(config.PARALLEL_MIN_INTERVAL_FLOOR, config.PARALLEL_MIN_INTERVAL_FLOOR,
                      max_workers=workers, start_workers=workers, sleep=lambda s: None)

    def factory():
        sess = requests.Session()
        sess.mount("https://", FakeAdapter(site, log))
        return PoliteClient(session=sess, limiter=lim, sleep=lambda s: None)
    state = State(tmp_path / "s.db")
    crawler = Crawler(factory(), state, tmp_path / "archive", cache_dir=tmp_path / "cache",
                      control_dir=tmp_path / "ctl")
    crawler.seed()
    return crawler, state, factory, lim, log


def test_parallel_crawl_downloads_everything_once(tmp_path, fake_site):
    crawler, state, factory, lim, log = parallel_setup(tmp_path, fake_site)
    crawler.run_parallel(factory, lim, 3, queue="audio-first")
    done = {r["item_id"] for r in state.conn.execute("SELECT item_id FROM tracks WHERE status='COMPLETED'")}
    assert done == {"101", "102", "103", "104", "105", "106"}
    per_url = collections.Counter(log)
    media = [u for u in per_url if "/data/media/" in u or "view_pdf" in u]
    assert media and all(per_url[u] == 1 for u in media), {u: n for u, n in per_url.items() if n > 1}
    assert crawler.completed_this_run == 6
    assert not list((tmp_path / "archive").rglob("*.part"))
    for r in state.conn.execute("SELECT local_filepath FROM tracks WHERE status='COMPLETED'"):
        assert Path(r[0]).exists()
    state.close()


def test_parallel_limit_is_exact(tmp_path, fake_site):
    crawler, state, factory, lim, _ = parallel_setup(tmp_path, fake_site)
    crawler.run_parallel(factory, lim, 3, limit=2, queue="audio-first")
    n = state.conn.execute("SELECT COUNT(*) FROM tracks WHERE status='COMPLETED'").fetchone()[0]
    assert crawler.completed_this_run == 2 and n == 2
    state.close()


def test_captcha_in_any_worker_halts_the_run(tmp_path, fake_site):
    site = dict(fake_site)
    site[BASE + "data/media/2/nich.mp3"] = (302, {"Location": BASE + "get_captcha.php"}, b"")
    crawler, state, factory, lim, log = parallel_setup(tmp_path, site)
    with pytest.raises(CaptchaRequired):
        crawler.run_parallel(factory, lim, 3, queue="audio-first")
    assert not any("captcha" in u for u in log)          # never requested
    assert not list((tmp_path / "archive").rglob("*.part"))
    state.close()


def test_dashboard_command_passes_workers(tmp_path):
    jm = JobManager(tmp_path)
    cmd = jm.command("crawl", tmp_path / "c.txt", queue="audio-only", limit=500, workers=4)
    assert cmd[-2:] == ["--workers", "4"]
    assert "--workers" not in jm.command("crawl", tmp_path / "c.txt", workers=1)
    assert "--workers" not in jm.command("test", tmp_path / "c.txt", workers=4)
    with pytest.raises(ValueError):
        jm.start("crawl", tmp_path / "c.txt", workers=config.PARALLEL_MAX_WORKERS + 1)
    assert jm.command("crawl", tmp_path / "c.txt", workers=32)[-2:] == ["--workers", "32"]


# --- IPv4 pinning ---------------------------------------------------------------------

def test_force_ipv4_pins_urllib3():
    import socket
    import urllib3.util.connection as conn
    from rr_archive import config, net
    assert config.IPV4_ONLY is True
    saved = conn.allowed_gai_family
    try:
        conn.allowed_gai_family = lambda: socket.AF_UNSPEC
        net.force_ipv4()
        assert conn.allowed_gai_family() == socket.AF_INET
    finally:
        conn.allowed_gai_family = saved
