"""
dedup.py — Two-layer deduplication for Job Radar.

Layer 1 – Exact dedup
    Groups posts by their ``dedup_key`` (company|title|city). When multiple
    posts share a key, the earliest (by first_seen or posted_at) is kept;
    the rest are marked as duplicates.

Layer 2 – Fuzzy dedup
    Within each (company, city), compares remaining pairs of titles with
    rapidfuzz.fuzz.ratio. Pairs at or above config.FUZZY_THRESHOLD are
    considered near-duplicates; the earlier post is kept. City is part of
    the group so the same role opened in Bengaluru and Gurugram stays as two
    listings — a fresher in either city needs to see theirs.

dedup_posts(posts) runs both layers on a list (pure, used by tests).
dedup_db(conn) recomputes duplicate links for all live posts from scratch.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import config

logger = logging.getLogger(__name__)


def _city(post: dict[str, Any]) -> str:
    """Rule-based city; older rows without the column fall back to dedup_key."""
    city = post.get("city")
    if city is None:
        key = post.get("dedup_key") or ""
        city = key.rsplit("|", 1)[-1] if "|" in key else ""
    return city.lower().strip()


def _sort_key(post: dict[str, Any]) -> str:
    """Sorting key to identify earlier post (earliest timestamp wins)."""
    return post.get("first_seen") or post.get("posted_at") or "9999-12-31T23:59:59Z"


# ---------------------------------------------------------------------------
# Layer 1: Exact deduplication
# ---------------------------------------------------------------------------


def exact_dedup(
    posts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Deduplicate posts by dedup_key (exact match).
    When duplicates exist, the post with earliest first_seen/posted_at is kept.

    Returns (unique_posts, stats_dict).
    """
    if not posts:
        return [], {"exact_dupes": 0, "exact_removed": 0, "duplicate_pairs": []}

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in posts:
        key = p.get("dedup_key") or ""
        if not key:
            grouped[f"__no_key__{p.get('id', id(p))}"].append(p)
        else:
            grouped[key].append(p)

    unique_posts: list[dict[str, Any]] = []
    duplicate_pairs: list[tuple[str, str]] = []

    for key, group in grouped.items():
        if len(group) == 1:
            unique_posts.append(group[0])
        else:
            # Sort by earliest first_seen / posted_at
            sorted_group = sorted(group, key=_sort_key)
            kept = sorted_group[0]
            unique_posts.append(kept)
            for dup in sorted_group[1:]:
                duplicate_pairs.append((dup["id"], kept["id"]))

    stats = {
        "exact_dupes": len(duplicate_pairs),
        "exact_removed": len(duplicate_pairs),
        "duplicate_pairs": duplicate_pairs,
    }
    logger.info(
        "exact_dedup: input=%d unique=%d duplicates=%d",
        len(posts), len(unique_posts), len(duplicate_pairs),
    )
    return unique_posts, stats


# ---------------------------------------------------------------------------
# Layer 2: Fuzzy deduplication
# ---------------------------------------------------------------------------


def fuzzy_dedup(
    posts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Within each company group, compare titles pairwise with rapidfuzz.
    Pairs above config.FUZZY_THRESHOLD are considered near-duplicates.
    The earlier post is kept.

    Returns (unique_posts, stats_dict).
    """
    if not posts:
        return [], {"fuzzy_dupes": 0, "fuzzy_removed": 0, "duplicate_pairs": []}

    try:
        from rapidfuzz.fuzz import ratio as fuzz_ratio
    except ImportError:
        logger.warning("rapidfuzz not installed; skipping fuzzy dedup")
        return posts, {"fuzzy_dupes": 0, "fuzzy_removed": 0, "duplicate_pairs": []}

    # Group by (company, city)
    by_company: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for p in posts:
        company = (p.get("company") or "unknown").lower().strip()
        by_company[(company, _city(p))].append(p)

    removed_ids: set[str] = set()
    duplicate_pairs: list[tuple[str, str]] = []

    for company, group in by_company.items():
        if len(group) < 2:
            continue
        # Sort group chronologically so earlier post is compared first
        sorted_group = sorted(group, key=_sort_key)
        for i in range(len(sorted_group)):
            if sorted_group[i]["id"] in removed_ids:
                continue
            for j in range(i + 1, len(sorted_group)):
                if sorted_group[j]["id"] in removed_ids:
                    continue

                title_i = (sorted_group[i].get("title") or "").strip().lower()
                title_j = (sorted_group[j].get("title") or "").strip().lower()

                score = fuzz_ratio(title_i, title_j)
                if score >= config.FUZZY_THRESHOLD:
                    kept = sorted_group[i]
                    dup = sorted_group[j]
                    removed_ids.add(dup["id"])
                    duplicate_pairs.append((dup["id"], kept["id"]))
                    logger.debug(
                        "fuzzy_dedup: score=%.1f company=%s kept=%r dup=%r",
                        score, company, kept["title"], dup["title"],
                    )

    unique_posts = [p for p in posts if p["id"] not in removed_ids]
    stats = {
        "fuzzy_dupes": len(duplicate_pairs),
        "fuzzy_removed": len(duplicate_pairs),
        "duplicate_pairs": duplicate_pairs,
    }
    logger.info(
        "fuzzy_dedup: input=%d unique=%d duplicates=%d",
        len(posts), len(unique_posts), len(duplicate_pairs),
    )
    return unique_posts, stats


# ---------------------------------------------------------------------------
# Combined pipeline
# ---------------------------------------------------------------------------


def dedup_posts(
    posts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Run exact dedup followed by fuzzy dedup on *posts*.

    Returns (unique_posts, stats) where stats has exact_dupes, fuzzy_dupes,
    total_dupes and duplicate_pairs [(duplicate_id, kept_id), ...].
    """
    after_exact, exact_stats = exact_dedup(posts or [])
    after_fuzzy, fuzzy_stats = fuzzy_dedup(after_exact)

    exact_pairs = exact_stats.get("duplicate_pairs", [])
    fuzzy_pairs = fuzzy_stats.get("duplicate_pairs", [])
    all_pairs = exact_pairs + fuzzy_pairs

    stats = {
        "exact_dupes": len(exact_pairs),
        "fuzzy_dupes": len(fuzzy_pairs),
        "total_dupes": len(all_pairs),
        "final_count": len(after_fuzzy),
        "duplicate_pairs": all_pairs,
    }
    return after_fuzzy, stats


def dedup_db(conn) -> list[tuple[str, str]]:
    """
    Recompute duplicate links for every live post and write them to the DB.

    Recomputing from scratch (instead of only deduping new posts) means a
    post hidden as a duplicate reappears once the post it pointed to closes.
    At a few hundred live posts this takes milliseconds.
    """
    import db  # local import: keeps dedup_posts usable without a DB

    _, stats = dedup_posts(db.get_live_candidates(conn))
    pairs = stats["duplicate_pairs"]
    db.set_duplicates(conn, pairs)
    return pairs
