"""Central constants. Network limits here reflect the terms agreed with the
archive maintainer: single-threaded, 2-4 s between requests, human-solved
CAPTCHA sessions only."""

from pathlib import Path

BASE_URL = "https://www.russian-records.com/"
START_URL = BASE_URL + "index.php"
CANONICAL_HOST = "www.russian-records.com"
ALLOWED_HOSTS = {"www.russian-records.com", "russian-records.com"}

# Paths the crawler must never request (robots.txt + CAPTCHA endpoints).
FORBIDDEN_PATH_PREFIXES = ("/cnstats/",)
CAPTCHA_MARKERS = ("get_captcha.php", "captcha.php")

# 4images session cookie name observed on the live site (not PHPSESSID).
SESSION_COOKIE_NAME = "sessionid"

# --- Politeness -------------------------------------------------------------
MIN_DELAY_FLOOR = 2.0      # hard floor; the CLI refuses anything lower
DEFAULT_MIN_DELAY = 2.0
DEFAULT_MAX_DELAY = 4.0
BACKOFF_BASE = 5.0         # seconds
BACKOFF_MAX = 120.0
MAX_RETRIES = 5
RETRY_STATUSES = {429, 500, 502, 503, 504}
REQUEST_TIMEOUT = (15, 60)  # (connect, read) seconds
MAX_CONSECUTIVE_403 = 3     # repeated 403s usually mean the session died

# Pin HTTP to IPv4: the site's IPv6 route on this dual-stack network times out
# intermittently. net.force_ipv4() applies this on import for every crawl process.
IPV4_ONLY = True

# --- Parallel mode ------------------------------------------------------------
# Only for operators who have the site's permission. For this archive the
# maintainer authorized parallel bulk transfer on 2026-10-18; without such
# permission, stay at the polite single-threaded default. In parallel mode
# net.RateLimiter is the governor - see its docstring for the back-off.
PARALLEL_MAX_WORKERS = 32         # hard cap
PARALLEL_DEFAULT_WORKERS = 1      # dashboard default: polite, single-threaded
PARALLEL_MIN_INTERVAL = 0.0       # no artificial spacing while healthy...
PARALLEL_MAX_INTERVAL = 0.02      # ...only a little jitter between request starts
PARALLEL_MIN_INTERVAL_FLOOR = 0.0
PARALLEL_PAGE_WORKERS_DIV = 4     # HTML lane = max(1, workers // 4) threads
HTTP_POOL_SIZE = 64               # keep-alive connections per session

DEFAULT_USER_AGENT = (
    "RussianRecordsArchiver/1.0 (authorized academic archival research; "
    "single-threaded, 2-4s delay)"
)

# --- Test mode --------------------------------------------------------------
TEST_MODE_LIMIT = 5
TEST_MODE_PAGE_BUDGET = 80      # max HTML pages fetched during a test run
TEST_MODE_CATEGORY_SAMPLE = 3   # minimum category pages visited before picking items

# --- Filesystem -------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_ROOT = PROJECT_ROOT / "russian_records_archive"
DB_PATH = PROJECT_ROOT / "archive_state.db"
PAGE_CACHE_DIR = PROJECT_ROOT / "_page_cache"
RECON_DIR = PROJECT_ROOT / "_recon"
LOG_PATH = PROJECT_ROOT / "scraper.log"

SEGMENT_MAX = 120   # max chars per folder/file name segment
PATH_BUDGET = 240   # keep absolute paths under Windows MAX_PATH (260)

# How the {Category} folder is named from the breadcrumb path:
#   "top_leaf" -> "TopCategory - DeepestSubcategory" (just the category when
#                 there is only one level; middle levels stay in TALB/DB)
#   "leaf"     -> deepest subcategory only
#   "top"      -> top-level category directly under "Home"
CATEGORY_MODE = "top_leaf"

# Media branches (top-level folders under ARCHIVE_ROOT).
BRANCH_RADIO = "Radio_and_Speech"
BRANCH_PRINT = "Newspapers_and_Print"
BRANCH_IMAGES = "Images_and_Scans"

MAX_LABEL_IMAGES = 4

# Cooperative pause/stop signals for the dashboard (checked between items).
CONTROL_DIR = PROJECT_ROOT / "_control"
COOKIE_FILE = PROJECT_ROOT / "cookies.txt"
