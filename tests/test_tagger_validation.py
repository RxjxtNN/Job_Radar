"""
tests/test_tagger_validation.py — Pytest tests for tagger._validate_tags().

These tests exercise the validation/cleaning logic that the Gemini tagger
applies to raw model responses before persisting them. The function signature
expected:

    _validate_tags(raw_tags: list[dict], batch_ids: list[str]) -> list[dict]

where batch_ids is the list of post IDs that were sent in the prompt.
"""
from __future__ import annotations

import pytest

import tagger


@pytest.fixture()
def mod():
    return tagger


@pytest.fixture()
def valid_tag() -> dict:
    return {
        "post_id": "abc123",
        "role": "Software Engineering",
        "city": "Bengaluru",
        "is_fresher": 1,
    }


BATCH_IDS = ["abc123", "def456", "ghi789"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestValidateTags:
    def test_valid_tag_passes_through(self, mod, valid_tag):
        """A perfectly valid tag dict is returned unchanged (minus extra keys)."""
        result = mod._validate_tags([valid_tag], BATCH_IDS)
        assert len(result) == 1
        assert result[0]["post_id"] == "abc123"
        assert result[0]["role"] == "Software Engineering"
        assert result[0]["city"] == "Bengaluru"
        assert result[0]["is_fresher"] == 1

    def test_role_not_in_role_list_mapped_to_other(self, mod, valid_tag):
        """A role that isn't in ROLE_LIST should be remapped to 'Other'."""
        tag = {**valid_tag, "role": "Completely Made Up Role XYZ"}
        result = mod._validate_tags([tag], BATCH_IDS)
        assert len(result) == 1
        assert result[0]["role"] == "Other"

    def test_id_not_in_batch_filtered_out(self, mod, valid_tag):
        """Tags whose post_id was NOT in the batch sent to the model are dropped."""
        tag = {**valid_tag, "post_id": "not-in-batch-id-999"}
        result = mod._validate_tags([tag], BATCH_IDS)
        assert result == []

    def test_mixed_valid_and_unknown_ids(self, mod, valid_tag):
        """Only tags with IDs in the batch survive."""
        ghost_tag = {**valid_tag, "post_id": "ghost-id"}
        result = mod._validate_tags([valid_tag, ghost_tag], BATCH_IDS)
        assert len(result) == 1
        assert result[0]["post_id"] == "abc123"

    def test_is_fresher_coerced_to_int_from_bool(self, mod, valid_tag):
        """is_fresher=True (bool) must be stored as 1."""
        tag = {**valid_tag, "is_fresher": True}
        result = mod._validate_tags([tag], BATCH_IDS)
        assert result[0]["is_fresher"] == 1
        assert isinstance(result[0]["is_fresher"], int)

    def test_is_fresher_coerced_to_int_zero(self, mod, valid_tag):
        """is_fresher=False must be stored as 0."""
        tag = {**valid_tag, "is_fresher": False}
        result = mod._validate_tags([tag], BATCH_IDS)
        assert result[0]["is_fresher"] == 0
        assert isinstance(result[0]["is_fresher"], int)

    def test_empty_batch_returns_empty(self, mod):
        """An empty list of raw tags yields an empty list."""
        result = mod._validate_tags([], BATCH_IDS)
        assert result == []

    def test_city_preserved_as_is(self, mod, valid_tag):
        """City is returned exactly as the model provided it (no normalisation here)."""
        tag = {**valid_tag, "city": "Navi Mumbai"}
        result = mod._validate_tags([tag], BATCH_IDS)
        assert result[0]["city"] == "Navi Mumbai"

    def test_missing_role_key_handled(self, mod, valid_tag):
        """Tags with a missing 'role' key should not raise; role defaults to None or 'Other'."""
        tag = {k: v for k, v in valid_tag.items() if k != "role"}
        # Should not raise
        result = mod._validate_tags([tag], BATCH_IDS)
        assert len(result) == 1
        # role absent → mapped to "Other" or None, either is acceptable
        assert result[0].get("role") in (None, "Other")

    def test_missing_city_key_handled(self, mod, valid_tag):
        """Tags with a missing 'city' key should not raise; city defaults to None."""
        tag = {k: v for k, v in valid_tag.items() if k != "city"}
        result = mod._validate_tags([tag], BATCH_IDS)
        assert len(result) == 1
        assert result[0].get("city") is None or result[0].get("city") == ""


# ---------------------------------------------------------------------------
# tag_posts: failures must not write permanent fallback tags
# ---------------------------------------------------------------------------


class TestTagPostsFailureHandling:
    def _conn_with_posts(self, n):
        import db  # noqa: PLC0415
        conn = db.get_connection(":memory:")
        db.create_tables(conn)
        for i in range(n):
            db.upsert_post(conn, {
                "id": f"p{i}", "source": "greenhouse", "source_job_id": str(i),
                "company": "Acme", "title": f"Intern {i}", "location_raw": "Bengaluru",
                "description": "desc", "url": "https://x", "content_hash": f"h{i}",
            })
        return conn

    def test_failed_batches_leave_posts_untagged_and_abort(self, mod, monkeypatch):
        conn = self._conn_with_posts(35)  # 4 batches of 10
        calls = []

        def boom(batch):
            calls.append(len(batch))
            raise RuntimeError("404 model not found")

        monkeypatch.setenv("GEMINI_API_KEY", "x")
        monkeypatch.setattr(mod, "_call_gemini", boom)
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)

        stats = mod.tag_posts(conn)

        assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
        assert len(calls) == mod.config.MAX_CONSECUTIVE_BATCH_FAILURES
        assert stats["untagged"] == 35
        # all posts still eligible next run
        assert len(mod.db.get_untagged_posts(conn, mod.PROMPT_VERSION)) == 35

    def test_omitted_posts_are_not_fallback_tagged(self, mod, monkeypatch):
        conn = self._conn_with_posts(3)

        def partial(batch):
            return ([{"id": "p0", "role": "Software Engineering", "city": "Bengaluru",
                      "is_fresher": True}], 100, 10)

        monkeypatch.setenv("GEMINI_API_KEY", "x")
        monkeypatch.setattr(mod, "_call_gemini", partial)
        stats = mod.tag_posts(conn)

        rows = conn.execute("SELECT post_id, model FROM tags").fetchall()
        assert [r[0] for r in rows] == ["p0"]
        assert rows[0][1] == mod.config.GEMINI_MODEL
        assert stats["tagged"] == 1 and stats["untagged"] == 2

    def test_identical_content_reuses_existing_tag(self, mod, monkeypatch):
        conn = self._conn_with_posts(2)
        conn.execute("UPDATE posts SET content_hash = 'same'")
        mod.db.save_tag(conn, {"post_id": "p0", "role": "Design", "city": "Pune",
                               "is_fresher": 1, "prompt_version": mod.PROMPT_VERSION})
        monkeypatch.setenv("GEMINI_API_KEY", "x")
        monkeypatch.setattr(mod, "_call_gemini", lambda b: pytest.fail("should not call LLM"))

        mod.tag_posts(conn)

        row = conn.execute("SELECT role, city FROM tags WHERE post_id='p1'").fetchone()
        assert tuple(row) == ("Design", "Pune")
