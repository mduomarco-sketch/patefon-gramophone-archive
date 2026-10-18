"""Crawl orchestration: frontier traversal, item building, downloads."""

from __future__ import annotations

import glob
import gzip
import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict
from pathlib import Path

from . import classify, config, paths
from .media import InvalidMedia, sniff_audio, sniff_image, sniff_pdf
from .net import CaptchaRequired, FetchError, PoliteClient, SessionProblem, decode_html
from .parser import (AUDIO_EXT, ItemPage, canonical_url, looks_like_details, notes_fields,
                     page_kind, parse_details, parse_listing, query_param, source_url_hint)
from .state import QUEUE_ALL, QUEUE_AUDIO_FIRST, QUEUE_AUDIO_ONLY, QUEUE_MODES, State
from .tagger import first_year, tag_audio, verify_audio

log = logging.getLogger(__name__)

HALT_EXCEPTIONS = (CaptchaRequired, SessionProblem)


# ---------------------------------------------------------------------------
# Metadata -> DB row
# ---------------------------------------------------------------------------

def _year_from(item: ItemPage) -> str | None:
    if item.fields.get("year"):
        return item.fields["year"]
    m = re.search(r"\((1[89]\d\d|19[0-9]\d)\)", item.title)
    if m:
        return m.group(1)
    m = re.search(r"(?:запис\w*|recorded|record(?:ing)? date|год\w*)\D{0,20}(1[89]\d\d|19\d\d)",
                  item.description, re.I)
    return m.group(1) if m else None


def category_from_path(names: list[str], mode: str | None = None) -> str:
    mode = mode or config.CATEGORY_MODE
    if not names:
        return ""
    if mode == "top":
        return names[0]
    if mode == "leaf" or len(names) == 1 or names[0] == names[-1]:
        return names[-1]
    return f"{names[0]} - {names[-1]}"


def build_record(item: ItemPage, category_names: dict[str, str] | None = None) -> dict:
    category_names = category_names or {}
    path_names = [c.name or category_names.get(c.cat_id or "", "") for c in item.breadcrumbs]
    path_names = [n for n in path_names if n]
    category = category_from_path(path_names)
    f = item.fields
    # download.php is the only media link when the template hides the file URL.
    via_download = bool(item.download_url and not item.audio_urls and not item.image_urls)
    artist = f.get("artist", "")
    title = item.title
    if not artist and title.count(" - ") == 1:
        # "Performer - Title" is a common catalogue convention.
        left, right = (s.strip() for s in title.split(" - "))
        if left and right and len(left) <= 60:
            artist, title = left, right

    ctx = classify.ItemContext(
        title=title, original_title=f.get("original_title", ""), category_path=path_names,
        description=item.description, keywords=item.keywords, language_field=f.get("language", ""),
        artist=artist, accompaniment=f.get("accompaniment", ""), genre=f.get("genre", ""),
        lyricist=f.get("lyricist", ""), has_audio=bool(item.audio_urls) or via_download,
        has_images=bool(item.image_urls), catalog_category=f.get("catalog_category", ""),
        is_document=bool(item.extra.get("has_pdf")))
    media_type = classify.classify_media(ctx)
    lang = classify.classify_language(ctx, media_type)

    raw = {
        "fields": item.fields, "raw_fields": item.raw_fields, "description": item.description,
        "keywords": item.keywords, "breadcrumbs": [asdict(c) for c in item.breadcrumbs],
        "audio_urls": item.audio_urls, "image_urls": item.image_urls,
        "download_url": item.download_url, "page_title": item.page_title,
        "lyricist": f.get("lyricist"), "accompaniment": f.get("accompaniment"),
        "genre": f.get("genre"), "original_title": f.get("original_title"),
        "parse_notes": item.notes, "language_note": lang.note, "extra": item.extra,
    }
    audio_url = item.audio_urls[0] if item.audio_urls else None
    if via_download:
        audio_url = item.download_url  # container type is sniffed after download
    return {
        "item_id": item.image_id,
        "source_url": item.url,
        "audio_url": audio_url,
        "media_type": media_type if audio_url or item.image_urls else classify.NONE,
        "language": lang.language,
        "language_detection_method": lang.method,
        "language_note": lang.note,
        "category": category,
        "category_path": " / ".join(path_names),
        "artist": artist,
        "composer": f.get("composer", ""),
        "title": title,
        "year": _year_from(item) or "",
        "record_label": f.get("label", ""),
        "catalog_number": f.get("catalog", ""),
        "matrix_number": f.get("matrix", ""),
        "label_image_urls": json.dumps(item.image_urls, ensure_ascii=False),
        "pdf_url": (item.extra.get("pdf_urls") or [None])[0],
        "raw_metadata_json": json.dumps(raw, ensure_ascii=False),
    }


def target_path(row, root: Path, ext: str) -> Path:
    s = paths.sanitize_segment
    lang = s(row["language"], classify.UNKNOWN_LANG)
    cat = s(row["category"], "Unknown_Category")
    artist = s(row["artist"], "Unknown_Artist")
    year = first_year(row["year"]) or "Unknown_Year"
    mt = row["media_type"]
    if mt == classify.RADIO:
        folders = [config.BRANCH_RADIO, lang, cat, artist]
    elif mt == classify.PRINT:
        folders = [config.BRANCH_PRINT, lang, cat]
    elif mt == classify.IMAGE:
        folders = [config.BRANCH_IMAGES, lang, cat]
    else:
        folders = [lang, cat, artist]
    return paths.build_path(root, folders, row["title"], year, row["item_id"], ext)


def cache_path(cache_dir: Path, url: str) -> Path:
    kind = page_kind(url) or "other"
    key = re.sub(r"[^A-Za-z0-9_=.-]+", "_", url.split("://", 1)[-1])[:150]
    return Path(cache_dir) / kind / f"{key}.html.gz"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------

class Crawler:
    def __init__(self, client: PoliteClient | None, state: State,
                 archive_root: Path = config.ARCHIVE_ROOT,
                 cache_dir: Path | None = config.PAGE_CACHE_DIR,
                 control_dir: Path | None = config.CONTROL_DIR):
        self.client = client
        self.control_dir = Path(control_dir) if control_dir else None
        self.state = state
        self.root = Path(archive_root)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.pages_fetched = 0
        self.completed_this_run = 0
        self.failed_this_run = 0

    # -- page cache -------------------------------------------------------------
    def _cache(self, url: str, body: bytes):
        if not self.cache_dir:
            return
        p = cache_path(self.cache_dir, url)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(gzip.compress(body))

    # -- main loop ------------------------------------------------------------------
    def seed(self, start_url: str = config.START_URL):
        url = canonical_url(start_url)
        self.state.add_page(url, page_kind(url) or "index")
        self.state.conn.commit()

    def _control(self) -> str:
        """Honour dashboard PAUSE/STOP files. Only checked between steps, so a
        pause never leaves a half-written file."""
        if not self.control_dir:
            return "run"
        stop, pause = self.control_dir / "STOP", self.control_dir / "PAUSE"
        announced = False
        while True:
            if stop.exists():
                log.info("STOP requested - exiting cleanly")
                return "stop"
            if not pause.exists():
                if announced:
                    log.info("resumed")
                return "run"
            if not announced:
                log.info("PAUSED (state saved) - waiting for resume")
                announced = True
            time.sleep(1.0)

    def _next_action(self, queue: str, test_mode: bool, limit: int | None, retry_failed: bool,
                     exclude_items=(), exclude_pages=(), allow_tracks: bool = True):
        """Pick the next unit of work as ("track"|"page", row), or None.

        audio-first: audio downloads -> audio details pages -> category pages
                     -> everything else (label scans, books, periodicals).
        audio-only:  the first three only; the rest stays PENDING for later.
        all / test:  plain discovery order (test mode has its own spreading).
        exclude_*:   work already in flight (parallel mode)."""
        if test_mode:
            row = self.state.next_pending_track(retry_failed=retry_failed)
            if row is not None:
                return "track", row
            page = self.state.next_page(test_mode=test_mode,
                                        category_sample=config.TEST_MODE_CATEGORY_SAMPLE,
                                        want=limit or config.TEST_MODE_LIMIT)
            return ("page", page) if page is not None else None
        if queue == QUEUE_ALL:
            row = allow_tracks and self.state.next_pending_track(retry_failed=retry_failed, exclude=exclude_items)
            if row:
                return "track", row
            page = self.state.next_any_page(exclude=exclude_pages)
            return ("page", page) if page is not None else None
        row = allow_tracks and self.state.next_pending_track(retry_failed=retry_failed, audio_only=True,
                                                             exclude=exclude_items)
        if row:
            return "track", row
        page = self.state.next_audio_page(exclude=exclude_pages)
        if page is not None:
            return "page", page
        if queue == QUEUE_AUDIO_ONLY:
            return None
        row = allow_tracks and self.state.next_pending_track(retry_failed=retry_failed, exclude=exclude_items)
        if row:
            return "track", row
        page = self.state.next_other_page(exclude=exclude_pages)
        return ("page", page) if page is not None else None

    def _pick_track(self, queue: str, retry_failed: bool, busy_items, busy_pages):
        """Media lane: the next download, same priorities as _next_action."""
        if queue == QUEUE_ALL:
            return self.state.next_pending_track(retry_failed=retry_failed, exclude=busy_items)
        row = self.state.next_pending_track(retry_failed=retry_failed, audio_only=True, exclude=busy_items)
        if row is not None or queue == QUEUE_AUDIO_ONLY:
            return row
        # audio-first: items without audio only once no audio pages remain
        if self.state.next_audio_page(exclude=busy_pages) is not None:
            return None
        return self.state.next_pending_track(retry_failed=retry_failed, exclude=busy_items)

    def _pick_page(self, queue: str, busy_pages):
        """HTML lane: the next page to fetch, same priorities as _next_action."""
        if queue == QUEUE_ALL:
            return self.state.next_any_page(exclude=busy_pages)
        page = self.state.next_audio_page(exclude=busy_pages)
        if page is not None or queue == QUEUE_AUDIO_ONLY:
            return page
        return self.state.next_other_page(exclude=busy_pages)

    def run_parallel(self, client_factory, limiter, workers: int, limit: int | None = None,
                     retry_failed: bool = False, queue: str = QUEUE_AUDIO_FIRST):
        """run() with page fetches and downloads spread over worker threads.

        Same queue rules, limit and PAUSE/STOP handling as run(). Each worker has
        its own HTTP session and database connection; all share `limiter`, which
        spaces request starts site-wide and sheds workers at any sign of strain
        (see net.RateLimiter). A CAPTCHA or dead session in any worker stops the
        whole run: queued jobs are cancelled, running ones finish their item."""
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

        if queue not in QUEUE_MODES:
            raise ValueError(f"queue must be one of {QUEUE_MODES}")
        if limit == 0:
            limit = None
        if retry_failed:
            self.state.requeue_failed_pages()
        log.info("parallel mode: up to %d workers (starting with %d), queue %s, limit %s",
                 workers, limiter.allowed, queue, limit or "unlimited")
        tls = threading.local()

        def job(kind: str, row: dict):
            if not hasattr(tls, "crawler"):
                tls.crawler = Crawler(client_factory(), State(self.state.path), self.root,
                                      cache_dir=self.cache_dir, control_dir=None)
            c = tls.crawler
            before = (c.completed_this_run, c.failed_this_run, c.pages_fetched)
            if kind == "track":
                c.process_track(row)
            else:
                c.process_page(row)
            return (c.completed_this_run - before[0], c.failed_this_run - before[1], c.pages_fetched - before[2])

        inflight: dict = {}   # future -> (kind, key)
        halt = None

        def harvest(futures):
            nonlocal halt
            for f in futures:
                kind, key = inflight.pop(f)
                if f.cancelled():
                    continue
                try:
                    done, failed, pages = f.result()
                except HALT_EXCEPTIONS as exc:
                    halt = halt or exc
                except Exception as exc:  # a bug in one job must not kill the run
                    log.exception("worker %s job %s crashed: %s", kind, key, exc)
                else:
                    self.completed_this_run += done
                    self.failed_this_run += failed
                    self.pages_fetched += pages

        # Two lanes sharing one limiter: binary media (audio, scans, PDFs) stream on
        # `workers` threads while a separate HTML lane keeps feeding the queue, so a
        # long FLAC/PDF transfer never stalls discovery of the next items.
        page_workers = max(1, workers // config.PARALLEL_PAGE_WORKERS_DIV)
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="media")
        page_pool = ThreadPoolExecutor(max_workers=page_workers, thread_name_prefix="pages")

        def busy(kind):
            return tuple(k for kd, k in inflight.values() if kd == kind)
        try:
            while True:
                harvest([f for f in list(inflight) if f.done()])
                if halt is not None:
                    break
                if self._control() == "stop":
                    break
                if limit is not None and self.completed_this_run >= limit:
                    log.info("limit of %d downloads reached", limit)
                    break
                capacity = min(workers, limiter.allowed)
                page_cap = max(1, min(page_workers, capacity // config.PARALLEL_PAGE_WORKERS_DIV or 1))
                while len(busy("track")) < capacity:
                    if limit is not None and self.completed_this_run + len(busy("track")) >= limit:
                        break
                    row = self._pick_track(queue, retry_failed, busy("track"), busy("page"))
                    if row is None:
                        break
                    inflight[pool.submit(job, "track", dict(row))] = ("track", row["item_id"])
                while len(busy("page")) < page_cap:
                    page = self._pick_page(queue, busy("page"))
                    if page is None:
                        break
                    inflight[page_pool.submit(job, "page", dict(page))] = ("page", page["url"])
                if not inflight:
                    if queue == QUEUE_AUDIO_ONLY:
                        log.info("audio-only: all audio done for now; deferred: %s", self.state.deferred_counts())
                    else:
                        log.info("frontier exhausted")
                    break
                wait(list(inflight), timeout=1.0, return_when=FIRST_COMPLETED)
        finally:
            for f in list(inflight):
                f.cancel()
            pool.shutdown(wait=True)   # running jobs finish their current item
            page_pool.shutdown(wait=True)
            harvest(list(inflight))
        if halt is not None:
            raise halt

    def run(self, limit: int | None = None, test_mode: bool = False,
            page_budget: int | None = None, retry_failed: bool = False,
            queue: str = QUEUE_AUDIO_FIRST):
        if self.client is None:
            raise RuntimeError("network crawl needs a session (use --cookie-file/--session-id)")
        if queue not in QUEUE_MODES:
            raise ValueError(f"queue must be one of {QUEUE_MODES}")
        if limit == 0:
            limit = None  # 0 = unlimited
        if retry_failed:
            self.state.requeue_failed_pages()
        if not test_mode:
            log.info("queue policy: %s, limit: %s", queue, limit or "unlimited")
        while True:
            if self._control() == "stop":
                break
            if limit is not None and self.completed_this_run >= limit:
                log.info("limit of %d downloads reached", limit)
                break
            action = self._next_action(queue, test_mode, limit, retry_failed)
            if action is None:
                if queue == QUEUE_AUDIO_ONLY and not test_mode:
                    log.info("audio-only: all audio done for now; deferred: %s",
                             self.state.deferred_counts())
                else:
                    log.info("frontier exhausted")
                break
            kind, row = action
            if kind == "track":
                self.process_track(row)
                continue
            if page_budget is not None and self.pages_fetched >= page_budget:
                log.info("page budget of %d reached", page_budget)
                break
            self.process_page(row)

    def process_page(self, page):
        url, kind = page["url"], page["kind"]
        try:
            final_url, body, html = self.client.fetch_html(url)
        except HALT_EXCEPTIONS:
            raise
        except FetchError as exc:
            log.warning("page failed %s: %s", url, exc)
            self.state.mark_page(url, "FAILED", str(exc))
            return
        self.pages_fetched += 1
        self._cache(url, body)
        if kind == "details":
            self.ingest_details(html, url, parent_cat=page["parent_cat"])
        else:
            self.ingest_listing(html, url)
        self.state.mark_page(url, "DONE")

    def ingest_listing(self, html: str, url: str):
        listing = parse_listing(html, url)
        names = [c.name for c in listing.breadcrumbs]
        if listing.cat_id:
            parent = listing.breadcrumbs[-2].cat_id if len(listing.breadcrumbs) >= 2 else None
            if listing.breadcrumbs and listing.breadcrumbs[-1].cat_id == listing.cat_id:
                path = names
            else:
                path = names + [listing.name]
            self.state.upsert_category(listing.cat_id, listing.name or (names[-1] if names else ""),
                                       parent, path)
        new = 0
        for cid, name, curl in listing.subcategories:
            self.state.upsert_category(cid, name, listing.cat_id, [])
            new += self.state.add_page(curl, "category", listing.cat_id)
        for purl in listing.pages:
            new += self.state.add_page(purl, "category", listing.cat_id)
        for iid, _name, iurl in listing.items:
            new += self.state.add_page(iurl, "details", listing.cat_id,
                                       "audio" if iid in listing.audio_items else None)
        self.state.conn.commit()
        log.info("listing %s '%s': %d subcats, %d items, %d pages (+%d new)", url, listing.name,
                 len(listing.subcategories), len(listing.items), len(listing.pages), new)
        return listing

    def ingest_details(self, html: str, url: str | None, parent_cat: str | None = None):
        item = parse_details(html, url)
        if not item.image_id:
            log.warning("details page without image_id: %s", url)
            return None
        cat_names = {}
        for c in item.breadcrumbs:
            if c.cat_id and not c.name:
                cat_names[c.cat_id] = self.state.category_name(c.cat_id) or ""
        record = build_record(item, cat_names)
        status = "PENDING" if record["media_type"] != classify.NONE else "SKIPPED"
        result = self.state.upsert_track(record["item_id"], record, status)
        if status == "SKIPPED" and result != "COMPLETED":
            self.state.mark_skipped(record["item_id"], "no audio or image media found on details page")
        for link in item.linked_items:
            self.state.add_page(link, "details", item.cat_id or parent_cat)
        self.state.conn.commit()
        log.info("item %s [%s/%s] %s | %s (%s)", item.image_id, record["media_type"],
                 record["language"], record["artist"] or "?", record["title"],
                 record["language_detection_method"])
        return record

    # -- downloads -------------------------------------------------------------------
    def process_track(self, row):
        item_id = row["item_id"]
        pdf = None
        try:
            if row["media_type"] in (classify.MUSIC, classify.RADIO):
                final, sha, size, labels = self._download_audio(row)
            elif row["media_type"] in (classify.PRINT, classify.IMAGE):
                final, sha, size, labels, pdf = self._download_print(row)
            else:
                self.state.mark_skipped(item_id, "no media")
                return
        except HALT_EXCEPTIONS:
            raise
        except (FetchError, InvalidMedia, ValueError, OSError) as exc:
            log.error("item %s failed: %s", item_id, exc)
            self.state.mark_failed(item_id, f"{type(exc).__name__}: {exc}")
            self.failed_this_run += 1
            return
        self.state.mark_completed(item_id, str(final), sha, size, [str(p) for p in labels],
                                  str(pdf) if pdf else None)
        self.completed_this_run += 1
        log.info("COMPLETED %s -> %s", item_id, final)

    def _fetch_validated_image(self, url: str, dest_stem: Path) -> tuple[Path, bytes, str]:
        part = dest_stem.with_name(dest_stem.name + ".img.part")
        self.client.download(url, part)
        try:
            ext, mime = sniff_image(part)
        except InvalidMedia:
            part.unlink(missing_ok=True)
            raise
        return part, part.read_bytes(), mime

    def _download_labels(self, row, main: Path, label_word: str) -> tuple[list[Path], tuple | None]:
        urls = json.loads(row["label_image_urls"] or "[]")
        saved, first = [], None
        for i, url in enumerate(urls):
            try:
                part, data, mime = self._fetch_validated_image(url, main.with_suffix(""))
            except HALT_EXCEPTIONS:
                raise
            except (FetchError, InvalidMedia, OSError) as exc:
                log.warning("label image %s failed (non-fatal): %s", url, exc)
                continue
            ext = sniff_image(part)[0]
            dest = paths.sidecar_path(main, label_word, len(saved), ext)
            os.replace(part, dest)
            saved.append(dest)
            if first is None:
                first = (data, mime)
        return saved, first

    def _download_audio(self, row):
        url = row["audio_url"]
        guess_ext = next((e for e in AUDIO_EXT if url.lower().split("?")[0].endswith(e)), ".mp3")
        final = target_path(row, self.root, guess_ext)
        if final.exists():
            # Crash between rename and DB update: adopt the verified file.
            sniff_audio(final)
            log.info("adopting existing file %s", final)
            labels = sorted(final.parent.glob(glob.escape(f"{final.stem} (label") + "*"))
            return final, _sha256(final), final.stat().st_size, labels
        final.parent.mkdir(parents=True, exist_ok=True)
        part = final.with_name(final.name + ".part")
        part.unlink(missing_ok=True)
        try:
            self.client.download(url, part)
            ext = sniff_audio(part)
            if ext != guess_ext:
                final = final.with_suffix(ext)
                part_new = final.with_name(final.name + ".part")
                os.replace(part, part_new)
                part = part_new
            labels, first_label = self._download_labels(row, final, "label")
            if tag_audio(part, ext, self._tag_meta(row), first_label):
                verify_audio(part, ext)
            else:
                log.warning("item %s is %s; only MP3 (ID3v2.4) and FLAC (Vorbis) are tagged",
                            row["item_id"], ext)
            os.replace(part, final)
        finally:
            part.unlink(missing_ok=True)
        return final, _sha256(final), final.stat().st_size, labels

    def _download_print(self, row):
        """Cover/page images, then the PDF scan if the item has one. Parts
        already on disk are adopted, so a retry only fetches what is missing
        (e.g. the PDF of an item downloaded before PDF support existed)."""
        urls = json.loads(row["label_image_urls"] or "[]")
        if not urls:
            raise ValueError("print/image item without image URLs")
        stub = target_path(row, self.root, ".jpg")
        stub.parent.mkdir(parents=True, exist_ok=True)
        existing = [p for p in stub.parent.glob(glob.escape(stub.stem) + "*")
                    if p.suffix.lower() not in (".part", ".pdf", ".bak")]
        # Main file ("stem.jpg") first, then "(page N)" sidecars.
        existing.sort(key=lambda p: (p.stem != stub.stem, p.name))
        if existing:
            saved = existing
        else:
            saved = []
            for i, url in enumerate(urls):
                part, _data, _mime = self._fetch_validated_image(url, stub.with_suffix(""))
                ext = sniff_image(part)[0]
                dest = stub.with_suffix(ext) if i == 0 else paths.sidecar_path(stub, "page", i, ext)
                os.replace(part, dest)
                saved.append(dest)
        pdf = self._download_pdf(row, stub.with_suffix(".pdf")) if row["pdf_url"] else None
        return saved[0], _sha256(saved[0]), saved[0].stat().st_size, saved[1:], pdf

    def _download_pdf(self, row, dest: Path) -> Path:
        if dest.exists():
            try:
                sniff_pdf(dest)
                return dest
            except InvalidMedia:
                log.warning("re-downloading invalid PDF %s", dest)
        part = dest.with_name(dest.name + ".part")
        part.unlink(missing_ok=True)
        try:
            self.client.download(row["pdf_url"], part)
            sniff_pdf(part)
            os.replace(part, dest)
        finally:
            part.unlink(missing_ok=True)
        log.info("PDF saved %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)
        return dest

    @staticmethod
    def _tag_meta(row) -> dict:
        raw = json.loads(row["raw_metadata_json"] or "{}")
        notes = []
        if raw.get("description"):
            notes.append(raw["description"])
        extra = [f"{k}: {v}" for k, v in notes_fields(raw.get("raw_fields") or {})]
        if extra:
            notes.append("\n".join(extra))
        notes.append(f"Language: {row['language']} ({row['language_detection_method']}: "
                     f"{row['language_note']})")
        publisher = ", ".join(x for x in (row["record_label"],
                                          f"cat. {row['catalog_number']}" if row["catalog_number"] else "")
                              if x)
        return {
            "title": row["title"], "artist": row["artist"], "composer": row["composer"],
            "lyricist": raw.get("lyricist"), "album": row["category_path"] or row["category"],
            "genre": raw.get("genre") or row["category"], "year": row["year"],
            "language_iso": classify.ISO639_2.get(row["language"], "und"),
            "language": row["language"], "language_method": row["language_detection_method"],
            "publisher": publisher, "catalog_number": row["catalog_number"],
            "matrix_number": row["matrix_number"], "source_url": row["source_url"],
            "notes": "\n\n".join(notes), "item_id": row["item_id"],
            "category_path": row["category_path"],
        }


# ---------------------------------------------------------------------------
# Local HTML ingest (pages saved from a browser session)
# ---------------------------------------------------------------------------

def ingest_local_dir(crawler: Crawler, directory: Path) -> dict:
    stats = {"details": 0, "listing": 0, "skipped": 0}
    files = sorted(p for p in Path(directory).rglob("*") if p.suffix.lower() in (".html", ".htm"))
    for p in files:
        body = p.read_bytes()
        html = decode_html(body)
        hint = source_url_hint(html)
        url = canonical_url(hint) if hint else None
        kind = page_kind(url) if url else None
        if kind == "details" or (kind is None and looks_like_details(html)):
            if crawler.ingest_details(html, url):
                stats["details"] += 1
                if url:
                    crawler.state.add_page(url, "details")
                    crawler.state.mark_page(url, "DONE")
        elif kind in ("category", "index") or "categories.php?cat_id=" in html:
            crawler.ingest_listing(html, url or config.START_URL)
            if url:
                crawler.state.add_page(url, kind or "category")
                crawler.state.mark_page(url, "DONE")
            stats["listing"] += 1
        else:
            stats["skipped"] += 1
            log.info("local file not recognised as a 4images page: %s", p)
    crawler.state.conn.commit()
    return stats
