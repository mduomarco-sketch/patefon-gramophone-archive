"""Polite single-threaded HTTP client.

Rules enforced here (not left to callers):
  * one request at a time, randomized 2-4 s gap before *every* request,
    including redirects and retries;
  * exponential backoff on 429/5xx/timeouts (base 5 s, max 120 s, 5 retries);
  * redirects are followed manually, and any hop toward get_captcha.php
    raises CaptchaRequired immediately. The CAPTCHA endpoints are never
    requested and nothing here tries to solve or bypass them.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import logging
import random
import re
import socket
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlsplit

import requests

from . import config  # noqa: F811  (config is used throughout this module)


def force_ipv4() -> None:
    """Pin urllib3 (and thus requests) to IPv4. On this dual-stack network the
    site's IPv6 route intermittently times out; IPv4-only avoids the stall.
    Process-global and idempotent; applied on import when config.IPV4_ONLY."""
    try:
        import urllib3.util.connection as _conn
        _conn.allowed_gai_family = lambda: socket.AF_INET
    except Exception:  # never let an import quirk stop the crawler
        pass


if getattr(config, "IPV4_ONLY", True):
    force_ipv4()

from . import config

log = logging.getLogger(__name__)


class CaptchaRequired(Exception):
    """The server wants a human verification. Halt the whole run."""


class SessionProblem(Exception):
    """Session looks dead or blocked (e.g. repeated 403). Halt the whole run."""


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


CAPTCHA_HTML_RE = re.compile(
    rb"""(action\s*=\s*["']?[^"'>]*get_captcha\.php|name\s*=\s*["']?proverka\b|id\s*=\s*["']?captcha_image)""",
    re.I,
)


def is_captcha_url(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return any(path.endswith(m) or f"/{m}" in path for m in config.CAPTCHA_MARKERS)


def is_allowed_url(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return False
    if (parts.hostname or "").lower() not in config.ALLOWED_HOSTS:
        return False
    path = parts.path.lower()
    return not any(path.startswith(p) for p in config.FORBIDDEN_PATH_PREFIXES)


def looks_like_captcha_page(body: bytes) -> bool:
    return bool(CAPTCHA_HTML_RE.search(body[:200_000]))


# ---------------------------------------------------------------------------
# Cookie loading
# ---------------------------------------------------------------------------

def load_cookies(cookie_file: str | None = None,
                 session_id: str | None = None,
                 cookie_header: str | None = None) -> requests.cookies.RequestsCookieJar:
    """Build a cookie jar from user-supplied, human-authenticated cookies.

    cookie_file may be a Netscape cookies.txt, a JSON export (list of
    {name, value, domain, path}), or a file holding a raw "a=b; c=d" header.
    """
    jar = requests.cookies.RequestsCookieJar()
    host = config.CANONICAL_HOST

    def add(name, value, domain=None, path="/"):
        name, value = name.strip(), value.strip()
        if name:
            jar.set(name, value, domain=(domain or host), path=path or "/")

    if cookie_file:
        text = Path(cookie_file).read_text(encoding="utf-8-sig").strip()
        if text.startswith("[") or text.startswith("{"):
            data = json.loads(text)
            if isinstance(data, dict):
                data = data.get("cookies", [data])
            for c in data:
                domain = (c.get("domain") or host).lstrip(".")
                if domain.endswith("russian-records.com"):
                    add(c["name"], str(c.get("value", "")), domain, c.get("path", "/"))
        elif "\t" in text or text.startswith("# Netscape") or text.startswith("# HTTP Cookie"):
            for line in text.splitlines():
                if line.startswith("#HttpOnly_"):
                    line = line[len("#HttpOnly_"):]
                elif not line.strip() or line.startswith("#"):
                    continue
                fields = line.split("\t")
                if len(fields) >= 7:
                    domain = fields[0].lstrip(".")
                    if domain.endswith("russian-records.com"):
                        add(fields[5], fields[6], domain, fields[2])
        else:
            cookie_header = (cookie_header + "; " if cookie_header else "") + text

    if cookie_header:
        header = re.sub(r"^\s*cookie\s*:\s*", "", cookie_header, flags=re.I)
        for pair in header.split(";"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                add(k, v)

    if session_id:
        add(config.SESSION_COOKIE_NAME, session_id)

    return jar


# ---------------------------------------------------------------------------
# Shared rate limiter (parallel mode)
# ---------------------------------------------------------------------------

class RateLimiter:
    """One limiter shared by every worker thread - connection health is the
    only speed governor (uncapped bulk sync allowed by the maintainer).

    Green  (healthy):  responses with time-to-first-byte under HEALTHY_TTFB run
                       at 100 % of the configured workers, with only a tiny
                       jitter (min_interval..max_interval) between request starts.
    Yellow (strain):   SLOW_STREAK consecutive responses slower than SLOW_TTFB,
                       or any timeout / connection error / 429 / 5xx, cut the
                       allowed workers by CUT (25 %) for PENALTY_SECONDS; each
                       further sign of strain cuts again.
    Recovery:          after the penalty window, every RAMP_AFTER clean
                       responses add RAMP_STEP workers until back at full speed.
    """

    HEALTHY_TTFB = 3.0
    SLOW_TTFB = 8.0
    SLOW_STREAK = 3
    CUT = 0.25
    PENALTY_SECONDS = 15.0
    RAMP_AFTER = 10
    RAMP_STEP = 4

    def __init__(self, min_interval: float, max_interval: float, max_workers: int = 1,
                 start_workers: int | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        if min_interval < config.PARALLEL_MIN_INTERVAL_FLOOR:
            raise ValueError(f"min_interval must be >= {config.PARALLEL_MIN_INTERVAL_FLOOR}s")
        if not 1 <= max_workers <= config.PARALLEL_MAX_WORKERS:
            raise ValueError(f"workers must be between 1 and {config.PARALLEL_MAX_WORKERS}")
        self.min_interval, self.max_interval = min_interval, max(max_interval, min_interval)
        self.max_workers = max_workers
        self.allowed = min(max_workers, start_workers or max_workers)   # start at full speed
        self._sleep, self._clock = sleep, clock
        self._lock = threading.Lock()
        self._next_slot = 0.0
        self._ok_streak = 0
        self._slow_streak = 0
        self._penalty_until = 0.0
        self.trouble_events = 0

    @property
    def state(self) -> str:
        return "yellow" if self._clock() < self._penalty_until or self.allowed < self.max_workers else "green"

    def wait(self) -> None:
        if self.max_interval <= 0:
            return
        with self._lock:
            now = self._clock()
            slot = max(now, self._next_slot)
            self._next_slot = slot + random.uniform(self.min_interval, self.max_interval)
        if slot > now:
            self._sleep(slot - now)

    def success(self, seconds: float | None = None) -> None:
        with self._lock:
            if seconds is not None and seconds > self.SLOW_TTFB:
                self._slow_streak += 1
                if self._slow_streak < self.SLOW_STREAK:
                    return
            else:
                self._slow_streak = 0
        if seconds is not None and seconds > self.SLOW_TTFB:
            self.trouble(f"{self.SLOW_STREAK} responses slower than {self.SLOW_TTFB:.0f}s")
            return
        with self._lock:
            if seconds is not None and seconds > self.HEALTHY_TTFB:
                return                      # neither healthy nor strained: hold the current level
            self._ok_streak += 1
            if (self._ok_streak >= self.RAMP_AFTER and self.allowed < self.max_workers
                    and self._clock() >= self._penalty_until):
                self.allowed = min(self.max_workers, self.allowed + self.RAMP_STEP)
                self._ok_streak = 0
                log.info("connection healthy - workers back up to %d/%d", self.allowed, self.max_workers)

    def trouble(self, why: str = "") -> None:
        with self._lock:
            before = self.allowed
            self.allowed = max(1, int(self.allowed * (1 - self.CUT)))
            self._ok_streak = 0
            self._slow_streak = 0
            self._penalty_until = self._clock() + self.PENALTY_SECONDS
            self.trouble_events += 1
        log.warning("connection strain (%s) - workers %d -> %d for %ds",
                    why, before, self.allowed, int(self.PENALTY_SECONDS))


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class PoliteClient:
    def __init__(self,
                 cookies: requests.cookies.RequestsCookieJar | None = None,
                 user_agent: str = config.DEFAULT_USER_AGENT,
                 min_delay: float = config.DEFAULT_MIN_DELAY,
                 max_delay: float = config.DEFAULT_MAX_DELAY,
                 session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 enforce_floor: bool = True,
                 limiter: "RateLimiter | None" = None):
        # A shared limiter (parallel mode) replaces the per-client 2-4 s gap;
        # its own floor applies instead.
        if limiter is None and enforce_floor and min_delay < config.MIN_DELAY_FLOOR:
            raise ValueError(f"min_delay must be >= {config.MIN_DELAY_FLOOR}s (maintainer terms)")
        if max_delay < min_delay:
            raise ValueError("max_delay must be >= min_delay")
        self.limiter = limiter
        self.min_delay, self.max_delay = min_delay, max_delay
        self._sleep, self._clock = sleep, clock
        self._last_request: float | None = None
        self.request_count = 0
        self._consecutive_403 = 0

        if session is None:
            session = requests.Session()
            pool = requests.adapters.HTTPAdapter(pool_connections=config.HTTP_POOL_SIZE,
                                                 pool_maxsize=config.HTTP_POOL_SIZE)
            session.mount("https://", pool)
            session.mount("http://", pool)
        self.session = session
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept-Language": "ru,en;q=0.8",
            # HTML (and other text) comes back gzipped; requests decodes it.
            # Binary media is already compressed, so the server won't re-encode it.
            "Accept-Encoding": "gzip, deflate",
        })
        if cookies is not None:
            self.session.cookies.update(cookies)

    # -- throttling ---------------------------------------------------------
    def _throttle(self) -> None:
        if self.limiter is not None:
            self.limiter.wait()
            self.request_count += 1
            return
        if self._last_request is not None:
            gap = random.uniform(self.min_delay, self.max_delay)
            wait = self._last_request + gap - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()
        self.request_count += 1

    def _trouble(self, why: str) -> None:
        if self.limiter is not None:
            self.limiter.trouble(why)

    def _ok(self, seconds: float | None = None) -> None:
        if self.limiter is not None:
            self.limiter.success(seconds)

    # -- core request with retries and manual redirects ---------------------
    def get(self, url: str, stream: bool = False, max_redirects: int = 5) -> requests.Response:
        for _ in range(max_redirects + 1):
            if is_captcha_url(url):
                raise CaptchaRequired(url)
            if not is_allowed_url(url):
                raise FetchError(f"refusing off-site or disallowed URL: {url}")
            resp = self._get_with_retries(url, stream)
            if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location", "")
                resp.close()
                target = urljoin(url, location)
                if is_captcha_url(target):
                    raise CaptchaRequired(target)
                log.debug("redirect %s -> %s", url, target)
                url = target
                continue
            self._check_status(resp, url)
            return resp
        raise FetchError(f"too many redirects: {url}")

    def _get_with_retries(self, url: str, stream: bool) -> requests.Response:
        attempt = 0
        while True:
            self._throttle()
            started = self._clock()
            try:
                resp = self.session.get(url, stream=stream, allow_redirects=False,
                                        timeout=config.REQUEST_TIMEOUT)
            except (requests.Timeout, requests.ConnectionError) as exc:
                self._trouble(f"{type(exc).__name__}")
                if attempt >= config.MAX_RETRIES:
                    raise FetchError(f"network error after {attempt} retries: {exc}") from exc
                self._backoff(attempt, None, f"{type(exc).__name__} on {url}")
                attempt += 1
                continue
            if resp.status_code in config.RETRY_STATUSES:
                self._trouble(f"HTTP {resp.status_code}")
                if attempt >= config.MAX_RETRIES:
                    resp.close()
                    raise FetchError(f"HTTP {resp.status_code} after {attempt} retries", resp.status_code)
                retry_after = resp.headers.get("Retry-After")
                resp.close()
                self._backoff(attempt, retry_after, f"HTTP {resp.status_code} on {url}")
                attempt += 1
                continue
            # Time to first byte is the server's own health signal (streamed
            # bodies are excluded: big files are slow by nature, not by strain).
            self._ok(self._clock() - started)
            return resp

    def _backoff(self, attempt: int, retry_after: str | None, why: str) -> None:
        delay = min(config.BACKOFF_MAX, config.BACKOFF_BASE * (2 ** attempt))
        if retry_after and retry_after.strip().isdigit():
            delay = max(delay, float(retry_after))  # always honour the server
        delay += random.uniform(0, 1.0)
        log.warning("%s - backing off %.1fs (retry %d/%d)", why, delay, attempt + 1, config.MAX_RETRIES)
        self._sleep(delay)

    def _check_status(self, resp: requests.Response, url: str) -> None:
        if resp.status_code == 403:
            self._consecutive_403 += 1
            resp.close()
            if self._consecutive_403 >= config.MAX_CONSECUTIVE_403:
                raise SessionProblem(f"{self._consecutive_403} consecutive HTTP 403 responses (last: {url})")
            raise FetchError("HTTP 403", 403)
        self._consecutive_403 = 0
        if resp.status_code >= 400:
            resp.close()
            raise FetchError(f"HTTP {resp.status_code}", resp.status_code)

    # -- helpers ------------------------------------------------------------
    def fetch_html(self, url: str) -> tuple[str, bytes, str]:
        """Return (final_url, raw_bytes, decoded_text)."""
        resp = self.get(url)
        body = resp.content
        if looks_like_captcha_page(body):
            raise CaptchaRequired(resp.url or url)
        return resp.url or url, body, decode_html(body, resp.headers.get("Content-Type", ""))

    def download(self, url: str, dest: Path, chunk: int = 256 * 1024) -> dict:
        """Stream url into dest (a .part path). Returns size/type/sha256.

        Raises FetchError on truncation and CaptchaRequired if the server
        hands back the verification page instead of media."""
        resp = self.get(url, stream=True)
        ctype = resp.headers.get("Content-Type", "").lower()
        expected = resp.headers.get("Content-Length")
        sha = hashlib.sha256()
        size = 0
        head = b""
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(dest, "wb") as fh:
                for block in resp.iter_content(chunk):
                    if not block:
                        continue
                    if len(head) < 4096:
                        head += block[: 4096 - len(head)]
                    fh.write(block)
                    sha.update(block)
                    size += len(block)
        except BaseException:
            # The stream broke mid-transfer (e.g. IncompleteRead). Downloads never
            # resume, so the partial file is useless - and for an optional label
            # scan on an item that still completes, nothing would ever retry it.
            dest.unlink(missing_ok=True)
            raise
        finally:
            resp.close()
        if "text/html" in ctype or head.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
            body = dest.read_bytes()
            dest.unlink(missing_ok=True)
            if looks_like_captcha_page(body):
                raise CaptchaRequired(url)
            raise FetchError(f"expected media but got HTML ({ctype or 'no content-type'})")
        if expected and expected.isdigit() and int(expected) != size:
            dest.unlink(missing_ok=True)
            raise FetchError(f"truncated download: {size} of {expected} bytes")
        return {"size": size, "content_type": ctype, "sha256": sha.hexdigest(), "head": head}


META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([\w-]+)""", re.I)


def decode_html(body: bytes, content_type: str = "") -> str:
    """Decode a page, preferring HTTP header, then <meta>, then cp1251.

    The live site serves windows-1251; browser 'Save as' copies are often
    re-encoded to UTF-8, so try UTF-8 strictly before falling back."""
    candidates = []
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if m:
        candidates.append(m.group(1))
    m = META_CHARSET_RE.search(body[:4096])
    if m:
        candidates.append(m.group(1).decode("ascii", "ignore"))
    candidates += ["utf-8", "cp1251"]
    for enc in candidates:
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("cp1251", errors="replace")
