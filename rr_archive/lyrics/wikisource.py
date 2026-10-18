"""Public-domain lyrics from Russian Wikisource (ru.wikisource.org).

Only pages Wikisource itself marks as public domain are used, and only when
the record and the page agree:
  * exact title match after normalising pre-1918 spelling and punctuation;
  * if the record names its lyricist, the page's author must match;
  * the page must contain verse (a .poem block);
  * ambiguous results (several public-domain candidates, no way to decide)
    are skipped rather than guessed.

Wikimedia API etiquette: one request per second, maxlag, identifying UA.
Resumable: every item checked is remembered in _lyrics/_wikisource.json.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from . import LYRICS_DIR, _atomic_write, existing_source, load_meta, save_meta, store_lyrics

log = logging.getLogger(__name__)

API = "https://ru.wikisource.org/w/api.php"
USER_AGENT = "PatefonArchive/1.0 (personal non-commercial record archive; uses public-domain texts only)"
CHECKED_PATH = LYRICS_DIR / "_wikisource.json"
MIN_INTERVAL = 1.0

PD_TEMPLATE_RE = re.compile(r"(^|:)PD[-\s]|public domain|общественн\w* достояни", re.I)
DISAMBIG_RE = re.compile(r"неоднозначност|disambig|список значений", re.I)
CYR_RE = re.compile(r"[А-Яа-яЁёІіѢѣѲѳѴѵЇїЄєҐґЎў]")
FOLK_RE = re.compile(r"народн|folk|traditional|фольклор", re.I)


def normalize_title(s: str) -> str:
    s = (s or "").lower()
    for a, b in (("ё", "е"), ("ѣ", "е"), ("і", "и"), ("ѳ", "ф"), ("ѵ", "и"), ("ї", "и")):
        s = s.replace(a, b)
    s = re.sub(r"ъ\b", "", s)                    # pre-reform word-final hard sign
    s = re.sub(r"[«»\"'“”„()\[\].,!?;:…—–\-]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def surname(name: str) -> str:
    """'S. Mukanov' / 'Мукан С.' / 'Сабит Муканов' -> lowercase longest word."""
    words = [w for w in re.split(r"[\s.,]+", (name or "").lower().replace("ё", "е")) if len(w) > 2]
    return max(words, key=len) if words else ""


def title_candidates(row: dict, raw: dict) -> list[str]:
    """Cyrillic titles to look up: parenthesised originals in the bilingual
    title, then the site's 'Title Name' field."""
    title = row.get("title") or ""
    cands = [p.strip() for p in re.findall(r"\(([^()]+)\)", title) if CYR_RE.search(p)]
    if CYR_RE.search(title.split("(")[0]):
        cands.append(title.split("(")[0].strip(" ,"))
    tn = (raw.get("fields") or {}).get("title") or ""
    if CYR_RE.search(tn):
        cands.append(tn)
    seen, out = set(), []
    for c in cands:
        n = normalize_title(c)
        if len(n) >= 3 and n not in seen:
            seen.add(n)
            out.append(c)
    return out


class WikisourceClient:
    def __init__(self, session: requests.Session | None = None, sleep=time.sleep):
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT})
        self._sleep, self._last, self.requests = sleep, 0.0, 0

    def get(self, **params) -> dict:
        wait = self._last + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        params = {"format": "json", "formatversion": "2", "maxlag": "5", **params}
        for attempt in range(4):
            r = self.s.get(API, params=params, timeout=30)
            self._last = time.monotonic()
            self.requests += 1
            if r.status_code == 200:
                data = r.json()
                if data.get("error", {}).get("code") == "maxlag":
                    self._sleep(5 * (attempt + 1))
                    continue
                return data
            if r.status_code in (429, 503):
                self._sleep(int(r.headers.get("Retry-After", 10)))
                continue
            r.raise_for_status()
        raise RuntimeError("Wikisource API unavailable")

    def search(self, title: str) -> list[str]:
        data = self.get(action="query", list="search", srsearch=f'intitle:"{title}"',
                        srnamespace="0", srlimit="10", srprop="")
        return [h["title"] for h in data.get("query", {}).get("search", [])]

    def page_info(self, titles: list[str]) -> list[dict]:
        data = self.get(action="query", prop="categories|templates", titles="|".join(titles[:20]),
                        cllimit="max", tllimit="max", redirects="1")
        return data.get("query", {}).get("pages", [])

    def page_html(self, title: str) -> str:
        data = self.get(action="parse", page=title, prop="text", disablelimitreport="1", redirects="1")
        return data.get("parse", {}).get("text", "")


def is_public_domain(page: dict) -> tuple[bool, str | None]:
    names = [t["title"] for t in page.get("templates", [])] + [c["title"] for c in page.get("categories", [])]
    for n in names:
        if PD_TEMPLATE_RE.search(n):
            return True, n.split(":", 1)[-1]
    return False, None


def is_disambiguation(page: dict) -> bool:
    names = [t["title"] for t in page.get("templates", [])] + [c["title"] for c in page.get("categories", [])]
    return any(DISAMBIG_RE.search(n) for n in names)


def extract_verse(html: str) -> tuple[str, str]:
    """(verse text, author name as shown on the page)."""
    soup = BeautifulSoup(html, "lxml")
    for junk in soup.select("sup.reference, .mw-editsection, .noprint, .ws-noexport, .reference"):
        junk.decompose()
    author = ""
    for sel in ("#ws-author", ".author", "#header .author", "td.gen_header_author", "a[href*='/wiki/%D0%90%D0%B2%D1%82%D0%BE%D1%80']"):
        node = soup.select_one(sel)
        if node and node.get_text(strip=True):
            author = node.get_text(" ", strip=True)
            break
    stanzas = []
    for poem in soup.select("div.poem"):
        for br in poem.find_all("br"):
            br.replace_with("\n")
        lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in poem.get_text("").splitlines()]
        block, out = [], []
        for ln in lines + [""]:
            if ln:
                block.append(ln)
            elif block:
                out.append("\n".join(block))
                block = []
        if out:
            stanzas.append("\n\n".join(out))
    return "\n\n".join(stanzas).strip(), author


def find_lyrics(client: WikisourceClient, row: dict, raw: dict) -> dict | None:
    """Return {"text", "url", "license", "page"} for one record, or None."""
    lyricist = (raw.get("raw_fields") or {}).get("Lyrics By") or (raw.get("fields") or {}).get("lyricist") or ""
    folk = bool(FOLK_RE.search(lyricist) or FOLK_RE.search(row.get("title") or ""))
    for cand in title_candidates(row, raw):
        want = normalize_title(cand)
        hits = [t for t in client.search(cand) if normalize_title(re.sub(r"\s*\([^()]*\)\s*$", "", t)) == want]
        if not hits:
            continue
        pd_pages = []
        for page in client.page_info(hits):
            if page.get("missing") or is_disambiguation(page):
                continue
            ok, lic = is_public_domain(page)
            if ok:
                pd_pages.append((page["title"], lic))
        if not pd_pages:
            continue
        chosen = []
        for title, lic in pd_pages:
            text, author = extract_verse(client.page_html(title))
            if not text:
                continue
            page_author = author or (re.search(r"\(([^()]+)\)\s*$", title) or [None, ""])[1]
            if lyricist and not folk and surname(lyricist) and page_author:
                if surname(lyricist)[:5] not in page_author.lower().replace("ё", "е"):
                    continue                     # different poem with the same title
            chosen.append({"text": text, "page": title, "license": lic,
                           "url": "https://ru.wikisource.org/wiki/" + title.replace(" ", "_")})
        if len(chosen) == 1 or (chosen and lyricist and not folk):
            return chosen[0]
        if len(chosen) > 1:
            log.info("ambiguous Wikisource match for %s: %d pages", row.get("item_id"), len(chosen))
    return None


def run(conn, limit: int | None = None, client: WikisourceClient | None = None) -> dict:
    """Check every vocal record not yet checked. conn is only read."""
    client = client or WikisourceClient()
    meta = load_meta()
    try:
        checked = json.loads(CHECKED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        checked = {}
    rows = conn.execute(
        "SELECT item_id, title, language, raw_metadata_json FROM tracks WHERE status='COMPLETED' "
        "AND media_type='MUSIC' AND COALESCE(language,'') NOT IN ('Instrumental')").fetchall()
    stats = {"checked": 0, "found": 0, "skipped_existing": 0}
    for item_id, title, language, raw_json in rows:
        if item_id in checked:
            continue
        if existing_source(item_id, meta) in ("manual", "wikisource"):
            stats["skipped_existing"] += 1
            continue
        raw = json.loads(raw_json or "{}")
        try:
            hit = find_lyrics(client, {"item_id": item_id, "title": title}, raw)
        except (requests.RequestException, RuntimeError) as exc:
            log.warning("Wikisource error for %s: %s", item_id, exc)
            break                               # stop politely; resume next run
        checked[item_id] = {"found": bool(hit), "page": hit["page"] if hit else None}
        stats["checked"] += 1
        if hit and store_lyrics(item_id, hit["text"], "txt", "wikisource", meta=meta, url=hit["url"],
                                license=hit["license"], language=language, extra={"page": hit["page"]}):
            stats["found"] += 1
        if stats["checked"] % 10 == 0:
            save_meta(meta)
            _atomic_write(CHECKED_PATH, json.dumps(checked, ensure_ascii=False))
        if limit and stats["checked"] >= limit:
            break
    save_meta(meta)
    _atomic_write(CHECKED_PATH, json.dumps(checked, ensure_ascii=False))
    stats["requests"] = client.requests
    return stats
