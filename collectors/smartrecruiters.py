"""
SmartRecruiters collector — public Posting API (no auth, no signup).

  List:   GET /v1/companies/{company}/postings?country=in&limit=100&offset=N
  Detail: GET /v1/companies/{company}/postings/{id}   (job description lives here)

The list has no description, and a large board (Bosch: ~670 India posts)
would need one detail request per post. So we pre-filter on the list's own
fields — drop mid/senior/director `experienceLevel` and senior titles — and
fetch details only for the survivors. Each company's enriched list is cached
for 24 h like the other collectors.

fetch() returns raw postings with the detail's `jobAd` merged in.
"""
import logging
import time

import config
import filters
from collectors._http import _cache_path, _read_cache, _write_cache, get_json
from normalize import company_name, iso_date, make_post, strip_html

log = logging.getLogger(__name__)

SOURCE = "smartrecruiters"
_LIST_URL = "https://api.smartrecruiters.com/v1/companies/{company}/postings"
_DETAIL_URL = "https://api.smartrecruiters.com/v1/companies/{company}/postings/{id}"
_PAGE = 100
# SmartRecruiters uses LinkedIn's scale: internship < entry_level < associate
# < mid_senior_level < director < executive. "Associate" (~1–3 years) can be
# within the 0–2 year fresher range, so it passes here and the description's
# stated years (rules) and the LLM decide. Bosch, for example, also tags 5–8
# year roles "Associate"; the years rule removes those.
_NON_FRESHER_LEVELS = {"mid_senior_level", "director", "executive"}


def _worth_detail(posting: dict) -> bool:
    """Cheap pre-filter on list fields, so we skip detail calls for senior roles."""
    level = (posting.get("experienceLevel") or {}).get("id", "")
    title = posting.get("name") or ""
    if level in _NON_FRESHER_LEVELS and not filters.has_hard_entry_signal(title):
        return False
    return not filters.is_senior_title(title)


def _list_postings(company: str) -> list[dict]:
    postings, offset = [], 0
    while True:
        data = get_json(_LIST_URL.format(company=company),
                        params={"country": "in", "limit": _PAGE, "offset": offset},
                        label=f"{SOURCE}:{company}")
        if not isinstance(data, dict):
            break
        page = data.get("content") or []
        postings.extend(page)
        offset += _PAGE
        if not page or offset >= data.get("totalFound", 0):
            break
        time.sleep(config.REQUEST_PAUSE)
    return postings


def _fetch_company(company: str) -> list[dict]:
    path = _cache_path(SOURCE, company)
    cached = _read_cache(path)
    if cached is not None:
        return [p for p in cached if _worth_detail(p)]  # rules may have changed

    listed = _list_postings(company)
    candidates = [p for p in listed if _worth_detail(p)]
    jobs = []
    for p in candidates:
        detail = get_json(_DETAIL_URL.format(company=company, id=p["id"]),
                          label=f"{SOURCE}:{company}:{p['id']}")
        if isinstance(detail, dict):
            p = {**p, "jobAd": detail.get("jobAd"), "postingUrl": detail.get("postingUrl")}
        p["source"] = SOURCE
        p["company_slug"] = company
        jobs.append(p)
        time.sleep(config.REQUEST_PAUSE)

    log.info("%s:%s: %d India postings, %d fetched in detail after pre-filter",
             SOURCE, company, len(listed), len(jobs))
    if listed:  # don't cache a failed/empty listing
        _write_cache(path, jobs)
    return jobs


def fetch() -> list[dict]:
    jobs = []
    for company in config.SMARTRECRUITERS_COMPANIES:
        jobs.extend(_fetch_company(company))
    log.info("%s: %d jobs from %d companies", SOURCE, len(jobs), len(config.SMARTRECRUITERS_COMPANIES))
    return jobs


def _readable(name: str) -> str:
    """SmartRecruiters often sends ALL-CAPS names ("SWIGGY"); keep mixed case ("ServiceNow")."""
    return name.title() if name.isupper() else name


def normalize(raw: dict):
    job_id = str(raw.get("id") or "").strip()
    slug = raw.get("company_slug") or (raw.get("company") or {}).get("identifier") or ""
    url = raw.get("postingUrl") or (
        f"https://jobs.smartrecruiters.com/{slug}/{job_id}" if slug and job_id else "")

    loc = raw.get("location") or {}
    location = loc.get("fullLocation") or ", ".join(
        x for x in (loc.get("city"), loc.get("region"), "India") if x)
    if loc.get("remote"):
        location = f"Remote, {location}"

    # Skip companyDescription: boilerplate (full of "international",
    # "internal"...) that adds noise to filtering, search and the LLM.
    sections = (raw.get("jobAd") or {}).get("sections") or {}
    parts = [strip_html((sections.get(k) or {}).get("text") or "")
             for k in ("jobDescription", "qualifications", "additionalInformation")]
    # The structured experience level is a strong signal; putting it in the
    # text lets both the fresher rules and the LLM see it.
    level = (raw.get("experienceLevel") or {}).get("label") or ""
    if level and level.lower() != "not applicable":
        parts.insert(0, f"Experience level: {level}.")

    return make_post(
        SOURCE,
        job_id=job_id,
        title=raw.get("name"),
        url=url,
        company=company_name(slug, _readable((raw.get("company") or {}).get("name") or "")),
        location_raw=location,
        description=" ".join(p for p in parts if p),
        posted_at=iso_date(raw.get("releasedDate")),
    )
