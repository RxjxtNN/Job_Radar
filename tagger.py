"""
tagger.py — LLM-based job post classifier using Gemini.

Reads untagged posts from the DB, calls Gemini in batches, and writes
tag records (role, city, is_fresher) back to the DB.
"""
from __future__ import annotations

import os
import json
import re
import logging
import time
from pathlib import Path
from datetime import datetime, timezone

from google import genai
from google.genai import types

import config
import db

log = logging.getLogger(__name__)

PROMPT_VERSION = config.PROMPT_VERSION
_prompt_cache: str | None = None


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------

def load_prompt() -> str:
    """Load the system prompt for PROMPT_VERSION, e.g. prompts/tag_v2.txt (cached)."""
    global _prompt_cache
    if _prompt_cache is None:
        prompt_path = Path(__file__).parent / "prompts" / f"tag_{PROMPT_VERSION}.txt"
        _prompt_cache = prompt_path.read_text(encoding="utf-8")
        log.debug("Loaded prompt from %s (%d chars)", prompt_path, len(_prompt_cache))
    return _prompt_cache


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def tag_posts(conn, dry_run: bool = False) -> dict:
    """
    Tag all untagged posts using Gemini.

    Returns a stats dict:
        tagged       — number of posts successfully tagged
        skipped      — posts skipped (dry_run)
        errors       — batches that raised an exception
        untagged     — posts left untagged (failed / omitted); retried next run
        input_tokens — total prompt tokens consumed
        output_tokens— total completion tokens consumed
    """
    copied = db.copy_tags_by_content_hash(conn, PROMPT_VERSION)
    if copied:
        log.info("Reused cached tags for %d posts with identical content", copied)
    posts = db.get_untagged_posts(conn, PROMPT_VERSION)
    total = len(posts)
    log.info("Found %d untagged posts (prompt_version=%s)", total, PROMPT_VERSION)

    stats = {"tagged": 0, "skipped": 0, "errors": 0, "untagged": 0,
             "input_tokens": 0, "output_tokens": 0}

    if total == 0:
        return stats

    if dry_run:
        log.info("[dry-run] Skipping Gemini calls for %d posts", total)
        stats["skipped"] = total
        return stats

    if not os.environ.get("GEMINI_API_KEY"):
        log.error("GEMINI_API_KEY not set — skipping tagging for %d posts", total)
        stats["untagged"] = total
        stats["skipped"] = total
        return stats

    # Chunk into batches
    batch_size = config.BATCH_SIZE
    batches = [posts[i : i + batch_size] for i in range(0, total, batch_size)]
    log.info("Processing %d batches of up to %d posts each", len(batches), batch_size)

    consecutive_failures = 0
    for batch_idx, batch in enumerate(batches):
        batch_ids = {p["id"] for p in batch}
        log.debug("Batch %d/%d: %d posts", batch_idx + 1, len(batches), len(batch))

        try:
            tags, in_tok, out_tok = _call_gemini(batch)
            validated = _validate_tags(tags, batch_ids)
            consecutive_failures = 0

            for tag in validated:
                tag["model"] = config.GEMINI_MODEL
                tag["prompt_version"] = PROMPT_VERSION
                tag["input_tokens"] = in_tok // max(len(batch), 1)   # approximate per-post
                tag["output_tokens"] = out_tok // max(len(batch), 1)
                tag["tagged_at"] = datetime.now(timezone.utc).isoformat()
                db.save_tag(conn, tag)

            stats["tagged"] += len(validated)
            stats["input_tokens"] += in_tok
            stats["output_tokens"] += out_tok

            # Posts the model omitted stay untagged and are retried next run.
            # (Previously they got a permanent "Other / Unknown" tag, which
            # silently mislabelled them forever.)
            missing = batch_ids - {t["post_id"] for t in validated}
            if missing:
                log.warning("Batch %d: model omitted %d posts; will retry next run",
                            batch_idx + 1, len(missing))
                stats["untagged"] += len(missing)

        except Exception as exc:
            consecutive_failures += 1
            stats["errors"] += 1
            stats["untagged"] += len(batch)
            log.error("Batch %d failed: %s", batch_idx + 1, exc, exc_info=True)
            # No fallback tags: the posts stay untagged and are retried next run.
            if consecutive_failures >= config.MAX_CONSECUTIVE_BATCH_FAILURES:
                remaining = sum(len(b) for b in batches[batch_idx + 1:])
                stats["untagged"] += remaining
                log.error(
                    "Aborting tagging after %d consecutive failures (check GEMINI_API_KEY "
                    "and GEMINI_MODEL=%s); %d posts left untagged for the next run",
                    consecutive_failures, config.GEMINI_MODEL, stats["untagged"],
                )
                break

        # Polite pause between batches to stay within free-tier RPM limits
        if batch_idx < len(batches) - 1:
            time.sleep(4.0)

    log.info(
        "Tagging complete — tagged=%d skipped=%d errors=%d tokens_in=%d tokens_out=%d",
        stats["tagged"], stats["skipped"], stats["errors"],
        stats["input_tokens"], stats["output_tokens"],
    )
    return stats


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _client


_REQUIREMENT_RE = re.compile(
    r"\b(years?|yrs?|experience|exp\b|fresher|graduate|qualification|requirement|"
    r"eligib|degree|b\.?\s?tech|b\.?e\b|mba|months?)", re.I)


def description_excerpt(description: str) -> str:
    """
    What the LLM sees of a description, capped at config.MAX_DESC_CHARS.

    Experience requirements usually sit near the END of a job description,
    so a plain prefix often cut them off and the model judged freshness
    blind. Instead: the opening (what the role is) plus every sentence that
    mentions years / experience / qualifications, in their original order.
    """
    if len(description) <= config.MAX_DESC_CHARS:
        return description
    head = description[: config.DESC_HEAD_CHARS]
    rest = description[config.DESC_HEAD_CHARS:]
    sentences = re.split(r"(?<=[.!?•●\n])\s+", rest)
    picked = [s.strip() for s in sentences if _REQUIREMENT_RE.search(s)]
    excerpt = head + " … " + " … ".join(picked)
    return excerpt[: config.MAX_DESC_CHARS]


def _build_batch_input(posts: list[dict]) -> str:
    """Format a list of post dicts as a compact JSON string for the Gemini prompt."""
    items = []
    for p in posts:
        desc = description_excerpt(p.get("description") or "")
        items.append({
            "id": p["id"],
            "title": p.get("title", ""),
            "location_raw": p.get("location_raw", ""),
            "description": desc,
        })
    return json.dumps(items, ensure_ascii=False)


def _call_gemini(posts: list[dict]) -> tuple[list[dict], int, int]:
    """
    Call the Gemini API for a batch of posts.

    Returns (tags, input_tokens, output_tokens).
    Retries up to 3 times on HTTP 429 with exponential back-off (max 60 s).
    """
    client = _get_client()
    batch_text = _build_batch_input(posts)

    max_retries = 3
    wait = 15  # seconds, doubles each retry, capped at 60

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=batch_text,
                config=types.GenerateContentConfig(
                    system_instruction=load_prompt(),
                    response_mime_type="application/json",
                    temperature=0,
                ),
            )

            in_tok = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
            out_tok = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

            raw = response.text
            tags = json.loads(raw)
            if not isinstance(tags, list):
                raise ValueError(f"Expected JSON array, got {type(tags).__name__}")

            log.debug(
                "Gemini returned %d tags (in=%d out=%d tokens)",
                len(tags), in_tok, out_tok,
            )
            return tags, in_tok, out_tok

        except Exception as exc:
            # Detect 429 by string match (google-genai raises generic exceptions)
            msg = str(exc).lower()
            # 429 = rate limit / quota; 503 = model overloaded ("high demand"),
            # which Google says is usually temporary — both are worth a wait.
            is_rate_limit = any(k in msg for k in (
                "429", "quota", "resource_exhausted", "rate limit", "503", "unavailable"))
            if is_rate_limit and attempt < max_retries:
                sleep_time = min(wait, 60)
                log.warning(
                    "Rate-limited (attempt %d/%d). Sleeping %ds before retry.",
                    attempt, max_retries, sleep_time,
                )
                time.sleep(sleep_time)
                wait = min(wait * 2, 60)
            else:
                raise

    raise RuntimeError("Gemini call failed after all retries")  # unreachable but explicit


def _validate_tags(tags: list[dict], batch_ids: set | list) -> list[dict]:
    """
    Validate and normalise Gemini output.

    - Accepts batch_ids as set, list, or any iterable.
    - Drops items whose 'id' or 'post_id' is not in batch_ids.
    - Maps unrecognised 'role' values to 'Other'.
    - Ensures 'is_fresher' is an int (1 or 0).
    - Ensures 'city' is a non-empty string (defaults to 'Unknown').
    """
    valid_roles = set(config.ROLE_LIST)
    id_set = set(batch_ids)
    result: list[dict] = []

    for item in tags:
        if not isinstance(item, dict):
            log.warning("Skipping non-dict tag item: %r", item)
            continue

        post_id = item.get("post_id") or item.get("id")
        if post_id not in id_set:
            log.warning("Tag id %r not in batch, skipping", post_id)
            continue

        role = item.get("role") or "Other"
        if role not in valid_roles:
            log.warning("Unknown role %r for %s, mapping to Other", role, post_id)
            role = "Other"

        city_raw = item.get("city")
        city = str(city_raw).strip() if city_raw is not None else None

        is_fresher_val = item.get("is_fresher")
        if is_fresher_val is None:
            is_fresher_int = 1
        elif isinstance(is_fresher_val, bool):
            is_fresher_int = 1 if is_fresher_val else 0
        else:
            try:
                is_fresher_int = int(is_fresher_val)
            except (ValueError, TypeError):
                is_fresher_int = 1 if is_fresher_val else 0

        result.append({
            "post_id": post_id,
            "role": role,
            "city": city,
            "is_fresher": is_fresher_int,
        })

    return result
