"""
normalize.py — Shared helpers for turning raw source records into posts.

Each source module in collectors/ owns its own `normalize(raw)`, because only
it knows its payload's shape. They all end in `make_post(...)`, which builds
the canonical posts row (ids, hashes, dedup key, rule-based city) the same
way for every source. `normalize_post(raw, source)` dispatches to the right
collector and never raises.
"""

from __future__ import annotations

import hashlib
import html
import importlib
import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional

import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Low-level text utilities
# ---------------------------------------------------------------------------


class _HTMLStripper(HTMLParser):
    """Minimal HTMLParser subclass that accumulates visible text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:  # noqa: D401
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def strip_html(text: str) -> str:
    """
    Remove all HTML tags from *text* and return plain text.
    Also collapses multiple whitespace characters into a single space.
    """
    if not text:
        return ""
    # Greenhouse returns entity-escaped HTML ("&lt;p&gt;"), so unescape first;
    # otherwise the parser sees plain text and the tags survive.
    if "&lt;" in text or "&gt;" in text:
        text = html.unescape(text)
    stripper = _HTMLStripper()
    stripper.feed(text)
    cleaned = stripper.get_text()
    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# ---------------------------------------------------------------------------
# Hashing / key utilities
# ---------------------------------------------------------------------------


def compute_id(source: str, source_job_id: str) -> str:
    """Return a stable SHA-1 hex string identifying a source + job-id pair."""
    raw = f"{source}::{source_job_id}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def compute_content_hash(title: str, desc: str) -> str:
    """
    Return a SHA-1 hex of lowercased (title, description) content.
    Used to avoid re-tagging semantically identical posts from different sources.
    """
    normalised = f"{(title or '').lower().strip()}|{(desc or '').lower().strip()}"
    return hashlib.sha1(normalised.encode("utf-8")).hexdigest()


def make_dedup_key(company: str, title: str, city: str) -> str:
    """
    Return a normalised string key used for exact deduplication.
    Format: ``<company>|<title>|<city>`` — all lowercased and stripped.
    """
    parts = [
        (company or "").lower().strip(),
        (title or "").lower().strip(),
        (city or "").lower().strip(),
    ]
    return "|".join(parts)


def extract_city_from_location(location_raw: str) -> str:
    """
    Return the canonical city (config.CITY_MAP) mentioned EARLIEST in
    *location_raw*, e.g. "Gurugram; Bengaluru" -> "Gurugram",
    "Remote - India" -> "Remote". Returns "Unknown" when no city matches and
    "India" when only the country is named.
    """
    if not location_raw:
        return "Unknown"
    lower_loc = location_raw.lower()
    best_rank, best = (len(lower_loc) + 1, 0), "Unknown"
    for variant, canonical in config.CITY_MAP.items():
        m = re.search(r"(?<![a-z])" + re.escape(variant) + r"(?![a-z])", lower_loc)
        # Earliest match wins; on a tie the longer variant ("delhi ncr" > "delhi").
        if m and (m.start(), -len(variant)) < best_rank:
            best_rank, best = (m.start(), -len(variant)), canonical
    return best


# ---------------------------------------------------------------------------
# Canonical post builder + dispatch
# ---------------------------------------------------------------------------


def company_name(slug: str, api_name: str = "") -> str:
    """Readable company name: config override > name from the API > Title-cased slug."""
    slug = (slug or "").strip()
    return (config.COMPANY_NAMES.get(slug.lower())
            or (api_name or "").strip()
            or slug.replace("-", " ").title())


def make_post(source: str, job_id, title: str, url: str, company: str,
              location_raw: str, description: str, posted_at: Optional[str]) -> Optional[dict]:
    """
    Build the canonical posts row. Returns None when id, title or url is
    missing (the record can't be shown or linked).
    """
    job_id = str(job_id or "").strip()
    title = (title or "").strip()
    url = (url or "").strip()
    if not (job_id and title and url):
        logger.debug("%s: skipping incomplete record id=%s", source, job_id)
        return None

    location_raw = str(location_raw or "")
    description = (description or "")[: config.MAX_STORED_DESC_CHARS]
    city = extract_city_from_location(location_raw)
    return {
        "id":            compute_id(source, job_id),
        "source":        source,
        "source_job_id": job_id,
        "company":       company,
        "title":         title,
        "location_raw":  location_raw,
        "description":   description,
        "url":           url,
        "posted_at":     posted_at,
        "first_seen":    None,  # db.upsert_post sets these
        "last_seen":     None,
        "dedup_key":     make_dedup_key(company, title, city),
        "city":          city,
        "duplicate_of":  None,
        "content_hash":  compute_content_hash(title, description),
    }


def normalize_post(raw: dict, source: str) -> Optional[dict]:
    """
    Dispatch to collectors.<source>.normalize(raw).
    Returns None when the source is unknown or the record is incomplete.
    """
    try:
        handler = importlib.import_module(f"collectors.{source}").normalize
    except (ImportError, AttributeError):
        logger.warning("normalize_post: unknown source %r", source)
        return None
    try:
        return handler(raw)
    except Exception:
        logger.exception("normalize_post: error normalising %s record", source)
        return None


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def iso_date(value: Optional[str]) -> Optional[str]:
    """
    Best-effort: return value as-is if it looks like ISO-8601, else None.
    Strips trailing Z/timezone for SQLite compatibility.
    """
    if not value:
        return None
    value = str(value).strip()
    # Accept ISO-8601 patterns; SQLite stores as text anyway
    if re.match(r"\d{4}-\d{2}-\d{2}", value):
        return value
    return None


def ms_epoch_to_iso(ms: int | str) -> Optional[str]:
    """Convert a Unix-milliseconds epoch to an ISO-8601 string."""
    if not ms:
        return None
    try:
        ts = int(ms) / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None
