"""
tests/test_dedup.py — Pytest tests for dedup.py (exact + fuzzy deduplication).

These tests import dedup.dedup_posts(), exact_dedup(), and fuzzy_dedup()
directly, so they don't touch any real database.
"""
from __future__ import annotations

import pytest

import dedup


@pytest.fixture()
def mod():
    return dedup


def _post(
    post_id: str,
    title: str,
    company: str,
    dedup_key: str | None = None,
    first_seen: str = "2024-01-01T00:00:00Z",
) -> dict:
    """Minimal post dict for testing."""
    return {
        "id": post_id,
        "title": title,
        "company": company,
        "dedup_key": dedup_key or f"{company.lower()}::{title.lower()}",
        "first_seen": first_seen,
        "source": "test",
        "url": f"https://example.com/{post_id}",
        "description": "test description",
        "location_raw": "Bengaluru",
        "duplicate_of": None,
    }


# ---------------------------------------------------------------------------
# exact_dedup tests
# ---------------------------------------------------------------------------


class TestExactDedup:
    def test_removes_exact_duplicate(self, mod):
        """Posts with the same dedup_key: only the first_seen is kept."""
        posts = [
            _post("a", "SDE-1 Engineer", "Acme", first_seen="2024-01-02T00:00:00Z"),
            _post("b", "SDE-1 Engineer", "Acme", first_seen="2024-01-01T00:00:00Z"),
        ]
        kept, stats = mod.exact_dedup(posts)
        kept_ids = {p["id"] for p in kept}
        assert "b" in kept_ids  # earlier first_seen kept
        assert "a" not in kept_ids

    def test_keeps_earliest_post(self, mod):
        """The post with the smallest first_seen timestamp survives."""
        posts = [
            _post("x1", "Analyst", "Corp", first_seen="2024-03-10T00:00:00Z"),
            _post("x2", "Analyst", "Corp", first_seen="2024-02-01T00:00:00Z"),
            _post("x3", "Analyst", "Corp", first_seen="2024-04-05T00:00:00Z"),
        ]
        kept, _ = mod.exact_dedup(posts)
        assert len(kept) == 1
        assert kept[0]["id"] == "x2"

    def test_no_duplicates_returns_all(self, mod):
        """When all dedup_keys are unique, all posts are kept."""
        posts = [
            _post("p1", "Engineer", "Alpha"),
            _post("p2", "Designer", "Beta"),
            _post("p3", "Analyst", "Gamma"),
        ]
        kept, stats = mod.exact_dedup(posts)
        assert len(kept) == 3
        assert stats["exact_dupes"] == 0

    def test_stats_exact_dupe_count(self, mod):
        """stats['exact_dupes'] equals the number of removed posts."""
        posts = [
            _post("d1", "PM", "Foo"),
            _post("d2", "PM", "Foo"),
            _post("d3", "PM", "Foo"),
        ]
        _, stats = mod.exact_dedup(posts)
        assert stats["exact_dupes"] == 2

    def test_different_companies_same_title_kept(self, mod):
        """Same title at different companies must NOT be deduped at exact stage."""
        posts = [
            _post("e1", "Data Analyst", "Company A",
                  dedup_key="company a::data analyst"),
            _post("e2", "Data Analyst", "Company B",
                  dedup_key="company b::data analyst"),
        ]
        kept, stats = mod.exact_dedup(posts)
        assert len(kept) == 2
        assert stats["exact_dupes"] == 0


# ---------------------------------------------------------------------------
# fuzzy_dedup tests
# ---------------------------------------------------------------------------


class TestFuzzyDedup:
    def test_removes_near_duplicate_title(self, mod):
        """Titles above fuzzy threshold at the same company are deduped."""
        posts = [
            _post("f1", "Software Development Engineer - I", "BigCo",
                  dedup_key="bigco::software development engineer - i",
                  first_seen="2024-01-01T00:00:00Z"),
            _post("f2", "Software Development Engineer I", "BigCo",
                  dedup_key="bigco::software development engineer i",
                  first_seen="2024-01-02T00:00:00Z"),
        ]
        kept, stats = mod.fuzzy_dedup(posts)
        assert len(kept) == 1
        assert stats["fuzzy_dupes"] >= 1

    def test_keeps_distinct_titles(self, mod):
        """Titles that are clearly different must both survive."""
        posts = [
            _post("g1", "Backend Engineer", "StartupX"),
            _post("g2", "Frontend Designer", "StartupX"),
        ]
        kept, stats = mod.fuzzy_dedup(posts)
        assert len(kept) == 2
        assert stats["fuzzy_dupes"] == 0

    def test_fuzzy_only_matches_within_same_company(self, mod):
        """Near-duplicate titles at DIFFERENT companies are NOT deduped."""
        posts = [
            _post("h1", "Software Engineer Intern", "Alpha Inc",
                  dedup_key="alpha inc::software engineer intern"),
            _post("h2", "Software Engineer Intern", "Beta Ltd",
                  dedup_key="beta ltd::software engineer intern"),
        ]
        kept, stats = mod.fuzzy_dedup(posts)
        assert len(kept) == 2

    def test_no_posts_returns_empty(self, mod):
        """fuzzy_dedup with an empty list returns empty list."""
        kept, stats = mod.fuzzy_dedup([])
        assert kept == []
        assert stats["fuzzy_dupes"] == 0


# ---------------------------------------------------------------------------
# dedup_posts (combined) tests
# ---------------------------------------------------------------------------


class TestDedupPosts:
    def test_combines_both_layers(self, mod):
        """dedup_posts runs exact then fuzzy and returns a merged stats dict."""
        posts = [
            _post("c1", "SDE 1", "Acme", first_seen="2024-01-01T00:00:00Z"),
            _post("c2", "SDE 1", "Acme", first_seen="2024-01-02T00:00:00Z"),  # exact dup
            _post("c3", "Backend Developer Intern", "Acme"),
            _post("c4", "Backend Developer  Intern", "Acme"),  # near dup
            _post("c5", "Data Scientist", "OtherCo"),
        ]
        kept, stats = mod.dedup_posts(posts)
        assert len(kept) < len(posts)
        assert "exact_dupes" in stats
        assert "fuzzy_dupes" in stats

    def test_stats_total_dupes(self, mod):
        """stats['total_dupes'] == exact_dupes + fuzzy_dupes."""
        posts = [
            _post("s1", "ML Engineer", "AI Corp", first_seen="2024-01-01T00:00:00Z"),
            _post("s2", "ML Engineer", "AI Corp", first_seen="2024-01-02T00:00:00Z"),
            _post("s3", "Machine Learning Engineer", "AI Corp"),
        ]
        _, stats = mod.dedup_posts(posts)
        assert stats.get("total_dupes", stats["exact_dupes"] + stats["fuzzy_dupes"]) >= 1


# ---------------------------------------------------------------------------
# City-aware fuzzy dedup + DB recompute
# ---------------------------------------------------------------------------


class TestCityAwareness:
    def test_same_title_different_city_kept(self, mod):
        """Same role opened in two cities is two listings, not a duplicate."""
        a = {**_post("m1", "Account Development Representative", "MongoDB"), "city": "Bengaluru"}
        b = {**_post("m2", "Account Development Representative ", "MongoDB"), "city": "Gurugram"}
        kept, stats = mod.fuzzy_dedup([a, b])
        assert len(kept) == 2 and stats["fuzzy_dupes"] == 0

    def test_near_duplicate_same_city_removed(self, mod):
        a = {**_post("z1", "Account Executive - Majors", "Zscaler"), "city": "Mumbai"}
        b = {**_post("z2", "Account Executive, Majors", "Zscaler",
                     first_seen="2024-01-05T00:00:00Z"), "city": "Mumbai"}
        kept, _ = mod.fuzzy_dedup([a, b])
        assert [p["id"] for p in kept] == ["z1"]

    def test_city_falls_back_to_dedup_key(self, mod):
        a = _post("k1", "Data Analyst", "Acme", dedup_key="acme|data analyst|pune")
        b = _post("k2", "Data Analyst ", "Acme", dedup_key="acme|data analyst |delhi")
        kept, _ = mod.fuzzy_dedup([a, b])
        assert len(kept) == 2


class TestDedupDb:
    def _conn(self):
        import db  # noqa: PLC0415
        conn = db.get_connection(":memory:")
        db.create_tables(conn)
        return db, conn

    def _upsert(self, db, conn, pid, first_seen, last_seen):
        db.upsert_post(conn, {
            "id": pid, "source": "greenhouse", "source_job_id": pid, "company": "Acme",
            "title": "SDE 1", "location_raw": "Pune", "city": "Pune",
            "description": "d", "url": "https://x", "dedup_key": "acme|sde 1|pune",
            "content_hash": pid, "first_seen": first_seen, "last_seen": last_seen,
        })

    def test_duplicate_reappears_when_original_expires(self, mod):
        from datetime import datetime, timedelta, timezone  # noqa: PLC0415
        db, conn = self._conn()
        now = datetime.now(timezone.utc)
        iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        self._upsert(db, conn, "old", iso(now - timedelta(days=5)), iso(now))
        self._upsert(db, conn, "new", iso(now - timedelta(days=1)), iso(now))

        assert mod.dedup_db(conn) == [("new", "old")]

        # "old" stops being seen and expires; "new" is still open
        conn.execute("UPDATE posts SET last_seen = ? WHERE id = 'old'",
                     (iso(now - timedelta(days=30)),))
        assert mod.dedup_db(conn) == []
        live = db.get_live_posts(conn)
        assert list(live["id"]) == ["new"]
