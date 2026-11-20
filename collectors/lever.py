"""
Lever — public postings API (no auth).
https://api.lever.co/v0/postings/{company}?mode=json
"""
import config
from collectors._http import fetch_boards
from normalize import company_name, make_post, ms_epoch_to_iso, strip_html

SOURCE = "lever"
_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"


def fetch() -> list[dict]:
    return fetch_boards(SOURCE, config.LEVER_COMPANIES, _URL,
                        lambda d: d if isinstance(d, list) else None,
                        slug_field="company_slug")


def normalize(raw: dict):
    categories = raw.get("categories") or {}
    location = categories.get("location") or categories.get("allLocations") or ""
    if isinstance(location, list):
        location = ", ".join(location)

    description = raw.get("descriptionPlain") or strip_html(raw.get("description") or "")
    extra = raw.get("additionalPlain") or strip_html(raw.get("additional") or "")
    if extra:
        description = f"{description}\n\n{extra}"

    return make_post(
        SOURCE,
        job_id=raw.get("id"),
        title=raw.get("text"),
        url=raw.get("hostedUrl"),
        company=company_name(raw.get("company_slug") or raw.get("company") or ""),
        location_raw=location,
        description=description,
        posted_at=ms_epoch_to_iso(raw.get("createdAt")),  # Lever uses Unix ms
    )
