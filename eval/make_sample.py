"""
eval/make_sample.py — Draw a stratified evaluation sample from the jobs DB.

Usage:
    python eval/make_sample.py [--db data/jobs.db] [--out eval/sample.csv] [--n 50]

The script selects posts proportionally across role categories (stratified by
the model's role tag), using a fixed random seed of 42 so the sample is
reproducible. It writes eval/sample.csv with model outputs pre-filled and
true_role / true_city / true_is_fresher left blank for human annotation.

The description column is the same excerpt the model saw
(tagger.description_excerpt); open the url to check the full post.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tagger import description_excerpt  # noqa: E402

# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

DEFAULT_DB = Path("data/jobs.db")
DEFAULT_OUT = Path("eval/sample.csv")
SAMPLE_N = 50
SEED = 42

OUTPUT_COLUMNS = [
    "post_id",
    "source",
    "company",
    "title",
    "location_raw",
    "url",
    "description_excerpt",
    "model_role",
    "model_city",
    "shown_city",
    "model_is_fresher",
    "true_role",
    "true_city",
    "true_is_fresher",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_tagged_posts(db_path: Path) -> pd.DataFrame:
    """Return all non-duplicate, tagged posts from the DB."""
    if not db_path.exists():
        sys.exit(f"Database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT
            p.id          AS post_id,
            p.source,
            p.company,
            p.title,
            p.location_raw,
            p.description,
            p.url,
            t.role        AS model_role,
            t.city        AS model_city,
            t.is_fresher  AS model_is_fresher,
            -- what the site displays: rule city first, LLM city as fallback
            COALESCE(NULLIF(NULLIF(p.city, 'Unknown'), 'India'), t.city, p.city) AS shown_city
        FROM posts p
        JOIN tags t ON t.post_id = p.id          -- tagged posts only
        WHERE p.duplicate_of IS NULL
    """
    rows = conn.execute(sql).fetchall()
    conn.close()
    if not rows:
        sys.exit("No live posts found in the database. Run the pipeline first.")
    return pd.DataFrame(rows, columns=rows[0].keys())


def stratified_sample(df: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    """
    Sample *n* rows proportionally from each model_role stratum.
    Falls back to random sampling when n >= len(df).
    """
    if len(df) <= n:
        return df.sample(frac=1, random_state=seed).reset_index(drop=True)

    role_col = "model_role"
    # Normalise missing roles to "Other"
    df = df.copy()
    df[role_col] = df[role_col].fillna("Other")

    counts = df[role_col].value_counts()
    n_roles = len(counts)
    base = n // n_roles
    remainder = n % n_roles

    frames: list[pd.DataFrame] = []
    rng = pd.Series(counts.index, name=role_col)
    for i, role in enumerate(rng):
        quota = base + (1 if i < remainder else 0)
        stratum = df[df[role_col] == role]
        take = min(quota, len(stratum))
        frames.append(stratum.sample(take, random_state=seed))

    sample = pd.concat(frames, ignore_index=True)

    # If we got fewer than n (some strata too small), top up with random rows
    if len(sample) < n:
        already = set(sample["post_id"])
        rest = df[~df["post_id"].isin(already)]
        need = n - len(sample)
        extra = rest.sample(min(need, len(rest)), random_state=seed)
        sample = pd.concat([sample, extra], ignore_index=True)

    return sample.sample(frac=1, random_state=seed).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Draw an evaluation sample from the jobs DB.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Path to jobs.db")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output CSV path")
    parser.add_argument("--n", type=int, default=SAMPLE_N, help="Number of rows to sample")
    args = parser.parse_args()

    df = load_tagged_posts(args.db)
    sample = stratified_sample(df, args.n, SEED)

    # Add empty ground-truth columns for human annotation
    sample["description_excerpt"] = sample["description"].fillna("").map(description_excerpt)
    sample["true_role"] = ""
    sample["true_city"] = ""
    sample["true_is_fresher"] = ""

    # Reorder / select columns
    sample = sample.reindex(columns=OUTPUT_COLUMNS)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    sample.to_csv(args.out, index=False)
    print(f"Wrote {len(sample)} rows → {args.out}")
    print("Fill in true_role, true_city and true_is_fresher (1/0) for every row,")
    print("then run: python eval/score.py")


if __name__ == "__main__":
    main()
