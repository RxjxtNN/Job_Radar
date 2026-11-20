"""
db.py — SQLite persistence layer for Job Radar.
Uses stdlib sqlite3 only (no ORM dependency).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def get_connection(db_path: Optional[str] = None, read_only: bool = False) -> sqlite3.Connection:
    """
    Return a sqlite3.Connection to *db_path* (defaults to config.DB_PATH).

    The connection has:
    - row_factory = sqlite3.Row  → columns accessible by name
    - WAL journal mode           → safe concurrent readers
    - foreign_keys enforced

    *read_only* opens the file with mode=ro and changes no pragmas — used by
    the Streamlit app, which must never write to the committed DB.
    """
    path = db_path or config.DB_PATH
    if read_only:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL_POSTS = """
CREATE TABLE IF NOT EXISTS posts (
    id           TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    source_job_id TEXT,
    company      TEXT NOT NULL,
    title        TEXT NOT NULL,
    location_raw TEXT,
    description  TEXT,
    url          TEXT NOT NULL,
    posted_at    TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    city         TEXT,
    dedup_key    TEXT,
    duplicate_of TEXT,
    content_hash TEXT
);
"""

_DDL_TAGS = """
CREATE TABLE IF NOT EXISTS tags (
    post_id       TEXT PRIMARY KEY REFERENCES posts(id),
    role          TEXT,
    city          TEXT,
    is_fresher    INTEGER,
    model         TEXT,
    prompt_version TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    tagged_at     TEXT
);
"""

_DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_posts_source      ON posts(source);",
    "CREATE INDEX IF NOT EXISTS idx_posts_company     ON posts(company);",
    "CREATE INDEX IF NOT EXISTS idx_posts_last_seen   ON posts(last_seen);",
    "CREATE INDEX IF NOT EXISTS idx_posts_dedup_key   ON posts(dedup_key);",
    "CREATE INDEX IF NOT EXISTS idx_posts_content_hash ON posts(content_hash);",
    "CREATE INDEX IF NOT EXISTS idx_tags_role         ON tags(role);",
    "CREATE INDEX IF NOT EXISTS idx_tags_city         ON tags(city);",
    "CREATE INDEX IF NOT EXISTS idx_tags_is_fresher   ON tags(is_fresher);",
    "CREATE INDEX IF NOT EXISTS idx_tags_prompt_ver   ON tags(prompt_version);",
]


def create_tables(conn: sqlite3.Connection) -> None:
    """Create posts and tags tables (and indexes) if they do not exist."""
    with conn:
        conn.execute(_DDL_POSTS)
        conn.execute(_DDL_TAGS)
        # Migration: `city` (rule-based, from normalize) was added after v1.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(posts)")}
        if "city" not in cols:
            conn.execute("ALTER TABLE posts ADD COLUMN city TEXT")
        for idx_sql in _DDL_INDEXES:
            conn.execute(idx_sql)
    logger.debug("Tables and indexes ensured.")


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def upsert_post(conn: sqlite3.Connection, post: dict) -> None:
    """
    INSERT OR IGNORE a post, then UPDATE last_seen (and content_hash /
    dedup_key) if the row already existed.  Uses a single transaction.
    """
    now = _utcnow()
    post["first_seen"] = post.get("first_seen") or now
    post["last_seen"] = post.get("last_seen") or now

    insert_sql = """
        INSERT OR IGNORE INTO posts
            (id, source, source_job_id, company, title, location_raw,
             description, url, posted_at, first_seen, last_seen,
             city, dedup_key, duplicate_of, content_hash)
        VALUES
            (:id, :source, :source_job_id, :company, :title, :location_raw,
             :description, :url, :posted_at, :first_seen, :last_seen,
             :city, :dedup_key, :duplicate_of, :content_hash)
    """

    update_sql = """
        UPDATE posts
        SET last_seen    = :last_seen,
            company      = :company,
            title        = :title,
            location_raw = :location_raw,
            description  = :description,
            city         = :city,
            content_hash = :content_hash,
            dedup_key    = :dedup_key
        WHERE id = :id
    """

    with conn:
        conn.execute(insert_sql, _post_params(post))
        params = _post_params(post)
        conn.execute(update_sql, {k: params[k] for k in (
            "last_seen", "company", "title", "location_raw", "description", "city",
            "content_hash", "dedup_key", "id")})


def delete_posts(conn: sqlite3.Connection, post_ids: list[str]) -> None:
    """Remove posts (and their tags) — used when filter rules change."""
    with conn:
        conn.executemany("DELETE FROM tags WHERE post_id = ?", [(i,) for i in post_ids])
        conn.executemany("UPDATE posts SET duplicate_of = NULL WHERE duplicate_of = ?",
                         [(i,) for i in post_ids])
        conn.executemany("DELETE FROM posts WHERE id = ?", [(i,) for i in post_ids])


def get_live_candidates(conn: sqlite3.Connection) -> list[dict]:
    """Posts seen within EXPIRY_DAYS (duplicate or not) — the dedup universe."""
    rows = conn.execute(
        "SELECT * FROM posts WHERE datetime(last_seen) >= datetime('now', :w) "
        "ORDER BY first_seen ASC",
        {"w": f"-{config.EXPIRY_DAYS} days"},
    ).fetchall()
    return [dict(r) for r in rows]


def set_duplicates(conn: sqlite3.Connection, pairs: list[tuple[str, str]]) -> None:
    """
    Replace all duplicate links with *pairs* (duplicate_id, kept_id) in one
    transaction. Clearing first means a post hidden as a duplicate comes back
    when the post it pointed to closes or stops matching.
    """
    with conn:
        conn.execute("UPDATE posts SET duplicate_of = NULL WHERE duplicate_of IS NOT NULL")
        conn.executemany("UPDATE posts SET duplicate_of = ? WHERE id = ?",
                         [(kept, dup) for dup, kept in pairs])


def save_tag(conn: sqlite3.Connection, tag: dict) -> None:
    """
    INSERT OR REPLACE a tag row (upsert by post_id PK).
    *tag* must include at minimum: post_id.
    """
    tag.setdefault("tagged_at", _utcnow())

    sql = """
        INSERT OR REPLACE INTO tags
            (post_id, role, city, is_fresher, model, prompt_version,
             input_tokens, output_tokens, tagged_at)
        VALUES
            (:post_id, :role, :city, :is_fresher, :model, :prompt_version,
             :input_tokens, :output_tokens, :tagged_at)
    """
    with conn:
        conn.execute(sql, {
            "post_id":       tag.get("post_id"),
            "role":          tag.get("role"),
            "city":          tag.get("city"),
            "is_fresher":    int(tag["is_fresher"]) if tag.get("is_fresher") is not None else None,
            "model":         tag.get("model"),
            "prompt_version": tag.get("prompt_version", config.PROMPT_VERSION),
            "input_tokens":  tag.get("input_tokens"),
            "output_tokens": tag.get("output_tokens"),
            "tagged_at":     tag["tagged_at"],
        })


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def copy_tags_by_content_hash(conn: sqlite3.Connection, prompt_version: str) -> int:
    """
    Give untagged posts the tag of an already-tagged post with the same
    content_hash (same title + description, e.g. one job cross-posted to
    several cities/boards). This is the "tag cache": no LLM call needed.
    Returns the number of tags copied.
    """
    sql = """
        INSERT OR REPLACE INTO tags
            (post_id, role, city, is_fresher, model, prompt_version,
             input_tokens, output_tokens, tagged_at)
        SELECT p.id, t2.role, t2.city, t2.is_fresher, t2.model, t2.prompt_version,
               0, 0, :now
        FROM posts p
        JOIN posts p2 ON p2.content_hash = p.content_hash AND p2.id != p.id
        JOIN tags t2  ON t2.post_id = p2.id AND t2.prompt_version = :pv
        LEFT JOIN tags t ON t.post_id = p.id AND t.prompt_version = :pv
        WHERE t.post_id IS NULL
          AND p.duplicate_of IS NULL
          AND p.content_hash IS NOT NULL
        GROUP BY p.id
    """
    with conn:
        cur = conn.execute(sql, {"pv": prompt_version, "now": _utcnow()})
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def get_untagged_posts(conn: sqlite3.Connection, prompt_version: str) -> list[dict]:
    """
    Return posts that have no tag row for *prompt_version* AND whose
    content_hash has not been tagged under that version yet (avoid re-tagging
    semantically identical posts).
    """
    sql = """
        SELECT p.*
        FROM posts p
        LEFT JOIN tags t ON t.post_id = p.id AND t.prompt_version = :pv
        WHERE t.post_id IS NULL
          AND p.duplicate_of IS NULL
          AND COALESCE(p.content_hash, '') NOT IN (
              SELECT DISTINCT p2.content_hash
              FROM posts p2
              JOIN tags t2 ON t2.post_id = p2.id
              WHERE t2.prompt_version = :pv
                AND p2.content_hash IS NOT NULL
          )
        ORDER BY p.first_seen DESC
    """
    rows = conn.execute(sql, {"pv": prompt_version}).fetchall()
    return [dict(r) for r in rows]


LIVE_POSTS_SQL = """
    SELECT
        p.id, p.source, p.company, p.title, p.location_raw, p.description,
        p.url, p.posted_at, p.first_seen, p.last_seen,
        t.role,
        -- structured ATS location first; the LLM only fills in when the
        -- rules found no specific city ("Unknown" / bare "India")
        COALESCE(NULLIF(NULLIF(p.city, 'Unknown'), 'India'), t.city, p.city) AS city,
        t.city AS llm_city,
        t.is_fresher, t.model, t.prompt_version
    FROM posts p
    LEFT JOIN tags t ON t.post_id = p.id
    WHERE p.duplicate_of IS NULL
      AND datetime(p.last_seen) >= datetime('now', :window)
      AND COALESCE(t.is_fresher, 1) = 1   -- hide posts the LLM judged not fresher
    ORDER BY p.last_seen DESC, p.first_seen DESC
"""

LIVE_COLUMNS = [
    "id", "source", "company", "title", "location_raw", "description",
    "url", "posted_at", "first_seen", "last_seen",
    "role", "city", "llm_city", "is_fresher", "model", "prompt_version",
]


def get_live_posts(conn: sqlite3.Connection) -> pd.DataFrame:
    """
    The single definition of "what the site shows", used by both the CSV
    export and the Streamlit app: non-duplicate posts seen within
    EXPIRY_DAYS, joined with tags, excluding posts tagged is_fresher=0
    (untagged posts are kept — the rule filter already admitted them).
    """
    rows = conn.execute(LIVE_POSTS_SQL, {"window": f"-{config.EXPIRY_DAYS} days"}).fetchall()
    return pd.DataFrame([tuple(r) for r in rows], columns=LIVE_COLUMNS)


def get_funnel_stats(conn: sqlite3.Connection) -> dict:
    """
    Return DB-side counts for monitoring / reporting.

    Only posts that passed the India + fresher rule filters are stored, so
    raw-collection and filter counts are NOT available here — the pipeline
    passes those from the ingest stage (see build_site.build).
    """
    def _scalar(sql: str, params: dict | None = None) -> int:
        row = conn.execute(sql, params or {}).fetchone()
        return row[0] if row else 0

    window = {"w": f"-{config.EXPIRY_DAYS} days"}
    stored     = _scalar("SELECT COUNT(*) FROM posts")
    duplicates = _scalar("SELECT COUNT(*) FROM posts WHERE duplicate_of IS NOT NULL")
    tagged     = _scalar(
        "SELECT COUNT(*) FROM tags t JOIN posts p ON p.id = t.post_id WHERE p.duplicate_of IS NULL"
    )
    llm_not_fresher = _scalar(
        "SELECT COUNT(*) FROM tags t JOIN posts p ON p.id = t.post_id "
        "WHERE p.duplicate_of IS NULL AND t.is_fresher = 0"
    )
    expired = _scalar(
        "SELECT COUNT(*) FROM posts WHERE duplicate_of IS NULL "
        "AND datetime(last_seen) < datetime('now', :w)", window,
    )
    live = _scalar(
        "SELECT COUNT(*) FROM posts p LEFT JOIN tags t ON t.post_id = p.id "
        "WHERE p.duplicate_of IS NULL AND datetime(p.last_seen) >= datetime('now', :w) "
        "AND COALESCE(t.is_fresher, 1) = 1", window,
    )

    return {
        "stored":          stored,
        "duplicates":      duplicates,
        "unique":          stored - duplicates,
        "tagged":          tagged,
        "untagged":        stored - duplicates - tagged,
        "llm_not_fresher": llm_not_fresher,
        "expired":         expired,
        "live":            live,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _post_params(post: dict) -> dict:
    """Return a dict with all expected post column keys (None for missing)."""
    keys = [
        "id", "source", "source_job_id", "company", "title", "location_raw",
        "description", "url", "posted_at", "first_seen", "last_seen",
        "city", "dedup_key", "duplicate_of", "content_hash",
    ]
    return {k: post.get(k) for k in keys}
