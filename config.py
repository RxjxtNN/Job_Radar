"""
config.py — Central configuration for Job Radar.
All tuneable constants live here; nothing is scattered across modules.

Sections: taxonomy · fresher rules · India / city · dedup & expiry ·
LLM tagging · storage · HTTP · sources (company lists per job board).
"""
import os

# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

ROLE_LIST = [
    "Software Engineering",
    "Data and Analytics",
    "AI and ML",
    "Product",
    "Design",
    "Marketing and Growth",
    "Sales and Business Development",
    "Operations",
    "Finance",
    "HR and Recruiting",
    "Content and Writing",
    "Customer Support",
    "Other",
]

# ---------------------------------------------------------------------------
# Keyword lists (all lowercased for fast case-insensitive matching)
# ---------------------------------------------------------------------------

# Fresher detection is two-tier (see filters.is_fresher_post):
#   * STRONG signals mark a post as fresher on their own.
#   * Generic IC titles (engineer, analyst, ...) are NOT signals — they are
#     only kept when the description shows no 3+ year requirement, and the
#     LLM then makes the final is_fresher call.
FRESHER_TITLE_SIGNALS = [
    "intern", "interns", "internship", "trainee", "apprentice", "apprenticeship",
    "graduate", "new grad", "fresher", "freshers", "entry level", "entry-level",
    "junior", "jr", "associate", "early career", "early talent", "campus",
    "sde 1", "sde1", "sde-1", "sde i", "engineer 1", "engineer i", "analyst 1",
    "analyst i", "level 1", "l1", "fellowship", "fellow",
]

FRESHER_DESC_SIGNALS = [
    "fresher", "freshers", "new grad", "new graduate", "recent graduate",
    "recent graduates", "entry level", "entry-level", "campus hire",
    "campus hiring", "early career", "early talent", "no prior experience",
    "no experience required", "0-1 year", "0-1 years", "0-2 year", "0-2 years",
    "0 to 1 year", "0 to 2 years", "0-1 yrs", "0-2 yrs", "pass-out", "passout",
    "batch of 2025", "batch of 2026", "class of 2025", "class of 2026",
    "1-2 years", "1-2 yrs", "1 to 2 years", "up to 2 years", "upto 2 years",
    "final year students", "final-year students",
]

# Generic individual-contributor titles: kept only as candidates (no strong
# signal, no 3+ year requirement in description); Gemini decides is_fresher.
GENERIC_IC_TITLES = [
    "engineer", "developer", "analyst", "specialist", "coordinator",
    "executive", "representative", "designer", "writer", "recruiter",
    "consultant", "illustrator", "creator", "qa", "tester", "sde",
    "scientist", "assistant", "agent", "advisor",
    # added after reviewing rule rejections (2026-11-21)
    "officer", "receptionist", "helpdesk", "help desk", "accountant", "sourcer",
    "talent acquisition", "facilitator", "planner", "technician", "operator",
    "trainer", "administrator", "editor", "counsellor", "counselor", "caller",
    "support", "researcher",
]

# Title words that disqualify a post unless it also has an intern/trainee/
# apprentice signal. Matched with word boundaries.
SENIOR_TITLE_TERMS = [
    "senior", "sr", "lead", "principal", "staff", "director", "manager",
    "vice president", "vp", "head", "chief", "architect", "counsel",
    "avp", "partner", "president", "ii", "iii", "iv", "2", "3", "4", "5",
    "l3", "l4", "l5", "l6", "sde ii", "sde iii", "sde-2", "sde-3", "sde2", "sde3",
    "intermediate", "expert", "distinguished", "fellow engineer",
    "strategic", "enterprise", "mid market", "mid-market", "large enterprise",
]

# Placeholder / internal test postings that some ATS boards expose publicly.
JUNK_TITLE_TERMS = [
    "test employee", "test job", "test posting", "dummy", "do not apply", "test requisition",
    # evergreen "talent pool" postings, not real openings
    "employee referral", "don't see what you're looking for", "general application",
    "talent pool", "future opportunities", "expression of interest", "open application",
]

# Fresher = 0–2 years of experience. A post whose description requires more
# than this (e.g. "3+ years") is rejected, unless its title is a hard entry
# signal (intern / trainee / graduate / pass-out).
MAX_FRESHER_MIN_YEARS = 2

INDIA_KEYWORDS = [
    "india",
    "bengaluru",
    "bangalore",
    "mumbai",
    "delhi",
    "hyderabad",
    "pune",
    "chennai",
    "kolkata",
    "noida",
    "gurugram",
    "gurgaon",
    "ahmedabad",
    "remote",
    "work from home",
    "wfh",
    "anywhere in india",
]

# ---------------------------------------------------------------------------
# City normalisation map  (variant -> canonical)
# ---------------------------------------------------------------------------

CITY_MAP: dict[str, str] = {
    # Bengaluru variants
    "bangalore": "Bengaluru",
    "bengaluru": "Bengaluru",
    "bengalore": "Bengaluru",
    "blr": "Bengaluru",
    "bangalore urban": "Bengaluru",
    "bangalore rural": "Bengaluru",
    "electronic city": "Bengaluru",
    "whitefield": "Bengaluru",
    "koramangala": "Bengaluru",
    # Mumbai variants
    "mumbai": "Mumbai",
    "bombay": "Mumbai",
    "navi mumbai": "Mumbai",
    "thane": "Mumbai",
    "bom": "Mumbai",
    # Delhi / NCR
    "delhi": "Delhi",
    "new delhi": "Delhi",
    "delhi ncr": "Delhi NCR",
    "ncr": "Delhi NCR",
    "noida": "Noida",
    "gurugram": "Gurugram",
    "gurgaon": "Gurugram",
    "faridabad": "Faridabad",
    "ghaziabad": "Ghaziabad",
    # Hyderabad
    "hyderabad": "Hyderabad",
    "secunderabad": "Hyderabad",
    "hyd": "Hyderabad",
    "cyberabad": "Hyderabad",
    # Pune
    "pune": "Pune",
    "pimpri": "Pune",
    "pcmc": "Pune",
    # Chennai
    "chennai": "Chennai",
    "madras": "Chennai",
    "maa": "Chennai",
    # Kolkata
    "kolkata": "Kolkata",
    "calcutta": "Kolkata",
    # Ahmedabad
    "ahmedabad": "Ahmedabad",
    "amdavad": "Ahmedabad",
    # Jaipur
    "jaipur": "Jaipur",
    # Kochi / Kerala
    "kochi": "Kochi",
    "cochin": "Kochi",
    "thiruvananthapuram": "Thiruvananthapuram",
    "trivandrum": "Thiruvananthapuram",
    # Other major cities
    "chandigarh": "Chandigarh",
    "bhubaneswar": "Bhubaneswar",
    "indore": "Indore",
    "nagpur": "Nagpur",
    "coimbatore": "Coimbatore",
    "surat": "Surat",
    "vadodara": "Vadodara",
    "baroda": "Vadodara",
    "vizag": "Visakhapatnam",
    "visakhapatnam": "Visakhapatnam",
    "lucknow": "Lucknow",
    "patna": "Patna",
    # Remote / flexible
    "remote": "Remote",
    "work from home": "Remote",
    "wfh": "Remote",
    "anywhere in india": "Remote",
    "pan india": "Remote",
    "india": "India",
}

# ---------------------------------------------------------------------------
# Dedup & expiry
# ---------------------------------------------------------------------------

FUZZY_THRESHOLD: int = 90          # rapidfuzz title similarity (0-100), same company + city
EXPIRY_DAYS: int = 14              # posts not seen for this long leave the live view
FRESHER_SIGNAL_SCAN_CHARS: int = 3600  # description prefix scanned for fresher phrases (filters.py)
MAX_STORED_DESC_CHARS: int = 8000  # stored/filtered description length (experience reqs sit deep in JDs)

# ---------------------------------------------------------------------------
# LLM tagging (Gemini)
# ---------------------------------------------------------------------------

PROMPT_VERSION: str = "v4"         # prompts/tag_<version>.txt; bump to re-tag everything once
BATCH_SIZE: int = 25               # job posts per Gemini call (the ~1.5k-token prompt is paid once per batch)
MAX_DESC_CHARS: int = 2000         # description chars sent to Gemini (see tagger.description_excerpt)
DESC_HEAD_CHARS: int = 800         # ... of which the opening; the rest is requirement sentences
MAX_CONSECUTIVE_BATCH_FAILURES: int = 2  # abort tagging early (bad key / model) instead of hammering

# Gemini model — override with the GEMINI_MODEL env var. Prices are USD per
# 1M tokens, used only for the cost-per-1k-posts estimate in eval/score.py.
# Verify against https://ai.google.dev/gemini-api/docs/pricing before quoting.
GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL") or "gemini-3.5-flash-lite"
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.8-flash": (0.75, 3.75),
}

# ---------------------------------------------------------------------------
# Storage paths
# ---------------------------------------------------------------------------

DB_PATH: str = "data/jobs.db"
CACHE_DIR: str = "data/cache"

# ---------------------------------------------------------------------------
# HTTP client settings
# ---------------------------------------------------------------------------

REQUEST_TIMEOUT: int = 30          # seconds per HTTP request (some Lever boards are slow)
HTTP_RETRIES: int = 2               # extra attempts on timeout / 429 / 5xx (exponential back-off)
REQUEST_PAUSE: float = 0.2          # seconds between requests (rate-limit courtesy)
# Public repo URL — set after pushing; shown in the app and sent in the
# User-Agent so API operators can see who is calling.
REPO_URL: str = "https://github.com/RxjxtNN/Job_Radar"
USER_AGENT: str = "JobRadar/1.0" + (f" (+{REPO_URL})" if REPO_URL else "")

# ---------------------------------------------------------------------------
# Sources — company slugs per job board (one line per company to add)
# ---------------------------------------------------------------------------

# Display names for boards whose API doesn't send one (Lever) or whose slug
# is not a readable name. Greenhouse/Ashby/SmartRecruiters names come from
# the API itself; this map wins over everything.
COMPANY_NAMES: dict[str, str] = {
    "weekdayworks": "Weekday (hiring for clients)",
    "100ms": "100ms",
    "hackerrank": "HackerRank",
    "rubrik": "Rubrik",
    "razorpaysoftwareprivatelimited": "Razorpay",
    "stable-money1": "Stable Money",
    "hevodata": "Hevo Data",
    "fampay": "FamPay",
    "sprinto": "Sprinto",
    "gushwork": "Gushwork",
    "mactores": "Mactores",
    "kenko": "Kenko",
    "valgenesis": "ValGenesis",
    "levelai": "Level AI",
    "cred": "CRED",
    "pocketfm": "Pocket FM",
    "fi": "Fi Money",
    "mindtickle": "Mindtickle",
}

# SmartRecruiters company identifiers (public Posting API, no auth).
# The collector requests country=in, so only India postings are listed.
# Verified 2026-11-20 with India postings: Bosch ~667, Swiggy ~174,
# ServiceNow ~81, Wabtec ~78, Sutherland ~52, Experian ~41, Wise ~33,
# Freshworks ~30, Avery Dennison ~21, Continental ~13.
SMARTRECRUITERS_COMPANIES: list[str] = [
    "BoschGroup",
    "Swiggy",
    "ServiceNow",
    "Wabtec",
    "Experian",
    "Freshworks",
    "Continental",
    "Unacademy",
    "Wise",
    "Sutherland",
    "AveryDennison",
]

# Greenhouse job-board tokens for Indian companies.
# The collector calls https://boards-api.greenhouse.io/v1/boards/{token}/jobs
# and skips gracefully on 404 / non-JSON responses.
GREENHOUSE_TOKENS: list[str] = [
    # Top verified active boards with India R&D & campus hiring
    "rubrik",
    "thoughtworks",
    "inmobi",
    "elastic",
    "mongodb",
    "cloudflare",
    "stripe",
    "gitlab",
    "twilio",
    "coinbase",
    "pinterest",
    "dropbox",
    "datadog",
    "okta",
    "reddit",
    "coursera",
    "duolingo",
    "pagerduty",
    "scaleai",
    "roblox",
    "samsara",
    "checkr",
    "groww",
    "druva",
    "canonical",
    "zscaler",
    "toast",
    "affirm",
    "brex",
    "figma",
    "tanium",
    "asana",
    "databricks",
    "purestorage",
    "highradius",
    "sigmoid",
    "truecaller",
    "hackerrank",
    "sumologic",
    "airbnb",
    "observeai",
    "anthropic",
    "netskope",
    "glance",
    # found 2026-11-21 via web search for India postings on Greenhouse
    "instawork",
    "guidepoint",
    "karya",
    "emergentlabsinc",
    "inizio",
    "zinnov",
    "razorpaysoftwareprivatelimited",
    "truefoundry",
]

# Lever postings API slugs for Indian companies.
# The collector calls https://api.lever.co/v0/postings/{slug}
LEVER_COMPANIES: list[str] = [
    # Top verified active Lever boards
    "meesho",
    "mindtickle",
    "zeta",
    "paytm",
    "cred",
    "porter",
    "fi",
    "pocketfm",
    # found 2026-11-21 via web search for India postings on Lever
    "weekdayworks",
    "hevodata",
    "mactores",
    "stable-money1",
    "sprinto",
    "100ms",
    "fampay",
    "gushwork",
    "kenko",
    "valgenesis",
    "levelai",
]

# Ashby postings API slugs for Indian companies.
# The collector calls https://api.ashbyhq.com/posting-api/job-board/{slug}
ASHBY_COMPANIES: list[str] = [
    "resend",
    "sentry",
    "sarvam",
    "atlan",
    "tekion",
    "notion",
    "openai",
    "snowflake",
]
