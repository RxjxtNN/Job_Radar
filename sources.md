# Sources — Terms of Service Compliance Log

This document records the legal/ToS review for every data source considered
for Job Radar, a fresher-jobs aggregator for India.  It is updated each time a source is
added, re-evaluated, or dropped.

| Source | URL Checked | Date Checked | Verdict | Notes |
|--------|-------------|--------------|---------|-------|
| **Greenhouse** | https://boards-api.greenhouse.io/v1/boards/{token}/jobs | 2026-11-18 | ✅ **Allowed** | Fully public, unauthenticated read-only API. Greenhouse's own documentation at developers.greenhouse.io explicitly states the job-board API is public. No auth token required. Rate-limit politely (REQUEST_PAUSE applied). |
| **Lever** | https://api.lever.co/v0/postings/{slug} | 2026-11-18 | ✅ **Allowed** | Public read-only postings API, documented at hire.lever.co/developer/documentation. No auth required for `GET /v0/postings/{company}`. Used only for public job listings, not candidate data. |
| **Ashby** | https://api.ashbyhq.com/posting-api/job-board/{orgNameSlug} | 2026-11-18 | ✅ **Allowed** | Public Posting API documented at developers.ashbyhq.com/reference/postingapi. No authentication required for public job-board reads. Ashby explicitly intends this for third-party aggregation. |
| **SmartRecruiters** | https://api.smartrecruiters.com/v1/companies/{companyId}/postings | 2026-11-20 | ✅ **Allowed** | Public Posting API documented at developers.smartrecruiters.com (Posting API). No authentication; intended for publishing a company's open positions on career sites and job boards. List + per-posting detail requests, `REQUEST_PAUSE` between calls, list-level pre-filter keeps detail calls low. |
| **Adzuna** | https://api.adzuna.com/v1/api/jobs/{country}/search | 2026-11-18 | ⏸️ **Dropped 2026-11-20** | Allowed with free registration, but replaced by SmartRecruiters so the project needs no third-party signup. Original note: Free tier available after registering for an API key at developer.adzuna.com. ToS permits usage for job aggregation with attribution. Rate limits apply (must register). |
| **LinkedIn** | https://www.linkedin.com/jobs/ | 2026-11-18 | 🚫 **SKIP** | Scraping explicitly prohibited by LinkedIn User Agreement §8.2. Official API (LinkedIn Talent Solutions) requires enterprise partnership. No free public jobs API available for aggregation. |
| **Naukri.com** | https://www.naukri.com | 2026-11-18 | 🚫 **SKIP** | No public API. Scraping prohibited by Naukri ToS and robots.txt. Would require a commercial partnership agreement. |
| **Indeed.com** | https://www.indeed.com | 2026-11-18 | 🚫 **SKIP** | Scraping explicitly prohibited by Indeed ToS and robots.txt. The Indeed Publisher Programme (affiliate API) is closed to new applicants as of 2023. |
| **Internshala** | https://internshala.com | 2026-11-18 | 🚫 **SKIP** | No public API. Scraping prohibited by Internshala ToS. All automated access to listings requires explicit written permission from Internshala. |

## Notes

- All **Allowed** sources are accessed read-only, with `REQUEST_PAUSE` applied between requests to stay polite.
- No job source requires an API key. The only secret is `GEMINI_API_KEY`, stored in `.env` (git-ignored) locally and in GitHub Actions secrets.
- This log must be re-verified whenever a source is added or its terms change.
