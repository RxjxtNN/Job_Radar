"""Job Radar — Streamlit frontend for fresher jobs in India."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import streamlit as st

import config
import db

st.set_page_config(
    page_title="Job Radar — Fresher Jobs in India",
    page_icon="📡",
    layout="wide",
)

DB_PATH = Path(__file__).parent / config.DB_PATH


@st.cache_data(ttl=300)
def _read_live_posts() -> pd.DataFrame:
    """Cached read. Raises on failure — Streamlit does not cache exceptions,
    so a failed read is retried on the next page load instead of serving an
    empty table for the whole TTL."""
    conn = db.get_connection(str(DB_PATH), read_only=True)
    try:
        return db.get_live_posts(conn)
    finally:
        conn.close()


def load_data() -> pd.DataFrame:
    """Load live posts via db.get_live_posts — the same query as the CSV export."""
    if not DB_PATH.exists():
        st.error("No data yet: data/jobs.db not found. Run `python run_pipeline.py` first.")
        return pd.DataFrame(columns=db.LIVE_COLUMNS)
    try:
        return _read_live_posts()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not read data/jobs.db: {exc}. Reload the page to retry.")
        return pd.DataFrame(columns=db.LIVE_COLUMNS)


# ---------------------------------------------------------------------------
# Main UI
# ---------------------------------------------------------------------------


def main() -> None:
    st.title("📡 Job Radar")
    st.caption("Fresher jobs & internships in India (0–2 years) · from public company job boards · tagged by Gemini · refreshed daily")

    # Before load_data(): a click clears the cache, so this same run re-reads
    # jobs.db. It only re-reads — collecting/tagging is the pipeline's job.
    if st.sidebar.button("🔄 Reload data", help="Re-read the latest data/jobs.db (skips the 5-minute cache)"):
        _read_live_posts.clear()

    df = load_data()

    # ---- Sidebar filters ----
    st.sidebar.header("Filters")
    search = st.sidebar.text_input("Search", placeholder="python, marketing, design…")
    role_options = sorted(df["role"].dropna().unique()) if not df.empty else []
    roles = st.sidebar.multiselect("Role", role_options, disabled=not role_options)
    if not role_options and not df.empty:
        st.sidebar.caption("Role tags appear after the Gemini tagging run.")
    cities = st.sidebar.multiselect(
        "City", sorted(df["city"].dropna().unique()) if not df.empty else []
    )
    sources = st.sidebar.multiselect(
        "Source", sorted(df["source"].unique()) if not df.empty else []
    )

    # ---- Apply filters ----
    filtered = df.copy()
    if search:
        # Title/company: substring ("intern" finds "Internship"). Description:
        # whole words only, so "intern" doesn't match "internal"/"international"
        # boilerplate that appears in most job descriptions.
        word = r"\b" + re.escape(search.strip()) + r"\b"
        mask = (
            filtered["title"].str.contains(search, case=False, na=False, regex=False)
            | filtered["company"].str.contains(search, case=False, na=False, regex=False)
            | filtered["description"].str.contains(word, case=False, na=False, regex=True)
        )
        filtered = filtered[mask]
    if roles:
        filtered = filtered[filtered["role"].isin(roles)]
    if cities:
        filtered = filtered[filtered["city"].isin(cities)]
    if sources:
        filtered = filtered[filtered["source"].isin(sources)]

    # ---- Header metrics ----
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Live Posts", len(df))
    col2.metric("Showing", len(filtered))
    last_seen = df["last_seen"].max() if not df.empty else "—"
    col3.metric("Last Refreshed", last_seen)

    st.divider()

    # ---- Results ----
    if filtered.empty:
        st.info("No jobs match the current filters. Try broadening your search.")
    else:
        for _, row in filtered.iterrows():
            with st.container():
                c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
                c1.markdown(f"**{row['title']}** — {row['company']}")
                c2.markdown(f"🏷️ {row.get('role') or '—'}")
                c3.markdown(f"📍 {row.get('city') or row.get('location_raw') or '—'}")
                apply_url = row.get("url", "")
                if apply_url:
                    c4.link_button("Apply →", apply_url)
                st.divider()

    # ---- Pipeline info ----
    with st.expander("⚙️ How this works"):
        st.markdown(
            """
**Pipeline**: GitHub Actions runs daily at 6 AM IST
1. Collect from Greenhouse, Lever, Ashby, SmartRecruiters public job-board APIs (no keys)
2. Filter for India + fresher (0–2 years of experience)
3. Deduplicate (exact match + fuzzy match)
4. Tag role, city and fresher-fit with Gemini (batches of 25); non-fresher posts are hidden
5. Publish to this page

            """
        )
        if config.REPO_URL:
            st.markdown(f"**Source code**: [{config.REPO_URL}]({config.REPO_URL})")


if __name__ == "__main__":
    main()
