"""Tests for collectors._http (retry/cache) and normalize city extraction."""
from __future__ import annotations

import pytest
import requests


import normalize
from collectors import _http


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code, self._payload = status, payload
        self.ok = 200 <= status < 300

    def json(self):
        return self._payload


@pytest.fixture()
def no_sleep(monkeypatch):
    monkeypatch.setattr(_http.time, "sleep", lambda s: None)


def test_retries_on_timeout_then_succeeds(monkeypatch, no_sleep):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if len(calls) == 1:
            raise requests.exceptions.Timeout()
        return _Resp(200, {"jobs": [1]})

    monkeypatch.setattr(_http.requests, "get", fake_get)
    assert _http.get_json("https://x") == {"jobs": [1]}
    assert len(calls) == 2


def test_404_not_retried(monkeypatch, no_sleep):
    calls = []
    monkeypatch.setattr(_http.requests, "get", lambda url, **kw: calls.append(1) or _Resp(404))
    assert _http.get_json("https://x") is None
    assert len(calls) == 1


def test_gives_up_after_retries(monkeypatch, no_sleep):
    calls = []
    monkeypatch.setattr(_http.requests, "get", lambda url, **kw: calls.append(1) or _Resp(503))
    assert _http.get_json("https://x") is None
    assert len(calls) == _http.config.HTTP_RETRIES + 1


def test_fetch_list_caches_success_only(monkeypatch, no_sleep, tmp_path):
    monkeypatch.setattr(_http.config, "CACHE_DIR", str(tmp_path))
    responses = [_Resp(503), _Resp(503), _Resp(503), _Resp(200, {"jobs": [{"id": 1}]})]
    monkeypatch.setattr(_http.requests, "get", lambda url, **kw: responses.pop(0))
    extract = lambda d: d.get("jobs")  # noqa: E731

    assert _http.fetch_list("gh", "acme", "https://x", extract) == []  # failure not cached
    jobs = _http.fetch_list("gh", "acme", "https://x", extract, inject={"board_token": "acme"})
    assert jobs == [{"id": 1, "source": "gh", "board_token": "acme"}]
    # served from cache now — no responses left, a request would raise IndexError
    assert _http.fetch_list("gh", "acme", "https://x", extract) == jobs


@pytest.mark.parametrize("loc, city", [
    ("Gurugram; Bengaluru", "Gurugram"),
    ("Mumbai/Delhi/Bengaluru", "Mumbai"),
    ("Remote - India", "Remote"),
    ("Bengaluru, India", "Bengaluru"),
    ("Delhi NCR", "Delhi NCR"),
    ("India", "India"),
    ("Seattle, WA", "Unknown"),
    ("", "Unknown"),
])
def test_city_extraction_picks_first_mentioned(loc, city):
    assert normalize.extract_city_from_location(loc) == city


def test_every_source_module_has_fetch_and_normalize():
    """Contract for 'add a source': one collectors/<name>.py with fetch() and normalize()."""
    import importlib
    import pkgutil

    import collectors

    names = [m.name for m in pkgutil.iter_modules(collectors.__path__) if not m.name.startswith("_")]
    assert set(names) >= {"greenhouse", "lever", "ashby", "smartrecruiters"}
    for name in names:
        mod = importlib.import_module(f"collectors.{name}")
        assert callable(getattr(mod, "fetch", None)), name
        assert callable(getattr(mod, "normalize", None)), name
        assert mod.normalize({}) is None, f"{name}: incomplete record must return None"
