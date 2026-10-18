# Project Context: Russian Records Audio Archive (`russian-records.com`)

## 1. Project Overview & Objective

This workspace is dedicated to building and running a fault-tolerant, resumable web scraping pipeline that archives historical audio recordings, label scans, print scans and metadata from `https://www.russian-records.com/index.php`, a **4images** gallery (legacy PHP; *not* Coppermine). Pages: `index.php`, `categories.php?cat_id=N[&page=M]`, `details.php?image_id=N`; media under `data/media/<cat_id>/`, thumbnails under `data/thumbnails/<cat_id>/`, and `download.php?image_id=N`. The site serves `windows-1251`.

Access is authorized by the archive maintainer for academic/archival research on these conditions: a polite single-threaded rate limit (2–4 s between requests), and sessions authenticated **manually by a human**.

### Verified live DOM (recon 2026-10-17, pages kept in `_recon/`, regression-tested in `tests/test_recon_pages.py`)

* **Breadcrumbs:** `span.clickstream`, separated by `>`. Deep levels read `. .` and carry the real name in `title=""`. The current page's name is the trailing text.
* **Title:** the first `font.section-title` is the bilingual title, `English (Original-script), form`. The `Title Name`/`Name` row holds the short form.
* **Info rows:** `<td><b>Label:</b></td><td>value</td>`, sometimes with a third cell for the reverse-side link. Several sub-fields can share one cell: `russian | <b>Catalog category:</b> Orchestra | <b>Genre (Music Category):</b> Waltz`.
* **Field labels:** `Language(s) or Ethnics`, `Artist(s)`, `Composer`, `Lyrics By`, `Accompaniment Type`, `Bandmaster or conductor`, `Recording Place` / `Recording Date`, `Speed of transfer`, `Record size` / `Label size`, `Label(s)`, `Additional information`, `Additional keywords`, `Mirror Transcription`.
* **Catalog table:** headers `Label | Catalog No | Mx/Ctr No | Take | Order No | Censorial No | Additional information`. The first data row is this side, the second the reverse side.
* **Label transcription:** an unlabelled first row of the info table. It holds the text printed on the label, multilingual and in the original spelling, and feeds language detection through `original_title`.
* **Media:** audio is `<audio src="./data/media/<cat>/<file>.mp3|.flac">`. The full-size label scan is `data/media/<cat>/<file>.jpg`. Ignore thumbnails (`data/thumbnails`, different names), `data/userpic/`, `*_qr.png`, and nav-strip thumbnails or `data-src` players that link to other items. Books and periodicals have a PDF (`view_pdf.php`), which makes them `PRINT`. The PDF itself is not downloaded yet.
* **Listings:** rows are `tr.imagerow1`. The title is only in `onmouseover="shownote('Title (uploader)', ...)"`, and an audio item has a `data-src="...mp3"` play button, recorded as `pages.hint='audio'` so test mode prefers real tracks. No pagination links were seen; generic `&page=N` is still supported.
* **"Language(s) or Ethnics" is an ethnicity/origin tag**, not strictly the sung language. Instrumentals carry values like `italian` or `polish, american`, so the site's own Catalog category/Genre is checked **before** this field (see tier 0 below). Multi-value fields use the first listed language.

## 2. Mandatory Agent Workflow (Read Before Executing)

1. **Reconnaissance First:** Never guess DOM selectors or URL structures. Run `scraper.py --recon` (online with a session, or `--local-html-dir` on pages saved from the browser) and check its output (breadcrumbs, info-table labels, audio/label URLs, path preview) before any batch run. The selectors in `rr_archive/parser.py` follow the stock 4images templates and **must be verified against real pages**.

2. **Test Mode Gate (`--test-mode`):** Before any full crawl, run with `--test-mode` (capped at **5 items**, spread across categories, 80-page budget). Then verify:
   * the files are valid binary media (magic bytes checked; never HTML error pages);
   * the directory structure matches section 3, including the `{Language}` level;
   * ID3v2.4 tags (including `TLAN`) and `archive_state.db` are populated correctly.

3. **Idempotency & Resumability:** Every run consults `archive_state.db`. `COMPLETED` items are never re-downloaded or modified by a re-crawl.

4. **CAPTCHA / Authentication (hard rule):** The site gates every page behind `get_captcha.php`.
   * **Never** write CAPTCHA-solving, OCR or bypass code, and never request `captcha.php` or `get_captcha.php`.
   * The user solves the code in their own browser and passes the session with `--cookie-file` (Netscape cookies.txt, JSON export or raw header), `--session-id` (the 4images `sessionid` cookie) or `--cookie`. They can also save pages and use `--local-html-dir`.
   * On any redirect to, or page containing, the verification form, the run **halts immediately** (exit code 3), with state saved, and asks the user to refresh the session. Three consecutive HTTP 403 responses also halt (exit code 4).

## 3. Directory Hierarchy, Language Classification & File Naming Rules

* **Root Archive Directory:** `./russian_records_archive/`

* **Folder Taxonomy (by media type):**

  ```
  Music:            {Language}/{Category}/{Artist_or_Performer}/{Year_or_Unknown} - {Track_Title} [{Item_ID}].mp3
  Radio & speech:   Radio_and_Speech/{Language}/{Category}/{Artist_or_Performer}/{Year} - {Title} [{Item_ID}].mp3
  Newspapers/print: Newspapers_and_Print/{Language}/{Category}/{Year} - {Title} [{Item_ID}].jpg   (+ " (page 2).jpg" ...)
                    + same stem ".pdf" when the item has a PDF scan (view_pdf.php)
  Other images:     Images_and_Scans/{Language}/{Category}/{Year} - {Title} [{Item_ID}].jpg
  Label scans:      next to the audio file as "{same stem} (label).jpg" and embedded as APIC
  ```

  *(Fallbacks: `Unknown_Language`, `Unknown_Category`, `Unknown_Artist`, `Unknown_Year`.)* `[{Item_ID}]` is the 4images `image_id`, so names stay unique. The catalog number goes in the tags and the DB.

* **`{Category}` naming** (`CATEGORY_MODE` in `rr_archive/config.py`): the default `"top_leaf"` gives `TopCategory - DeepestSubcategory`, or just the category when there is only one level (e.g. `Песни - Шаляпин Ф.И.`). Middle levels are kept in `category_path` and `TALB`. The alternatives are `"leaf"` (deepest only) and `"top"`.

* **Media-type detection:** audio plus a speech/radio category or title → `Radio_and_Speech`; any other audio → music; images only plus a print category/title (newspaper, magazine, poster, catalogue, sheet music...) → `Newspapers_and_Print`.
  * **`Images_and_Scans/`** (kept by design): image-only items that are not print, such as photos, sleeves and loose label scans without audio.
  * **`SKIPPED`** status (kept by design): details pages with no downloadable media. They are recorded so the crawl never revisits them, and they are never retried.

* **Multi-Tiered Language Classification (`rr_archive/classify.py`), in priority order:**
  0. **Site instrumental** (`INSTRUMENTAL`, music only): the site's Catalog category/Genre names an instrument, orchestra, solo or chamber piece, and there are no vocal markers. This tier exists because the ethnicity field must not label a violin solo "Italian".
  1. **Explicit metadata** (`EXPLICIT_METADATA`): language field, category/breadcrumb name, title abbreviations like `(укр.)`, or note phrases like `на польском языке` / `sung in Yiddish`. A category naming *Russian* is a soft default only: it cannot override an instrumental or a decisively non-Russian title. *Gypsy/цыганский* and *Jewish/еврейский* are **weak hints**, because the цыганский романс is a Russian-language genre and much Jewish repertoire is in Russian. They apply only when the title isn't confidently Russian.
  2. **Instrumental** (`INSTRUMENTAL`, music only): instrumental category, form (march, waltz, overture...) or ensemble performer, *and* no vocal markers (choir, tenor, singer, "в сопр.", lyricist...).
  3. **Script & NLP** (`SCRIPT_NLP`): Hebrew script → Yiddish; Georgian/Armenian/Greek scripts. For Cyrillic, decisive orthography comes first: pre-reform Russian `ѣ ѳ ѵ`, word-final `ъ` and `-іе/-іи` (common on pre-1918 labels, and misread by lingua as Belarusian/Ukrainian); Ukrainian `ї є ґ`; Belarusian `ў`. Then letter-set exclusions, then `lingua` restricted to the remaining candidates. A non-Russian result needs ≥ 0.85 confidence and a 0.4 margin over Russian, because lingua is unreliable on short titles.
  4. **Default Cyrillic** (`DEFAULT_CYRILLIC`): a Cyrillic vocal title no detector could pin down → `Russian`.
  5. **Unknown** (`UNKNOWN`) → `Unknown_Language`. Latin-script titles may be transliterations, so they need ≥ 0.75 lingua confidence.
  6. **Manual** (`MANUAL`): a human override from the dashboard (`rr_archive/reclassify.py`). Re-crawls and retries never overwrite a `MANUAL` language or category.

* **Strict Path Sanitization (`rr_archive/paths.py`):**
  * Replace forbidden characters `/ \ : * ? " < > |` and control characters; avoid Windows reserved names (`CON`, `NUL`, ...).
  * Normalize to NFC UTF-8 and keep Cyrillic and other Unicode.
  * Collapse whitespace; strip trailing periods and whitespace.
  * Truncate each segment to 120 characters. Absolute paths stay ≤ 240 characters: category, artist and title shrink first, and `[Item_ID]` is never trimmed.

## 4. Technical Stack & Architecture Standards

* **Environment:** Python 3.10+ in `.venv` (dependencies pinned in `requirements.txt`).
* **Libraries:** `requests`, `beautifulsoup4` + `lxml`, `mutagen` (ID3v2.4), `lingua-language-detector`, `sqlite3`, `pytest`.
* **Layout:** `scraper.py` (CLI) and `rr_archive/`: `config`, `net` (polite client, cookies, CAPTCHA halt), `parser` (4images), `classify`, `paths`, `state`, `media` (magic bytes), `tagger`, `crawler`. Offline tests live in `tests/` and use a fake HTTP transport and synthetic pages.
* **Database Schema (`./archive_state.db`):**
  * `tracks`: `item_id` (PK, 4images `image_id`), `source_url`, `audio_url`, `media_type` (`MUSIC`/`RADIO_SPEECH`/`PRINT`/`IMAGE`/`NONE`), `language`, `language_detection_method` (`EXPLICIT_METADATA`, `INSTRUMENTAL`, `SCRIPT_NLP`, `DEFAULT_CYRILLIC`, `UNKNOWN`), `language_note`, `category`, `category_path`, `artist`, `composer`, `title`, `year`, `record_label`, `catalog_number`, `matrix_number`, `label_image_urls`, `label_local_paths`, `raw_metadata_json`, `local_filepath`, `file_sha256`, `file_size`, `status` (`PENDING`, `COMPLETED`, `FAILED`, `SKIPPED`), `attempts`, `last_error`, `discovered_at`, `updated_at`, `downloaded_at`.
  * `pages`: crawl frontier (`url`, `kind`, `parent_cat`, `seq`, `status`, `attempts`, `last_error`, `fetched_at`).
  * `categories`: `cat_id`, `name`, `parent_id`, `path_json`.
* **ID3v2.4 frames:** `TIT2`, `TPE1`, `TCOM`, `TEXT` (lyricist), `TALB` (category path), `TCON`, `TDRC` (first 4-digit year), `TLAN` (ISO 639-2: `rus`, `ukr`, `yid`, ...; `zxx` instrumental, `und` unknown), `TPUB` (label + catalog no.), `COMM` (source URL + notes + language reasoning), `WOAS`, `APIC` (label scan, type 3), and `TXXX` (`ITEM_ID`, `LANGUAGE`, `LANGUAGE_DETECTION_METHOD`, `CATALOG_NUMBER`, `MATRIX_NUMBER`, `CATEGORY_PATH`, `YEAR_ORIGINAL`). Only MP3 is tagged; any other container is kept untagged and logged.
* **Page cache:** fetched HTML is stored gzipped in `./_page_cache/`, so the parser can be improved without re-crawling.

## 4b. Local Dashboard & Static Catalog

* **Dashboard:** `python dashboard.py` serves `http://127.0.0.1:8000` (FastAPI, `rr_archive/web/`).
  * It binds to **127.0.0.1 only**. Host-header checking guards against DNS rebinding, and every POST needs the `X-RR-Dashboard: 1` header (CSRF guard).
  * Media is served only for paths recorded in the DB that resolve inside the archive root.
* **Session panel:** writes `./cookies.txt` (Netscape format) from a pasted `sessionid` / `4images_userid` or a raw `Cookie:` header. Values are shown masked and never echoed back. An optional browser User-Agent is stored in `_control/settings.json`.
* **Jobs** (`rr_archive/web/jobs.py`):
  * Each job runs `scraper.py` as **one subprocess at a time**, so the single-threaded rule holds even if buttons are clicked twice.
  * Pause, resume and stop use cooperative files `_control/PAUSE` and `_control/STOP`, checked between items, so a file is never half-written. Force kill is the fallback.
  * Exit code 3 shows the red CAPTCHA banner. The fresh `sessionid` pasted there restarts the same mode.
* **Recon** writes `_recon/report.json` with per-selector pass/fail checks, and the dashboard renders it.
* **Archive explorer:** `rr_archive/web/static/catalog.js` is shared by the dashboard and the static catalog. It never uses `innerHTML` on scraped text. Re-classification is the only write path to files:
  1. rewrite `TLAN`/`TALB`/`TXXX` in place;
  2. `os.replace` the main file and label sidecars;
  3. update the DB (new path, sha256, `MANUAL`);
  4. prune empty folders.
  Any failure rolls back moves and tags. Only `COMPLETED` items can be re-classified.
* **Static catalog:** `python scraper.py --export-html` (or the dashboard button) writes `./russian_records_archive/index.html`, which works offline without Python:
  * CSS and JS are inlined, with no CDN;
  * the catalog is embedded as JSON with `</` escaped;
  * media paths are relative, with each segment URI-encoded.
  Re-export after crawling or re-classifying.

## 5. Polite Crawling & Network Safety Constraints

* **Concurrency:** single-threaded by default (`--workers 1`).
  * **Parallel mode** (`--workers 2..32`, or the dashboard's 1–32 "Parallel downloads" slider; **default 1**). Only for operators with the site's permission: for this archive the maintainer authorized parallel bulk transfer on 2026-10-18, "as long as the page doesn't break". Without that permission, stay single-threaded. The public default stays polite; don't raise it, and don't bring back the old fixed caps (6 workers, 0.5 s spacing) unless the user or maintainer asks. These safeguards stay (`net.RateLimiter`, `Crawler.run_parallel`):
    * **Green:** time to first byte under 3 s. The run uses all configured workers from the start, with only 0–0.02 s jitter between request starts.
    * **Yellow:** 3 consecutive responses slower than 8 s, or any timeout, connection error, 429 or 5xx. Each event cuts the workers by **25%** for 15 s. Afterwards every 10 clean responses add 4 workers back, up to `--workers`.
    * **Two lanes:** binary media (audio, scans, PDFs) runs on the worker pool, and a separate HTML lane (workers ÷ 4, at least 1) keeps feeding the queue. Each session keeps a 64-connection keep-alive pool. SQLite runs in WAL mode with `synchronous=NORMAL` and a 30 s busy timeout.
    * A CAPTCHA or dead session in any worker still stops the whole run, and `.part` writes stay atomic.
    * Test mode always stays single-threaded.
* **Rate limiting (single-threaded mode):** a randomized **2.0–4.0 s** gap before every HTTP request, including redirects, retries and media. `--min-delay` below 2.0 is refused.
* **Backoff:** exponential (base 5 s, max 120 s, 5 retries) on 429/500/502/503/504, timeouts and connection errors. `Retry-After` is always honoured.
* **Scope:** only `www.russian-records.com` and `russian-records.com`; `/cnstats/` (robots.txt) and the CAPTCHA endpoints are never requested.
* **Atomic writes:** each download goes to `{file}.mp3.part`. The pipeline then checks length against Content-Length, checks magic bytes, writes tags and verifies them with mutagen, then calls `os.replace` to `.mp3`. Stale `.part` files are redone on the next run.

## 6. Common Commands

* **Initialize environment:**
  `python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt`
* **Run offline tests:** `python -m pytest -q tests`
* **Recon (online / from saved pages):**
  `python scraper.py --recon --cookie-file cookies.txt`
  `python scraper.py --recon --local-html-dir saved_pages`
* **5-item verification test:** `python scraper.py --test-mode --cookie-file cookies.txt`
* **Full resumable crawl:** `python scraper.py --resume --cookie-file cookies.txt` (add `--retry-failed` to retry failures)
  * `--queue audio-first` (default): downloads of items with audio, then details pages whose listing row had a play button (`pages.hint='audio'`), then category pages, then everything else (label scans, books, periodicals).
  * `--queue audio-only`: never visits or downloads items without audio. They stay `PENDING`, and a later `audio-first` run fetches them. The end-of-run log line reports how many were deferred.
  * `--queue all`: plain discovery order.
  * `--limit N`: stop after N completed items, for controlled overnight batches. `0` means unlimited.
  * The dashboard exposes the same options as the "Audio priority" and "Audio only" checkboxes and a "Stop after N items" input, which apply to full crawls only.
* **PDF scans:** `view_pdf.php?image_id=N&image=2` streams `application/pdf` directly, with no Content-Length. A PDF is saved next to its cover after checking it starts with `%PDF-` and ends with an `%%EOF` marker. A failed PDF leaves the item `FAILED` with its cover kept, so `--retry-failed` fetches only the PDF. `--verify` checks PDFs too.
* **Launcher:** double-click `start_dashboard.bat`. It runs `.venv\Scripts\python.exe dashboard.py --port 8000` in its own console window, independent of Claude Code, and opens the browser. If the dashboard is already running it just opens the browser.
* **Ingest saved pages (metadata only without a session):** `python scraper.py --local-html-dir saved_pages`
* **Progress:** `python scraper.py --status` or
  `sqlite3 archive_state.db "SELECT media_type, language, status, COUNT(*) FROM tracks GROUP BY 1,2,3;"`
* **Re-verify files on disk:** `python scraper.py --verify`
* **After any parser/classifier fix:** `python scraper.py --refile-cached` re-parses `_page_cache/` offline.
  * Items with unchanged media URLs are moved and fully re-tagged in place.
  * Items whose media URLs changed have their old files moved to `_superseded/<item_id>/`, never deleted, and go back to `PENDING`. Then run `--resume --limit N` to fetch them.
  * `MANUAL` overrides are never touched.
* **Audio formats:** MP3 is tagged with ID3v2.4. FLAC is tagged with Vorbis comments (`LANGUAGE` = the ISO 639-2 code, like `TLAN`, plus `TITLE`, `ARTIST`, `DATE`, `ORGANIZATION`, `CATALOGNUMBER` and others), with the label scan as a front-cover picture.
* **Dashboard:** `python dashboard.py` → `http://127.0.0.1:8000`
* **Static catalog:** `python scraper.py --export-html` → `russian_records_archive/index.html`
