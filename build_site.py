"""
build_site.py — Export live posts to CSV and write a pipeline summary JSON.

Usage (programmatic):
    import build_site
    build_site.build()

Usage (CLI):
    python build_site.py
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import config
import db

log = logging.getLogger(__name__)

DATA_DIR = Path("data")
CSV_PATH = DATA_DIR / "jobs.csv"
SUMMARY_PATH = DATA_DIR / "pipeline_summary.json"


def build(conn=None, ingest_stats: dict | None = None) -> dict:
    """
    Export live posts to CSV and write pipeline_summary.json.

    *ingest_stats* is the funnel from run_pipeline's collect/filter stage
    (raw_collected, removed_location, removed_fresher, kept). Without it the
    summary only reports DB-side counts for this run.

    Returns the summary dict.
    """
    close_conn = False
    if conn is None:
        conn = db.get_connection()
        close_conn = True

    try:
        # 1. Load live posts (joins posts + tags, excludes duplicates & expired)
        df: pd.DataFrame = db.get_live_posts(conn)
        log.info("Live posts: %d", len(df))

        # 2. Export to CSV
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        df.to_csv(CSV_PATH, index=False)
        log.info("Wrote %s (%d rows)", CSV_PATH, len(df))

        # 3. Build funnel summary
        funnel = db.get_funnel_stats(conn)
        ingest = ingest_stats or {}
        summary = {
            # this run
            "collected":          ingest.get("raw_collected"),
            "removed_non_india":  ingest.get("removed_location"),
            "removed_non_fresher": ingest.get("removed_fresher"),
            "kept_this_run":      ingest.get("kept"),
            # cumulative DB state
            "stored":             funnel["stored"],
            "duplicates":         funnel["duplicates"],
            "unique":             funnel["unique"],
            "tagged":             funnel["tagged"],
            "untagged":           funnel["untagged"],
            "llm_not_fresher":    funnel["llm_not_fresher"],
            "expired":            funnel["expired"],
            "live":               len(df),
            "last_run":           datetime.now(timezone.utc).isoformat(),
        }

        SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        log.info("Wrote %s", SUMMARY_PATH)

        return summary

    finally:
        if close_conn:
            conn.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    print(json.dumps(build(), indent=2))
