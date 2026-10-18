"""russian-records.com archiver CLI.

Authentication is always manual: solve the verification code in your own
browser, then hand the session to this script with --cookie-file or
--session-id. If the server ever redirects to get_captcha.php the run stops
immediately (exit code 3) and asks you to refresh the session.

Examples:
  python scraper.py --recon --cookie-file cookies.txt
  python scraper.py --recon --local-html-dir saved_pages
  python scraper.py --test-mode --cookie-file cookies.txt          # 5 items
  python scraper.py --resume --cookie-file cookies.txt             # full crawl
  python scraper.py --local-html-dir saved_pages                   # ingest only
  python scraper.py --status
  python scraper.py --verify
  python scraper.py --export-html                                  # static catalog
  python scraper.py --refile-cached                                # after parser fixes
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from rr_archive import config
from rr_archive.crawler import Crawler, ingest_local_dir
from rr_archive.export import export_catalog
from rr_archive.media import InvalidMedia, sniff_audio, sniff_image, sniff_pdf
from rr_archive.net import CaptchaRequired, PoliteClient, SessionProblem, load_cookies
from rr_archive.recon import recon_local, recon_online
from rr_archive.state import QUEUE_AUDIO_FIRST, QUEUE_MODES, State

EXIT_CAPTCHA = 3
EXIT_SESSION = 4

HALT_MESSAGE = """
============================================================================
 SESSION NEEDS A HUMAN: the server redirected to the verification page
 ({where}).

 Nothing was bypassed and progress is saved in {db}.
 To continue:
   1. Open https://www.russian-records.com/ in your browser and solve the code.
   2. Export fresh cookies (cookies.txt / JSON) or copy the 'sessionid' value.
   3. Re-run the same command with --cookie-file / --session-id.
============================================================================
"""


def setup_logging(verbose: bool):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    handlers = [logging.StreamHandler(sys.stdout),
                logging.FileHandler(config.LOG_PATH, encoding="utf-8")]
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
                        handlers=handlers)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def make_client(args) -> PoliteClient | None:
    if not (args.cookie_file or args.session_id or args.cookie):
        return None
    jar = load_cookies(args.cookie_file, args.session_id, args.cookie)
    if not jar:
        sys.exit("No russian-records.com cookies found in the supplied input.")
    return PoliteClient(cookies=jar, user_agent=args.user_agent,
                        min_delay=args.min_delay, max_delay=args.max_delay)


# ---------------------------------------------------------------------------
# Status / verify
# ---------------------------------------------------------------------------

def print_status(state: State):
    print(f"pages: {state.page_count()} total, {state.page_count('DONE')} done, "
          f"{state.page_count('PENDING')} pending, {state.page_count('FAILED')} failed")
    print(f"{'media_type':<14}{'language':<20}{'status':<11}{'count':>7}")
    for r in state.summary():
        print(f"{str(r['media_type']):<14}{str(r['language']):<20}{r['status']:<11}{r['n']:>7}")


def verify(state: State) -> int:
    bad = 0
    for r in state.completed_rows():
        p = Path(r["local_filepath"] or "")
        try:
            if not p.exists():
                raise InvalidMedia("missing on disk")
            if r["media_type"] in ("MUSIC", "RADIO_SPEECH"):
                sniff_audio(p)
            else:
                sniff_image(p)
            if r["pdf_local_path"]:
                pdf = Path(r["pdf_local_path"])
                if not pdf.exists():
                    raise InvalidMedia(f"PDF missing on disk ({pdf.name})")
                sniff_pdf(pdf)
        except InvalidMedia as exc:
            bad += 1
            print(f"BAD {r['item_id']}: {exc} ({p})")
            state.mark_failed(r["item_id"], f"verify: {exc}")
    print(f"verified {len(state.completed_rows()) + bad} files, {bad} problems (re-queued as FAILED)")
    return bad


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_argument_group("modes")
    mode.add_argument("--recon", action="store_true", help="inspect a few pages and print parse results")
    mode.add_argument("--test-mode", action="store_true",
                      help=f"download only {config.TEST_MODE_LIMIT} items spread across categories")
    mode.add_argument("--resume", action="store_true", help="full resumable crawl")
    mode.add_argument("--status", action="store_true", help="print progress by media type/language")
    mode.add_argument("--verify", action="store_true", help="re-check every COMPLETED file on disk")
    mode.add_argument("--refile-cached", action="store_true",
                      help="re-parse cached pages with the current parser and re-file COMPLETED items")
    mode.add_argument("--export-html", action="store_true",
                      help="write a self-contained russian_records_archive/index.html catalog")
    mode.add_argument("--build-gift", action="store_true",
                      help="build a compacted, standalone copy in ~/Desktop/patefon_gift from COMPLETED "
                           "items (reads the master archive read-only; incremental and re-runnable)")
    mode.add_argument("--seed-demo", action="store_true",
                      help="generate a tiny synthetic ./demo_archive (5 items, no cookies, no network) so "
                           "anyone can open the Patefon UI immediately")
    ap.add_argument("--playlists", type=Path,
                    help="with --export-html: a Patefon backup (.json from 'Back up library') to bake into "
                         "the gift; it is also saved as user_playlists.json for later exports")
    gift = ap.add_argument_group("gift builder (--build-gift)")
    gift.add_argument("--gift-dest", type=Path, default=None, help="destination folder (default ~/Desktop/patefon_gift)")
    gift.add_argument("--gift-workers", type=int, default=None, help="parallel workers (default cpu_count-2)")
    gift.add_argument("--gift-skip-pdfs", action="store_true", help="do not copy PDF scans")
    gift.add_argument("--gift-pdf-max", type=int, default=15, help="copy PDFs up to this many MB (default 15)")
    gift.add_argument("--gift-limit", type=int, default=None, help="process at most N new items this run")
    auth = ap.add_argument_group("session (human-authenticated)")
    auth.add_argument("--cookie-file", help="cookies.txt (Netscape), JSON export, or raw Cookie header file")
    auth.add_argument("--session-id", help=f"value of the '{config.SESSION_COOKIE_NAME}' cookie")
    auth.add_argument("--cookie", help="raw cookie header, e.g. 'sessionid=...; 4images_userid=...'")
    auth.add_argument("--user-agent", default=config.DEFAULT_USER_AGENT,
                      help="set to your browser's UA if the session is bound to it")
    ap.add_argument("--local-html-dir", type=Path, help="ingest/recon pages saved from your browser")
    ap.add_argument("--limit", type=int, help="stop after N completed downloads (0 = unlimited)")
    ap.add_argument("--queue", choices=QUEUE_MODES, default=QUEUE_AUDIO_FIRST,
                    help="audio-first (default): items with audio before label scans/books; "
                         "audio-only: skip items without audio (left PENDING for later); "
                         "all: plain discovery order")
    ap.add_argument("--retry-failed", action="store_true", help="retry FAILED items and pages")
    ap.add_argument("--start-url", default=config.START_URL)
    ap.add_argument("--archive-root", type=Path, default=config.ARCHIVE_ROOT)
    ap.add_argument("--db", type=Path, default=config.DB_PATH)
    ap.add_argument("--min-delay", type=float, default=config.DEFAULT_MIN_DELAY)
    ap.add_argument("--max-delay", type=float, default=config.DEFAULT_MAX_DELAY)
    ap.add_argument("--workers", type=int, default=1,
                    help=f"parallel downloads (default 1 = polite single-threaded; up to "
                         f"{config.PARALLEL_MAX_WORKERS} ONLY with the site operator's permission). In parallel "
                         "mode strain cuts workers 25%% for 15 s, then they ramp back")
    ap.add_argument("--min-interval", type=float, default=config.PARALLEL_MIN_INTERVAL,
                    help="parallel mode: minimum seconds between request starts site-wide "
                         "(default 0 = no pacing while healthy)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    setup_logging(args.verbose)
    if args.min_delay < config.MIN_DELAY_FLOOR:
        ap.error(f"--min-delay below {config.MIN_DELAY_FLOOR}s violates the maintainer's terms")
    if not 1 <= args.workers <= config.PARALLEL_MAX_WORKERS:
        ap.error(f"--workers must be between 1 and {config.PARALLEL_MAX_WORKERS}")
    if args.min_interval < config.PARALLEL_MIN_INTERVAL_FLOOR:
        ap.error(f"--min-interval below {config.PARALLEL_MIN_INTERVAL_FLOOR}s is not allowed")

    if args.recon:
        if args.local_html_dir:
            recon_local(args.local_html_dir, args.archive_root)
            return 0
        client = make_client(args)
        if client is None:
            ap.error("--recon needs --cookie-file/--session-id/--cookie or --local-html-dir")
        try:
            recon_online(client, args.archive_root)
        except CaptchaRequired as exc:
            print(HALT_MESSAGE.format(where=exc, db="(recon writes no state)"))
            return EXIT_CAPTCHA
        return 0

    state = State(args.db)
    try:
        if args.status:
            print_status(state)
            return 0
        if args.verify:
            return 1 if verify(state) else 0
        if args.refile_cached:
            from rr_archive.reclassify import refile_from_cache
            stats = refile_from_cache(state, args.archive_root)
            print(f"refile: {stats}")
            print_status(state)
            return 1 if stats["errors"] else 0
        if args.export_html:
            from rr_archive.export import clean_seed, load_user_playlists, save_user_playlists
            if args.playlists:
                seed = clean_seed(json.loads(args.playlists.read_text(encoding="utf-8")))
                if not seed:
                    ap.error(f"{args.playlists} has no playlists or liked songs")
                save_user_playlists(seed)
            out = export_catalog(state, args.archive_root)
            seed = load_user_playlists()
            print(f"static catalog written: {out}")
            if seed:
                print(f"  baked in: {len(seed['playlists'])} playlists, {len(seed['liked'])} liked songs")
            return 0
        if args.seed_demo:
            from rr_archive.demo import seed_demo
            info = seed_demo()
            print(f"demo archive built: {info['items']} items")
            print(f"open this file in a browser:\n  {info['index']}")
            return 0
        if args.build_gift:
            from rr_archive.gift_builder import DEST_ROOT, build_gift, human_gb

            dest = args.gift_dest or DEST_ROOT

            def show(s):
                sg, dg = human_gb(s["src_bytes"]), human_gb(s["dst_bytes"])
                saved = (1 - s["dst_bytes"] / s["src_bytes"]) * 100 if s["src_bytes"] else 0
                print(f"\r[Synced {s['synced']}/{s['total']} items | Source {sg} GB -> Gift {dg} GB "
                      f"| Saved {saved:.1f}% | failed {s['failed']}]", end="", flush=True)

            stats = build_gift(source_db=args.db, source_archive=args.archive_root, dest_root=dest,
                               workers=args.gift_workers, skip_pdfs=args.gift_skip_pdfs,
                               pdf_max_mb=args.gift_pdf_max, limit=args.gift_limit, progress=show)
            saved = (1 - stats["dst_bytes"] / stats["src_bytes"]) * 100 if stats["src_bytes"] else 0
            print(f"\ngift built at {dest}")
            print(f"  synced {stats['synced']} new, skipped {stats['skipped_existing']} already done, "
                  f"{stats['failed']} failed; {human_gb(stats['src_bytes'])} GB -> "
                  f"{human_gb(stats['dst_bytes'])} GB ({saved:.1f}% smaller), {stats['workers']} workers")
            return 1 if stats["failed"] else 0

        client = make_client(args)
        crawler = Crawler(client, state, args.archive_root)

        if args.local_html_dir:
            stats = ingest_local_dir(crawler, args.local_html_dir)
            print(f"local ingest: {stats}")
            if client is None:
                print("No session supplied: metadata recorded as PENDING; "
                      "re-run with --cookie-file to download media.")
                print_status(state)
                return 0

        if not (args.test_mode or args.resume or args.local_html_dir):
            ap.error("choose a mode: --recon, --test-mode, --resume, --status, --verify or --local-html-dir")
        if client is None:
            ap.error("downloading needs a human-authenticated session: --cookie-file, --session-id or --cookie")

        crawler.seed(args.start_url)
        if args.test_mode:
            limit = args.limit or config.TEST_MODE_LIMIT
            crawler.run(limit=limit, test_mode=True, page_budget=config.TEST_MODE_PAGE_BUDGET,
                        retry_failed=args.retry_failed)
        elif args.workers > 1:
            from rr_archive.net import RateLimiter
            limiter = RateLimiter(args.min_interval, max(args.min_interval, config.PARALLEL_MAX_INTERVAL),
                                  max_workers=args.workers)   # starts at full speed; health decides
            jar = load_cookies(args.cookie_file, args.session_id, args.cookie)
            clients = []

            def client_factory():
                c = PoliteClient(cookies=jar, user_agent=args.user_agent, limiter=limiter)
                clients.append(c)
                return c
            crawler.run_parallel(client_factory, limiter, args.workers, limit=args.limit or None,
                                 retry_failed=args.retry_failed, queue=args.queue)
            client.request_count += sum(c.request_count for c in clients)
            print(f"parallel: {limiter.trouble_events} slow-downs; ended {limiter.state} at "
                  f"{limiter.allowed}/{args.workers} workers")
        else:
            crawler.run(limit=args.limit or None, retry_failed=args.retry_failed, queue=args.queue)
        print(f"\nrun finished: {crawler.completed_this_run} completed, {crawler.failed_this_run} failed, "
              f"{crawler.pages_fetched} pages, {client.request_count} HTTP requests")
        print_status(state)
        return 0
    except CaptchaRequired as exc:
        print(HALT_MESSAGE.format(where=exc, db=args.db))
        return EXIT_CAPTCHA
    except SessionProblem as exc:
        print(HALT_MESSAGE.format(where=f"session rejected: {exc}", db=args.db))
        return EXIT_SESSION
    except KeyboardInterrupt:
        print("\nInterrupted - state saved; any .part file will be redone on the next run.")
        return 130
    finally:
        state.close()


if __name__ == "__main__":
    sys.exit(main())
