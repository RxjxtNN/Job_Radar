"""
run_pipeline.py — Orchestrator for Job Radar.

Runs all pipeline stages in order:
  1. Collect   — fetch raw job posts from Greenhouse, Lever, Ashby, SmartRecruiters APIs
  2. Normalize — map each source to unified schema, strip HTML, compute hashes
  3. Filter    — keep only India locations and fresher keywords
  4. Dedup     — two-layer exact + fuzzy deduplication
  5. Tag       — classify role, city, is_fresher via Gemini Flash
  6. Publish   — export to CSV, write summary JSON for Streamlit UI

Usage:
    python run_pipeline.py                # full run
    python run_pipeline.py --dry-run      # skip Gemini API calls
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("run_pipeline")

# ---------------------------------------------------------------------------
# Load .env if present
# ---------------------------------------------------------------------------

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------

import build_site
import config
import db
import dedup
import filters
import normalize
import tagger

_COLLECTORS_PKG = "collectors"


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

def _stage_collect_and_filter(conn) -> dict:
    """
    Fetch raw jobs from all collectors, normalize to standard schema,
    filter for India + fresher criteria, and upsert matching posts to SQLite.
    Returns funnel statistics.
    """
    collectors_dir = Path(__file__).parent / "collectors"
    funnel = {
        "raw_collected": 0,
        "normalized": 0,
        "removed_location": 0,
        "removed_fresher": 0,
        "kept": 0,
    }

    collector_files = sorted(collectors_dir.glob("*.py"))
    for py_file in collector_files:
        if py_file.name.startswith("_"):
            continue
        source_name = py_file.stem
        module_name = f"{_COLLECTORS_PKG}.{source_name}"
        try:
            mod = importlib.import_module(module_name)
            if not hasattr(mod, "fetch"):
                continue
            log.info("Running collector: %s", module_name)
            raw_posts = mod.fetch()
            log.info("Collector %s returned %d raw items", source_name, len(raw_posts))
            funnel["raw_collected"] += len(raw_posts)

            for raw in raw_posts:
                src = raw.get("source") or source_name
                norm = normalize.normalize_post(raw, src)
                if not norm:
                    continue
                funnel["normalized"] += 1

                location = norm.get("location_raw") or ""
                title = norm.get("title") or ""
                desc = norm.get("description") or ""
                if not filters.is_india_location(location, f"{title}\n{desc}"):
                    funnel["removed_location"] += 1
                    continue

                if not filters.is_fresher_post(title, desc):
                    funnel["removed_fresher"] += 1
                    continue

                db.upsert_post(conn, norm)
                funnel["kept"] += 1

        except Exception as exc:
            log.error("Collector %s failed: %s", module_name, exc, exc_info=True)

    log.info(
        "Ingest summary: raw=%d normalized=%d non_india=%d non_fresher=%d kept=%d",
        funnel["raw_collected"],
        funnel["normalized"],
        funnel["removed_location"],
        funnel["removed_fresher"],
        funnel["kept"],
    )
    return funnel


def _stage_refilter(conn) -> int:
    """
    Re-apply the CURRENT India + fresher rules to every stored post and drop
    those that no longer pass. Without this, tightening a rule only takes
    effect after EXPIRY_DAYS, because previously admitted posts stay live.
    """
    rows = conn.execute("SELECT id, title, location_raw, description FROM posts").fetchall()
    stale = [
        r["id"] for r in rows
        if not (filters.is_india_location(r["location_raw"] or "", f"{r['title']}\n{r['description'] or ''}")
                and filters.is_fresher_post(r["title"], r["description"] or ""))
    ]
    if stale:
        db.delete_posts(conn, stale)
        log.info("Refilter: removed %d stored posts that fail the current rules", len(stale))
    return len(stale)


def _stage_dedup(conn) -> int:
    """Run exact + fuzzy deduplication directly in the database."""
    try:
        pairs = dedup.dedup_db(conn)
        live_df = db.get_live_posts(conn)
        unique_count = len(live_df)
        log.info(
            "Dedup stage complete: marked %d duplicates; %d unique posts remain",
            len(pairs), unique_count,
        )
        return unique_count
    except Exception as exc:
        log.error("Dedup stage failed: %s", exc, exc_info=True)
        return 0


def _stage_tag(conn, dry_run: bool) -> dict:
    """Run the Gemini tagging stage for untagged posts."""
    try:
        stats = tagger.tag_posts(conn, dry_run=dry_run)
        log.info("Tag stage result: %s", stats)
        return stats
    except Exception as exc:
        log.error("Tag stage failed: %s", exc, exc_info=True)
        return {"tagged": 0, "skipped": 0, "errors": 1, "untagged": 0, "input_tokens": 0, "output_tokens": 0}


def _stage_build(conn, ingest_stats: dict) -> dict:
    """Export live posts and write summary."""
    try:
        summary = build_site.build(conn, ingest_stats)
        return summary
    except Exception as exc:
        log.error("Build stage failed: %s", exc, exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# Summary reporting
# ---------------------------------------------------------------------------

def _print_final_summary(ingest: dict, summary: dict, tag_stats: dict, dry_run: bool) -> str:
    """Print and return the one-screen pipeline summary (the only summary printed)."""
    def row(label: str, value) -> str:
        return f"  {label:<26}: {'—' if value is None else value:>6}"

    lines = [
        "",
        "=" * 58,
        "   📡 JOB RADAR — PIPELINE SUMMARY" + (" [DRY RUN]" if dry_run else ""),
        "=" * 58,
        "  This run",
        row("Raw collected", ingest.get("raw_collected", 0)),
        row("− non-India", ingest.get("removed_location", 0)),
        row("− not fresher (rules)", ingest.get("removed_fresher", 0)),
        row("Kept & upserted", ingest.get("kept", 0)),
        row("− failing updated rules", ingest.get("removed_by_rule_change", 0)),
        row("Newly tagged", tag_stats.get("tagged", 0)),
        row("Left untagged (retry)", tag_stats.get("untagged", 0)),
        row("Tagging errors", tag_stats.get("errors", 0)),
        row("LLM tokens in / out", f"{tag_stats.get('input_tokens', 0)}/{tag_stats.get('output_tokens', 0)}"),
        "  Database",
        row("Stored posts", summary.get("stored")),
        row("− duplicates", summary.get("duplicates")),
        row("− expired (>%dd)" % config.EXPIRY_DAYS, summary.get("expired")),
        row("− not fresher (LLM)", summary.get("llm_not_fresher")),
        row("LIVE ON SITE", summary.get("live")),
        f"  Timestamp (UTC)           : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 58,
        "",
    ]
    text = "\n".join(lines)
    print(text)
    return text


def _write_github_summary(text: str) -> None:
    """Append text to $GITHUB_STEP_SUMMARY if that env var is set."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("```\n")
            fh.write(text)
            fh.write("```\n")
        log.info("Wrote pipeline summary to GITHUB_STEP_SUMMARY=%s", path)
    except OSError as exc:
        log.warning("Could not write to GITHUB_STEP_SUMMARY: %s", exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Job Radar pipeline.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip Gemini API calls (useful for testing pipeline structure).",
    )
    args = parser.parse_args()
    dry_run: bool = args.dry_run

    if dry_run:
        log.info("=== DRY RUN MODE: Gemini calls will be skipped ===")

    conn = db.get_connection()
    db.create_tables(conn)

    # 1. Ingest: Collect, Normalize, Filter, Upsert
    log.info("--- Stage 1: Collect & Filter ---")
    ingest_stats = _stage_collect_and_filter(conn)

    ingest_stats["removed_by_rule_change"] = _stage_refilter(conn)

    # 2. Dedup
    log.info("--- Stage 2: Deduplicate ---")
    _stage_dedup(conn)

    # 3. LLM Tagging
    log.info("--- Stage 3: LLM Tagging ---")
    tag_stats = _stage_tag(conn, dry_run=dry_run)

    # 4. Build & Export
    log.info("--- Stage 4: Build Site & Summarize ---")
    build_summary = _stage_build(conn, ingest_stats)

    # Leave jobs.db self-contained: switching out of WAL folds the -wal file
    # into the main file and removes it. The workflow commits only jobs.db,
    # and the app opens it read-only, which in WAL mode depends on -shm/-wal
    # side files that aren't committed. (The next run switches WAL back on.)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()

    summary_text = _print_final_summary(ingest_stats, build_summary, tag_stats, dry_run)
    _write_github_summary(summary_text)


if __name__ == "__main__":
    main()
