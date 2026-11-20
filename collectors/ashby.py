"""
Ashby — public posting API (no auth).
https://api.ashbyhq.com/posting-api/job-board/{org}
"""
import config
from collectors._http import fetch_boards
from normalize import iso_date, make_post, strip_html

SOURCE = "ashby"
_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def _extract(data):
    # {"jobs": [...], "apiVersion": ...}; "jobPostings" is an older shape.
    # Unlisted postings are not public openings.
    if not isinstance(data, dict):
        return None
    jobs = data.get("jobs", data.get("jobPostings"))
    return [j for j in jobs if j.get("isListed", True)] if isinstance(jobs, list) else None


def fetch() -> list[dict]:
    return fetch_boards(SOURCE, config.ASHBY_COMPANIES, _URL, _extract, slug_field="company_slug")


def normalize(raw: dict):
    location = raw.get("locationName") or raw.get("location") or {}
    if isinstance(location, dict):
        location = location.get("name", "")
    parts = [str(location)] if location else []
    for sec in raw.get("secondaryLocations") or []:
        name = sec.get("location") if isinstance(sec, dict) else sec
        if name:
            parts.append(str(name))
    # Location names are often a bare city ("Delhi", "Remote"); the structured
    # address carries the country, which the India filter needs.
    country = ((raw.get("address") or {}).get("postalAddress") or {}).get("addressCountry") or ""
    if country and country.lower() not in " ".join(parts).lower():
        parts.append(country)

    employer = raw.get("employer") or {}
    return make_post(
        SOURCE,
        job_id=raw.get("id"),
        title=raw.get("title"),
        url=raw.get("jobUrl"),
        company=(employer.get("name") or raw.get("company") or raw.get("company_slug") or "").strip().title(),
        location_raw="; ".join(parts),
        description=strip_html(raw.get("descriptionHtml") or raw.get("descriptionPlain") or ""),
        posted_at=iso_date(raw.get("publishedAt") or raw.get("publishedDate") or raw.get("createdAt")),
    )
