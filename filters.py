"""
filters.py — Location and fresher-suitability filters for Job Radar.

Two independent predicates:
  1. is_india_location  — keep only Indian / remote roles
  2. is_fresher_post    — keep only entry-level / intern / fresher roles

run_pipeline applies both per post and counts what each one removes.
"""

from __future__ import annotations

import re

import config


# Foreign regions that disqualify a job unless India is explicitly present.
# Only ever matched against location / title text (never descriptions, where
# words like "us" are common English).
FOREIGN_INDICATORS = [
    "united states", "usa", "us", "u.s.", "mexico", "canada", "united kingdom",
    "uk", "germany", "ireland", "australia", "japan", "singapore", "brazil",
    "france", "spain", "poland", "netherlands", "sweden", "switzerland",
    "colombia", "argentina", "italy", "emea", "amer", "americas", "latam",
    "europe", "apac", "anz", "nyc", "new york", "london", "san francisco",
    "seattle", "toronto", "dublin", "berlin", "amsterdam", "tokyo", "sydney",
    "philippines", "indonesia", "vietnam", "uae", "dubai", "israel",
]

# Title patterns that make a post entry-level regardless of other words
# (e.g. "Senior Associate" is still out, but "Engineering Intern" is in).
_HARD_ENTRY_RE = re.compile(
    r"\b(intern|interns|internship|trainee|apprentice|apprenticeship|fresher|freshers|"
    r"new grad|graduate|campus|early talent|early career)\b|pass\s*-?\s*out",
)

# "3+ years", "2-4 yrs", "minimum of 5 years", "at least 3 years" ... The
# number must be followed by years/yrs and an experience word within 60 chars
# (or preceded by one), so "founded 10 years ago" does not count.
# A "+" or a range ("3+ years", "5-8 years") reads as a requirement on its
# own; a bare "10 years" also needs an experience word nearby.
_YEARS_RE = re.compile(
    r"(?:(minimum|min\.?|at least|atleast)\s+(?:of\s+)?)?"
    r"(\d{1,2})\s*(\+|plus)?\s*((?:-|–|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)",
)
_EXPERIENCE_RE = re.compile(r"experience|exp\b|background|track record|working in|hands-on")


def _has_word(text: str, terms) -> bool:
    return any(re.search(r"\b" + re.escape(t) + r"\b", text) for t in terms)


def required_min_years(description: str) -> int | None:
    """
    Return the smallest experience requirement (in years) mentioned in
    *description*, or None when no requirement is stated.

    Taking the minimum is deliberately lenient: a post saying "0-2 years for
    associates, 5+ for leads" is treated as open to freshers.
    """
    text = (description or "").lower()
    found: list[int] = []
    for m in _YEARS_RE.finditer(text):
        explicit = m.group(1) or m.group(3) or m.group(4)
        window = text[max(0, m.start() - 60): m.end() + 60]
        if explicit or _EXPERIENCE_RE.search(window):
            found.append(int(m.group(2)))
    return min(found) if found else None


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------


def is_india_location(location_raw: str, context: str = "") -> bool:
    """
    Return True when *location_raw* refers to India or India-remote.

    *context* (title + description) is only consulted for bare "Remote"
    locations: those are global by default and are kept only when the
    title/description mention India and the title names no foreign region.
    """
    if not location_raw:
        return False
    lower = location_raw.lower()

    india_kw = [kw for kw in config.INDIA_KEYWORDS if kw not in ("remote", "work from home", "wfh")]
    if _has_word(lower, india_kw):
        return True

    if _has_word(lower, FOREIGN_INDICATORS):
        return False

    if _has_word(lower, ("remote", "wfh", "work from home", "anywhere")):
        ctx = (context or "").lower()
        title_part = ctx.split("\n", 1)[0]
        if _has_word(title_part, FOREIGN_INDICATORS):
            return False
        return bool(re.search(r"\bindia\b|\bindian\b", ctx))

    return False


def _clean_title(title: str) -> str:
    """Lowercase; '_' → ' ' so 'IN_RBAI_Senior_Engineer' matches word rules
    (regex \b treats '_' as a word character)."""
    return (title or "").lower().replace("_", " ")


def has_hard_entry_signal(title: str) -> bool:
    """Intern / trainee / apprentice / graduate / campus / pass-out titles."""
    return bool(_HARD_ENTRY_RE.search(_clean_title(title)))


def is_senior_title(title: str) -> bool:
    """True when the title has a seniority marker and no hard entry signal."""
    t = _clean_title(title)
    return not has_hard_entry_signal(t) and _has_word(t, config.SENIOR_TITLE_TERMS)


def is_fresher_post(title: str, description: str) -> bool:
    """
    Decide whether a post is plausibly open to freshers.

    1. Senior titles (Senior/Lead/Manager/II/III/...) are rejected unless the
       title is a hard entry signal (intern, trainee, graduate, pass-out ...).
    2. A description requiring more than MAX_FRESHER_MIN_YEARS years is
       rejected unless the title is a hard entry signal.
    3. Strong title signals (associate, junior, SDE 1 ...) or description
       signals (fresher, 0-1 years, recent graduate ...) are accepted.
    4. Generic IC titles (engineer, analyst ...) with no stated experience
       requirement above the limit are accepted as *candidates*; the Gemini
       tagger makes the final is_fresher call and the UI hides is_fresher=0.
    """
    t = _clean_title(title)
    desc = (description or "").lower()
    if _has_word(t, config.JUNK_TITLE_TERMS) or re.match(r"test\b", t):
        return False  # not a real opening
    hard_entry = bool(_HARD_ENTRY_RE.search(t))

    if is_senior_title(title):
        return False

    years = required_min_years(desc)
    if not hard_entry and years is not None and years > config.MAX_FRESHER_MIN_YEARS:
        return False

    if hard_entry or _has_word(t, config.FRESHER_TITLE_SIGNALS):
        return True

    desc_sample = desc[: config.FRESHER_SIGNAL_SCAN_CHARS]
    if _has_word(desc_sample, config.FRESHER_DESC_SIGNALS):
        return True

    return _has_word(t, config.GENERIC_IC_TITLES)
