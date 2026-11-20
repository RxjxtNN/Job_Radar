"""
Greenhouse — public job-board API (no auth).
https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
"""
import config
from collectors._http import fetch_boards
from normalize import company_name, iso_date, make_post, strip_html

SOURCE = "greenhouse"
_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def fetch() -> list[dict]:
    return fetch_boards(SOURCE, config.GREENHOUSE_TOKENS, _URL,
                        lambda d: d.get("jobs") if isinstance(d, dict) else None,
                        slug_field="board_token")


def normalize(raw: dict):
    return make_post(
        SOURCE,
        job_id=raw.get("id"),
        title=raw.get("title"),
        url=raw.get("absolute_url"),
        company=company_name(raw.get("board_token") or "", raw.get("company_name") or ""),
        location_raw=(raw.get("location") or {}).get("name") or "",
        # content is entity-escaped HTML; strip_html unescapes it first
        description=strip_html(raw.get("content") or ""),
        posted_at=iso_date(raw.get("updated_at") or raw.get("posted_at")),
    )
