"""SQLite state ledger: crawl frontier (pages) + per-item status (tracks).

Item status values:
  PENDING    - metadata known, media not yet downloaded
  COMPLETED  - media on disk, validated, tagged, hashed
  FAILED     - last attempt failed (see last_error); retried with --retry-failed
  SKIPPED    - details page had no downloadable media
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    item_id                   TEXT PRIMARY KEY,
    source_url                TEXT NOT NULL,
    audio_url                 TEXT,
    media_type                TEXT,
    language                  TEXT,
    language_detection_method TEXT,
    language_note             TEXT,
    category                  TEXT,
    category_path             TEXT,
    artist                    TEXT,
    composer                  TEXT,
    title                     TEXT,
    year                      TEXT,
    record_label              TEXT,
    catalog_number            TEXT,
    matrix_number             TEXT,
    label_image_urls          TEXT,
    label_local_paths         TEXT,
    raw_metadata_json         TEXT,
    local_filepath            TEXT,
    file_sha256               TEXT,
    file_size                 INTEGER,
    status                    TEXT NOT NULL DEFAULT 'PENDING',
    attempts                  INTEGER NOT NULL DEFAULT 0,
    last_error                TEXT,
    discovered_at             TIMESTAMP,
    updated_at                TIMESTAMP,
    downloaded_at             TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(status);
CREATE INDEX IF NOT EXISTS idx_tracks_language ON tracks(language);

CREATE TABLE IF NOT EXISTS pages (
    url         TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    parent_cat  TEXT,
    seq         INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'PENDING',
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    fetched_at  TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pages_status ON pages(status, kind);

CREATE TABLE IF NOT EXISTS categories (
    cat_id     TEXT PRIMARY KEY,
    name       TEXT,
    parent_id  TEXT,
    path_json  TEXT
);
"""

TRACK_FIELDS = ("source_url", "audio_url", "media_type", "language", "language_detection_method",
                "language_note", "category", "category_path", "artist", "composer", "title",
                "year", "record_label", "catalog_number", "matrix_number", "label_image_urls",
                "pdf_url", "raw_metadata_json")

# Columns added after the first release; created on open for older databases.
MIGRATIONS = {
    "pages": {"hint": "TEXT"},
    "tracks": {"pdf_url": "TEXT", "pdf_local_path": "TEXT"},
}

# Crawl queue policies (CLI --queue, dashboard checkboxes).
QUEUE_AUDIO_FIRST = "audio-first"   # audio items first, then everything else
QUEUE_AUDIO_ONLY = "audio-only"     # never visit/download items without audio
QUEUE_ALL = "all"                   # plain discovery order
QUEUE_MODES = (QUEUE_AUDIO_FIRST, QUEUE_AUDIO_ONLY, QUEUE_ALL)
AUDIO_TYPES = ("MUSIC", "RADIO_SPEECH")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class State:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        # 30 s busy timeout: parallel workers each hold their own connection
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        for table, columns in MIGRATIONS.items():
            have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for col, decl in columns.items():
                if col not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.close()

    # -- pages ----------------------------------------------------------------
    def add_page(self, url: str, kind: str, parent_cat: str | None = None,
                 hint: str | None = None) -> bool:
        seq = self.conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM pages").fetchone()[0]
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO pages(url, kind, parent_cat, seq, hint) VALUES (?,?,?,?,?)",
            (url, kind, parent_cat, seq, hint))
        if cur.rowcount == 0 and hint:
            self.conn.execute("UPDATE pages SET hint=? WHERE url=? AND hint IS NULL", (hint, url))
        return cur.rowcount > 0

    def mark_page(self, url: str, status: str, error: str | None = None):
        self.conn.execute(
            "UPDATE pages SET status=?, last_error=?, attempts=attempts+1, fetched_at=? WHERE url=?",
            (status, error, now(), url))
        self.conn.commit()

    def next_page(self, test_mode: bool = False, category_sample: int = 0,
                  want: int = 5) -> sqlite3.Row | None:
        if test_mode:
            # Keep exploring categories until enough distinct categories offer
            # items with audio (listing rows with a play button), so the test
            # run exercises real tracks, not only book/label scans.
            visited_cats = self.conn.execute(
                "SELECT COUNT(*) FROM pages WHERE kind IN ('index','category') AND status='DONE'"
            ).fetchone()[0]
            audio_cats = self.conn.execute(
                "SELECT COUNT(DISTINCT COALESCE(parent_cat,'')) FROM pages "
                "WHERE kind='details' AND hint='audio' AND status='PENDING'").fetchone()[0]
            if visited_cats < category_sample or audio_cats < want:
                row = self.conn.execute(
                    "SELECT * FROM pages WHERE status='PENDING' AND kind IN ('index','category') "
                    "ORDER BY (url LIKE '%page=%'), seq LIMIT 1").fetchone()
                if row:
                    return row
            # Spread across categories: first unvisited item of each category, round-robin.
            row = self.conn.execute(
                """SELECT * FROM (
                       SELECT p.*, ROW_NUMBER() OVER (PARTITION BY parent_cat ORDER BY seq) AS rn
                       FROM pages p WHERE kind='details' AND status='PENDING'
                         AND NOT EXISTS (SELECT 1 FROM tracks t
                                         WHERE t.source_url = p.url)
                   ) WHERE COALESCE(parent_cat, '') NOT IN (
                       SELECT DISTINCT COALESCE(p2.parent_cat, '') FROM pages p2
                       JOIN tracks t ON t.source_url = p2.url
                       WHERE t.status IN ('COMPLETED', 'FAILED', 'SKIPPED'))
                   ORDER BY (COALESCE(hint, '') = 'audio') DESC, rn, seq LIMIT 1""").fetchone()
            if row:
                return row
        row = self.conn.execute(
            "SELECT * FROM pages WHERE status='PENDING' AND kind='details' ORDER BY seq LIMIT 1"
        ).fetchone()
        if row:
            return row
        return self.conn.execute(
            "SELECT * FROM pages WHERE status='PENDING' ORDER BY seq LIMIT 1").fetchone()

    def page_count(self, status: str | None = None) -> int:
        if status:
            return self.conn.execute("SELECT COUNT(*) FROM pages WHERE status=?", (status,)).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]

    def requeue_failed_pages(self) -> int:
        cur = self.conn.execute("UPDATE pages SET status='PENDING' WHERE status='FAILED'")
        self.conn.commit()
        return cur.rowcount

    # -- categories -------------------------------------------------------------
    def upsert_category(self, cat_id: str, name: str, parent_id: str | None, path: list[str]):
        if not cat_id:
            return
        self.conn.execute(
            """INSERT INTO categories(cat_id, name, parent_id, path_json) VALUES (?,?,?,?)
               ON CONFLICT(cat_id) DO UPDATE SET
                 name=COALESCE(NULLIF(excluded.name,''), categories.name),
                 parent_id=COALESCE(excluded.parent_id, categories.parent_id),
                 path_json=CASE WHEN excluded.path_json != '[]' THEN excluded.path_json
                                ELSE categories.path_json END""",
            (cat_id, name, parent_id, json.dumps(path, ensure_ascii=False)))

    def category_name(self, cat_id: str) -> str | None:
        row = self.conn.execute("SELECT name FROM categories WHERE cat_id=?", (cat_id,)).fetchone()
        return row["name"] if row else None

    # -- tracks -------------------------------------------------------------------
    def get_track(self, item_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM tracks WHERE item_id=?", (item_id,)).fetchone()

    def upsert_track(self, item_id: str, data: dict, status: str = "PENDING") -> str:
        """Insert or refresh metadata. COMPLETED rows are never touched so a
        re-crawl cannot disturb verified files. Returns the resulting status."""
        existing = self.get_track(item_id)
        if existing and existing["status"] == "COMPLETED":
            return "COMPLETED"
        values = {k: data.get(k) for k in TRACK_FIELDS}
        if existing and existing["language_detection_method"] == "MANUAL":
            # A human override survives re-crawls and retries.
            for k in ("language", "language_detection_method", "language_note", "category"):
                values[k] = existing[k]
        if existing:
            new_status = existing["status"] if existing["status"] == "FAILED" and status == "PENDING" else status
            sets = ", ".join(f"{k}=?" for k in values)
            self.conn.execute(f"UPDATE tracks SET {sets}, status=?, updated_at=? WHERE item_id=?",
                              (*values.values(), new_status, now(), item_id))
        else:
            new_status = status
            cols = ", ".join(values)
            marks = ", ".join("?" for _ in values)
            self.conn.execute(
                f"INSERT INTO tracks(item_id, {cols}, status, discovered_at, updated_at) "
                f"VALUES (?, {marks}, ?, ?, ?)",
                (item_id, *values.values(), new_status, now(), now()))
        self.conn.commit()
        return new_status

    def next_pending_track(self, retry_failed: bool = False, audio_only: bool = False,
                           exclude=()) -> sqlite3.Row | None:
        statuses = ("PENDING", "FAILED") if retry_failed else ("PENDING",)
        q = f"SELECT * FROM tracks WHERE status IN ({','.join('?' * len(statuses))}) "
        params = list(statuses)
        if retry_failed:
            q += "AND attempts < 50 "
        if audio_only:
            q += f"AND media_type IN {AUDIO_TYPES} "
        if exclude:
            q += f"AND item_id NOT IN ({','.join('?' * len(exclude))}) "
            params += list(exclude)
        return self.conn.execute(q + "ORDER BY attempts, discovered_at LIMIT 1", params).fetchone()

    @staticmethod
    def _not_in(exclude) -> tuple[str, list]:
        if not exclude:
            return "", []
        return f"AND url NOT IN ({','.join('?' * len(exclude))}) ", list(exclude)

    def next_audio_page(self, exclude=()) -> sqlite3.Row | None:
        """Audio-priority frontier: details pages whose listing row had a play
        button, then index/category pages (to discover more of them)."""
        extra, params = self._not_in(exclude)
        return self.conn.execute(
            "SELECT * FROM pages WHERE status='PENDING' AND "
            "(kind IN ('index','category') OR (kind='details' AND hint='audio')) " + extra +
            "ORDER BY (kind='details') DESC, seq LIMIT 1", params).fetchone()

    def next_other_page(self, exclude=()) -> sqlite3.Row | None:
        """Details pages without an audio hint (label scans, books, periodicals)."""
        extra, params = self._not_in(exclude)
        return self.conn.execute(
            "SELECT * FROM pages WHERE status='PENDING' AND kind='details' "
            "AND COALESCE(hint,'') != 'audio' " + extra + "ORDER BY seq LIMIT 1", params).fetchone()

    def next_any_page(self, exclude=()) -> sqlite3.Row | None:
        """Plain discovery order: details pages first, then everything else."""
        extra, params = self._not_in(exclude)
        row = self.conn.execute("SELECT * FROM pages WHERE status='PENDING' AND kind='details' " + extra +
                                "ORDER BY seq LIMIT 1", params).fetchone()
        return row or self.conn.execute("SELECT * FROM pages WHERE status='PENDING' " + extra +
                                        "ORDER BY seq LIMIT 1", params).fetchone()

    def deferred_counts(self) -> dict:
        """What audio-only mode leaves for later."""
        pages = self.conn.execute(
            "SELECT COUNT(*) FROM pages WHERE status='PENDING' AND kind='details' "
            "AND COALESCE(hint,'') != 'audio'").fetchone()[0]
        items = self.conn.execute(
            f"SELECT COUNT(*) FROM tracks WHERE status='PENDING' AND "
            f"COALESCE(media_type,'') NOT IN {AUDIO_TYPES}").fetchone()[0]
        return {"details_pages_without_audio": pages, "items_without_audio": items}

    def mark_completed(self, item_id: str, local_path: str, sha256: str, size: int,
                       label_paths: list[str], pdf_path: str | None = None):
        self.conn.execute(
            """UPDATE tracks SET status='COMPLETED', local_filepath=?, file_sha256=?, file_size=?,
                   label_local_paths=?, pdf_local_path=?, last_error=NULL, attempts=attempts+1,
                   downloaded_at=?, updated_at=? WHERE item_id=?""",
            (local_path, sha256, size, json.dumps(label_paths, ensure_ascii=False), pdf_path,
             now(), now(), item_id))
        self.conn.commit()

    def mark_failed(self, item_id: str, error: str):
        self.conn.execute(
            "UPDATE tracks SET status='FAILED', last_error=?, attempts=attempts+1, updated_at=? "
            "WHERE item_id=?", (error[:2000], now(), item_id))
        self.conn.commit()

    def mark_skipped(self, item_id: str, reason: str):
        self.conn.execute("UPDATE tracks SET status='SKIPPED', last_error=?, updated_at=? WHERE item_id=?",
                          (reason, now(), item_id))
        self.conn.commit()

    def completed_rows(self):
        return self.conn.execute("SELECT * FROM tracks WHERE status='COMPLETED' ORDER BY item_id").fetchall()

    def summary(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT media_type, language, status, COUNT(*) AS n FROM tracks "
            "GROUP BY media_type, language, status ORDER BY media_type, n DESC").fetchall()
