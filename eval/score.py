"""
eval/score.py — Score model tagging accuracy against human-annotated sample.

Usage:
    python eval/score.py [--sample eval/sample.csv] [--db data/jobs.db] [--out eval/score_report.md]

Expects eval/sample.csv to have true_role and true_city columns filled in.
Joins with model outputs from the DB and prints / saves a report.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

DEFAULT_SAMPLE = Path("eval/sample.csv")
DEFAULT_DB = Path("data/jobs.db")
DEFAULT_OUT = Path("eval/score_report.md")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

# USD per 1M tokens for the configured model (see config.MODEL_PRICING).
MODEL = config.GEMINI_MODEL
PRICE_INPUT_PER_M, PRICE_OUTPUT_PER_M = config.MODEL_PRICING.get(MODEL, (0.0, 0.0))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_sample(path: Path) -> pd.DataFrame:
    if not path.exists():
        sys.exit(f"Sample file not found: {path}\nRun: python eval/make_sample.py first.")
    df = pd.read_csv(path, dtype=str)
    missing = {"post_id", "true_role", "true_city"} - set(df.columns)
    if missing:
        sys.exit(f"Sample CSV is missing columns: {missing}")
    return df


def load_model_tags(db_path: Path, post_ids: list[str]) -> pd.DataFrame:
    if not db_path.exists():
        sys.exit(f"Database not found: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(post_ids))
    sql = f"""
        SELECT
            p.id AS post_id, p.title, p.company,
            t.role AS model_role, t.city AS model_city, t.is_fresher AS model_is_fresher,
            COALESCE(NULLIF(NULLIF(p.city, 'Unknown'), 'India'), t.city, p.city) AS shown_city,
            t.input_tokens, t.output_tokens
        FROM posts p
        JOIN tags t ON t.post_id = p.id
        WHERE p.id IN ({placeholders})
    """
    rows = conn.execute(sql, post_ids).fetchall()
    conn.close()
    if not rows:
        return pd.DataFrame(columns=["post_id", "title", "company", "model_role", "model_city",
                                     "model_is_fresher", "shown_city",
                                     "input_tokens", "output_tokens"])
    return pd.DataFrame(rows, columns=rows[0].keys())


def load_token_totals(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    # Only tags produced by an actual LLM call (copied cache hits carry 0 tokens
    # and cost nothing, so they'd understate the per-post cost).
    row = conn.execute(
        "SELECT SUM(input_tokens), SUM(output_tokens), COUNT(*) FROM tags "
        "WHERE input_tokens > 0 AND prompt_version = ?", (config.PROMPT_VERSION,)
    ).fetchone()
    conn.close()
    return {
        "input_tokens": int(row[0] or 0),
        "output_tokens": int(row[1] or 0),
        "tagged_posts": int(row[2] or 0),
    }


def normalise(s: str | float | None) -> str:
    if pd.isna(s) or s is None:
        return ""
    return str(s).strip().lower()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _md_table(df: pd.DataFrame) -> str:
    """Minimal markdown table (avoids the optional `tabulate` dependency)."""
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(r[c]).replace("|", "/") for c in cols) + " |")
    return "\n".join(lines)


def _fresher(v) -> str:
    s = normalise(v)
    return {"1": "1", "true": "1", "yes": "1", "y": "1",
            "0": "0", "false": "0", "no": "0", "n": "0"}.get(s, "")


def score(sample: pd.DataFrame, model: pd.DataFrame) -> dict:
    """Merge the hand labels with the DB's tags and compute accuracy."""
    sample = sample.drop(columns=[c for c in ("model_role", "model_city", "model_is_fresher",
                                              "shown_city", "title") if c in sample.columns])
    merged = sample.merge(model, on="post_id", how="left")
    annotated = merged[merged["true_role"].fillna("").str.strip() != ""].copy()
    n = len(annotated)
    if n == 0:
        sys.exit("No annotated rows found. Fill in true_role / true_city / true_is_fresher in the sample CSV.")

    eq = lambda a, b: annotated.apply(lambda r: normalise(r[a]) == normalise(r[b]), axis=1)  # noqa: E731
    annotated["role_ok"] = eq("model_role", "true_role")
    annotated["city_ok"] = eq("model_city", "true_city")
    annotated["shown_city_ok"] = eq("shown_city", "true_city")
    has_f = annotated["true_is_fresher"].map(_fresher) != "" if "true_is_fresher" in annotated else pd.Series(False, index=annotated.index)
    annotated["fresher_ok"] = annotated.apply(
        lambda r: _fresher(r.get("true_is_fresher")) == _fresher(r["model_is_fresher"]), axis=1)

    f = annotated[has_f]
    tp = int(((f["model_is_fresher"].map(_fresher) == "1") & (f["true_is_fresher"].map(_fresher) == "1")).sum())
    fp = int(((f["model_is_fresher"].map(_fresher) == "1") & (f["true_is_fresher"].map(_fresher) == "0")).sum())
    fn = int(((f["model_is_fresher"].map(_fresher) == "0") & (f["true_is_fresher"].map(_fresher) == "1")).sum())

    miss_mask = ~(annotated["role_ok"] & annotated["city_ok"] & (annotated["fresher_ok"] | ~has_f))
    misses = annotated[miss_mask][["title", "model_role", "true_role", "model_city", "true_city",
                                   "model_is_fresher", "true_is_fresher"]]
    return {
        "annotated": n,
        "n_role": int(annotated["role_ok"].sum()),
        "n_city": int(annotated["city_ok"].sum()),
        "n_shown_city": int(annotated["shown_city_ok"].sum()),
        "n_all": int((annotated["role_ok"] & annotated["city_ok"]).sum()),
        "n_fresher_labeled": int(has_f.sum()),
        "n_fresher": int(f["fresher_ok"].sum()),
        "tp": tp, "fp": fp, "fn": fn,
        "misses": misses,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _pct(a: int, b: int) -> str:
    return f"{a / b * 100:.1f}%" if b else "—"


def build_report(r: dict, tokens: dict) -> str:
    n = r["annotated"]
    tagged = tokens["tagged_posts"] or 1
    cost_in = tokens["input_tokens"] / 1_000_000 * PRICE_INPUT_PER_M
    cost_out = tokens["output_tokens"] / 1_000_000 * PRICE_OUTPUT_PER_M
    per_1k = (cost_in + cost_out) / tagged * 1000
    precision = _pct(r["tp"], r["tp"] + r["fp"])
    recall = _pct(r["tp"], r["tp"] + r["fn"])
    misses = _md_table(r["misses"]) if not r["misses"].empty else "_No misses._"

    return f"""# Job Radar — Tagger Evaluation Report

Model `{MODEL}`, prompt `{config.PROMPT_VERSION}`. Hand-checked sample (n={n}), drawn by
`eval/make_sample.py` (stratified by predicted role, seed 42).

## Accuracy

| Metric | Correct | Accuracy |
|---|---|---|
| Role (LLM) | {r["n_role"]} / {n} | {_pct(r["n_role"], n)} |
| City (LLM tag) | {r["n_city"]} / {n} | {_pct(r["n_city"], n)} |
| City shown on site (rules first, LLM fallback) | {r["n_shown_city"]} / {n} | {_pct(r["n_shown_city"], n)} |
| Role and city (LLM) both correct | {r["n_all"]} / {n} | {_pct(r["n_all"], n)} |
| is_fresher (0–2 yrs) | {r["n_fresher"]} / {r["n_fresher_labeled"]} | {_pct(r["n_fresher"], r["n_fresher_labeled"])} |

is_fresher precision {precision} (of posts the model shows as fresher, how many truly are) ·
recall {recall} (of truly fresher posts, how many the model keeps).

## Misses

{misses}

## Token Usage & Cost

| | Value |
|---|---|
| Posts tagged by an LLM call (prompt {config.PROMPT_VERSION}) | {tokens["tagged_posts"]:,} |
| Input / output tokens | {tokens["input_tokens"]:,} / {tokens["output_tokens"]:,} |
| Paid-equivalent cost, all tagged posts | ${cost_in + cost_out:.4f} |
| **Paid-equivalent cost per 1,000 posts** | **${per_1k:.2f}** |
| **Actual cost** | **$0** (Gemini free tier) |

_Prices: {MODEL} — ${PRICE_INPUT_PER_M}/1M input, ${PRICE_OUTPUT_PER_M}/1M output (verify at ai.google.dev/gemini-api/docs/pricing)._
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    sample = load_sample(args.sample)
    model = load_model_tags(args.db, sample["post_id"].tolist())
    tokens = load_token_totals(args.db) if args.db.exists() else {"input_tokens": 0, "output_tokens": 0, "tagged_posts": 0}
    report = build_report(score(sample, model), tokens)

    print(report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report)
    print(f"\nReport saved → {args.out}")


if __name__ == "__main__":
    main()
