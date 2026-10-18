"""Demo mode + repo-hygiene guards.

The hygiene tests are a safety net for public publication: they fail the build
if the local personal dedication or a live session cookie ever lands in a
publishable file. The forbidden values are read at runtime from the git-ignored
files themselves, so this test file never contains them.
"""

import json
import re
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
STATIC = PROJECT / "rr_archive" / "web" / "static"
NEUTRAL = "A gift of old records"
TEXT_EXT = {".py", ".js", ".css", ".html", ".json", ".md", ".txt", ".bat", ".cfg", ".ini", ".toml"}
# Git-ignored / generated / large dirs that are never published.
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".claude", "russian_records_archive",
             "patefon_gift", "demo_archive", "_recon", "_superseded", "_page_cache", "_control",
             "_backup_before_ussr", "fonts"}
# Git-ignored files that legitimately hold private data on the owner's machine.
SKIP_FILES = {"dedication.json", "cookies.txt"}


def publishable_text_files():
    for p in PROJECT.rglob("*"):
        if p.is_dir() or p.suffix.lower() not in TEXT_EXT:
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(PROJECT).parts):
            continue
        if p.name in SKIP_FILES:
            continue
        yield p


def personal_phrases():
    """The local, git-ignored dedication and its distinctive words ([] on a fresh clone)."""
    try:
        text = json.loads((STATIC / "dedication.json").read_text(encoding="utf-8"))["text"].strip()
    except (OSError, ValueError, KeyError):
        return []
    if not text or text == NEUTRAL:
        return []
    common = {"for", "with", "love", "from", "your", "dear", "dearest", "always", "forever"}
    words = [w for w in re.findall(r"\w{4,}", text) if w.lower() not in common]
    return [text] + words


def live_cookie_values():
    """Session values from the git-ignored cookies.txt ([] on a fresh clone)."""
    try:
        lines = (PROJECT / "cookies.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    values = []
    for line in lines:
        parts = line.strip().split("\t")
        if len(parts) >= 7 and not line.startswith("#"):
            values.append(parts[6])
        for m in re.finditer(r"(?:sessionid|4images_userid)=([^;\s]+)", line):
            values.append(m.group(1))
    return [v for v in values if len(v) >= 8]          # short ids would false-positive


def test_no_personal_dedication_in_publishable_files():
    phrases = personal_phrases()
    if not phrases:
        pytest.skip("no personal dedication.json on this machine")
    hits = sorted({str(p.relative_to(PROJECT)) for p in publishable_text_files()
                   for ph in phrases
                   if ph.lower() in p.read_text(encoding="utf-8", errors="ignore").lower()})
    assert hits == [], f"personal dedication text found in: {hits}"


def test_no_live_session_cookie_in_publishable_files():
    values = live_cookie_values()
    if not values:
        pytest.skip("no cookies.txt on this machine")
    hits = sorted({str(p.relative_to(PROJECT)) for p in publishable_text_files()
                   for v in values if v in p.read_text(encoding="utf-8", errors="ignore")})
    assert hits == [], f"live session value found in: {hits}"


def test_gitignore_patterns_are_whole_lines():
    # Git has no trailing comments: "foo  # note" would NOT ignore foo.
    lines = {l.strip() for l in (PROJECT / ".gitignore").read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.lstrip().startswith("#")}
    for pat in ("cookies.txt", "rr_archive/web/static/dedication.json", "_control/", ".claude/",
                "*.db", "*.db-wal", "*.db-shm", "russian_records_archive/", "patefon_gift/",
                "demo_archive/", "_recon/", "_superseded/", "_page_cache/", "scraper.log", ".venv/"):
        assert pat in lines, f".gitignore must contain the exact line {pat!r}"
    assert not [l for l in lines if "#" in l], "a .gitignore pattern contains '#': trailing comments break it"


def test_dedication_example_is_committed_and_neutral():
    d = json.loads((STATIC / "dedication.example.json").read_text(encoding="utf-8"))
    assert d["text"] == NEUTRAL


def test_seed_demo_builds_standalone_ui(tmp_path):
    pytest.importorskip("imageio_ffmpeg")
    pytest.importorskip("PIL")
    from rr_archive.demo import seed_demo
    info = seed_demo(dest=tmp_path / "demo")
    assert info["items"] == 5
    root = tmp_path / "demo" / "russian_records_archive"
    html = (root / "index.html").read_text(encoding="utf-8")
    data = json.loads(html.split('<script id="rr-data" type="application/json">', 1)[1].split("</script>", 1)[0])
    assert len(data["items"]) == 5
    assert {i["branch"] for i in data["items"]} >= {"MUSIC", "RADIO_SPEECH", "PRINT"}
    assert all((root / i["file"]).exists() for i in data["items"])
    # standalone (no admin/API), neutral dedication, nothing personal baked in
    assert "/api/" not in html and "admin-drawer" not in html and "X-RR-Dashboard" not in html
    assert data["dedication"]["text"] == NEUTRAL
    assert "seed" not in data
    low = html.lower()
    assert not [ph for ph in personal_phrases() if ph.lower() in low]
