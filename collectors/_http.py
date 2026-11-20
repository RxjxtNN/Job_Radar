"""
Shared HTTP + cache helpers for collectors.

Every collector does the same thing: GET a public JSON endpoint per company
(or per query/page), pull a list of jobs out of the payload, tag each job
with its source, and cache the list on disk for CACHE_TTL so re-runs during
development don't hit the APIs again.

The leading underscore keeps run_pipeline's collector auto-discovery from
treating this module as a source.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Optional

import requests

import config

log = logging.getLogger(__name__)

CACHE_TTL = timedelta(hours=24)
_RETRY_STATUSES = {429, 500, 502, 503, 504}

Extractor = Callable[[object], Optional[list]]


def _cache_path(source: str, key: str) -> Path:
    cache_dir = Path(config.CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")
    return cache_dir / f"{source}_{slug}.json"


def _read_cache(path: Path) -> Optional[list]:
    try:
        if path.exists() and datetime.now() - datetime.fromtimestamp(path.stat().st_mtime) < CACHE_TTL:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: cache read error: %s", path.name, exc)
    return None


def _write_cache(path: Path, data: list) -> None:
    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: cache write error: %s", path.name, exc)


def get_json(url: str, params: Optional[dict] = None, label: str = "") -> Optional[object]:
    """
    GET *url* and return parsed JSON, or None on failure (never raises).

    Retries up to config.HTTP_RETRIES times on timeouts, connection errors,
    429 and 5xx, with exponential back-off. 4xx (e.g. 404 for a company that
    left the ATS) fails immediately.
    """
    label = label or url
    for attempt in range(config.HTTP_RETRIES + 1):
        try:
            resp = requests.get(
                url,
                params=params,
                timeout=config.REQUEST_TIMEOUT,
                headers={"User-Agent": config.USER_AGENT},
            )
            if resp.ok:
                return resp.json()
            if resp.status_code not in _RETRY_STATUSES:
                log.warning("%s: HTTP %s — skipping", label, resp.status_code)
                return None
            reason = f"HTTP {resp.status_code}"
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            reason = type(exc).__name__
        except (requests.exceptions.RequestException, ValueError) as exc:
            log.warning("%s: request failed: %s", label, exc)
            return None

        if attempt < config.HTTP_RETRIES:
            wait = 2 ** attempt
            log.info("%s: %s, retrying in %ds", label, reason, wait)
            time.sleep(wait)
        else:
            log.warning("%s: %s after %d attempts — skipping", label, reason, attempt + 1)
    return None


def fetch_list(source: str, key: str, url: str, extract: Extractor,
               params: Optional[dict] = None, inject: Optional[dict] = None) -> list[dict]:
    """
    Return the job list for one endpoint, from cache when fresh.

    *extract* pulls the list out of the payload (returning None if the shape
    is unexpected). Every job gets ``source`` plus any *inject* fields. Only
    successful responses are cached, so a timeout is retried on the next run.
    """
    path = _cache_path(source, key)
    cached = _read_cache(path)
    if cached is not None:
        return cached

    data = get_json(url, params=params, label=f"{source}:{key}")
    if data is None:
        return []
    jobs = extract(data)
    if not isinstance(jobs, list):
        log.warning("%s:%s: unexpected payload shape — skipping", source, key)
        return []

    for job in jobs:
        job["source"] = source
        job.update(inject or {})
    _write_cache(path, jobs)
    return jobs


def fetch_boards(source: str, slugs: Iterable[str], url_template: str,
                 extract: Extractor, slug_field: str) -> list[dict]:
    """Fetch every company board in *slugs* and return all jobs as one list."""
    slugs = list(slugs)
    if not slugs:
        log.warning("%s: no companies configured — nothing to fetch", source)
        return []

    all_jobs: list[dict] = []
    for i, slug in enumerate(slugs):
        all_jobs.extend(fetch_list(source, slug, url_template.format(slug=slug),
                                   extract, inject={slug_field: slug}))
        if i < len(slugs) - 1:
            time.sleep(config.REQUEST_PAUSE)

    log.info("%s: %d jobs from %d boards", source, len(all_jobs), len(slugs))
    return all_jobs
