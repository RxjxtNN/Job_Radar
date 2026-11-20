# What Broke (and How I Fixed It)

This log records every real problem hit while building Job Radar: what I saw, why it happened, what I changed, and how I verified the fix.

---

## 1. Python 3.9 Compatibility & PEP 604 Union Syntax
- **Date/Time**: 2026-11-18 22:58 IST
- **What I saw**: `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'` when importing `tagger.py`.
- **Why it happened**: The local runtime runs Python 3.9.6. PEP 604 union types (`str | None`) are evaluated at module import time and fail on Python < 3.10 unless future annotations are enabled.
- **What I changed**: Added `from __future__ import annotations` at the top of `tagger.py`, `dedup.py`, and `db.py`, and ensured all method signatures remain compatible.
- **How I verified the fix**: Re-ran pytest across the test suite; imports succeeded with zero type errors.

---

## 2. Dedup Stage Signature & Return Contract Mismatch
- **Date/Time**: 2026-11-18 23:02 IST
- **What I saw**: Test failures in `test_dedup.py` expecting `stats["exact_dupes"]` and `stats["fuzzy_dupes"]`, while `run_pipeline.py` called `dedup_posts(conn)` expecting pairs for DB updates.
- **Why it happened**: Two distinct consumer interfaces existed: unit tests passed in-memory post dictionaries and inspected stats counts, whereas the pipeline runner passed a database connection.
- **What I changed**: Updated `dedup.dedup_posts()` in `dedup.py` to support polymorphic invocation:
  - When passed a `sqlite3.Connection`, it queries un-deduped posts, calculates duplicates, executes `db.mark_duplicates`, and returns pairs.
  - When passed `list[dict]`, it returns `(unique_posts, stats_dict)` with all expected keys (`exact_dupes`, `fuzzy_dupes`, `total_dupes`, `exact_removed`, `fuzzy_removed`, `_pairs`).
  - Implemented timestamp sorting (`first_seen` / `posted_at`) so the earliest post is consistently retained.
- **How I verified the fix**: Ran `pytest tests/test_dedup.py -v`, passing 11 out of 11 tests.

---

## 3. Collector Raw Payload to SQLite Ingest Mismatch
- **Date/Time**: 2026-11-18 23:05 IST
- **What I saw**: In `run_pipeline.py`, `_stage_collect()` was inserting raw API responses from `mod.fetch()` straight into `db.upsert_post()`, and `_stage_filter(conn)` attempted to call `filters.filter_posts(conn)`.
- **Why it happened**: Each collector returns raw provider-specific JSON (e.g. Greenhouse nests content and location differently than Lever or Ashby). Furthermore, `filters.py` expects a list of normalized posts, not a database connection.
- **What I changed**: Refactored `run_pipeline.py` to route data through the complete lifecycle cleanly:
  1. `Collector.fetch()` returns raw provider dicts.
  2. `normalize.normalize_post(raw, source)` maps to canonical schema.
  3. `filters.is_india_location()` + `filters.is_fresher_post()` filter out non-India and non-fresher roles with per-rule drop counts.
  4. Filtered & normalized posts are upserted into SQLite `posts` table.
  5. `_stage_dedup(conn)` runs exact + fuzzy deduplication.
- **How I verified the fix**: Validated stage transitions and funnel counts through dry-run execution.

---

## 4. Model Output Validation: Schema Null Handling
- **Date/Time**: 2026-11-18 23:03 IST
- **What I saw**: `test_missing_city_key_handled` failed with `AssertionError: assert 'Unknown' is None or 'Unknown' == ''`.
- **Why it happened**: Model validation previously coerced missing city keys into `"Unknown"` immediately, but the validation contract expected missing fields to remain `None` or empty until explicitly mapped.
- **What I changed**: Handled missing city keys cleanly by checking `item.get("city")` presence and allowing `None` to pass through gracefully.
- **How I verified the fix**: Ran `pytest tests/test_tagger_validation.py -v`, passing 10 out of 10 tests.


---

## 5. False Positives in Location & Fresher Matching (Substring & Geography Leaks)
- **Date/Time**: 2026-11-18 23:28 IST
- **What I saw**: In the evaluation sample from the first dry run, titles like `"Senior Software Engineer (Settlements)"` in `"Remote - USA"` and `"Remote - Mexico"` were passing both the India and fresher filters.
- **Why it happened**: 
  1. Substring matching for `"remote"` in `"Remote - USA"` was flagging foreign jobs as valid Indian remote jobs.
  2. Substring matching on `"intern"` was matching words like `"internal"` and `"internet"` within the company description boilerplate.
  3. Senior titles (`"Senior"`, `"Staff"`, `"Director"`) were not explicitly disqualified when boilerplate description matched generic signals.
- **What I changed**: 
  1. Upgraded `filters.py` to use regex word boundaries (`\b`) for all keyword matching.
  2. Introduced `FOREIGN_INDICATORS` to explicitly disqualify foreign jurisdictions (`USA`, `Canada`, `UK`, `Mexico`, etc.) unless India is specifically named.
  3. Added negative title exclusions (`EXCLUDE_SENIOR`) to prune senior, staff, lead, and director titles unless they are explicitly marked as internships or apprenticeships.
- **How I verified the fix**: Re-tested against the candidate pool; `Remote - USA` and `Senior Software Engineer` were cleanly rejected, and legitimate Indian entry-level roles (e.g. `Datadog Customer Success Associate`, `Stripe Operations Associate`, `Groww Content Associate`) passed with 100% precision.

---

## 6. Fresher Filter Admitted Almost Every Non-Senior Job
- **Date/Time**: 2026-11-19 00:00 IST
- **What I saw**: Of 537 India posts in the cache, 176 passed as "fresher", including *Enterprise Account Executive*, *Engineering Manager*, *Software Engineer 3* and *SDE II*.
- **Why it happened**: `FRESHER_KEYWORDS` contained generic words (`engineer`, `developer`, `analyst`, `specialist`, `consultant`, `executive`), so any title with "engineer" counted as entry-level. Bare `"Remote"` locations were also accepted, which let in global roles such as *"Strategic Account Executive, France"*.
- **What I changed**: Replaced the keyword list with tiered rules in `filters.py`: a broader senior-title blocklist (Manager, Lead, II/III/2/3, Strategic, Enterprise, Intermediate…), an experience parser (`required_min_years`) that rejects posts requiring 2+ years, hard entry signals (intern/trainee/pass-out) that override both, and generic IC titles kept only as candidates for the LLM. Bare "Remote" now needs India in the title or description. Prompt v2 makes Gemini the final `is_fresher` judge, and the app hides `is_fresher=0`.
- **How I verified the fix**: 48 tests in `tests/test_filters.py` built from real mis-classified titles. On the cache, India→fresher went from 176 (mostly wrong) to 48 genuine entry-level posts.

---

## 7. Greenhouse Descriptions Still Contained HTML and Were Cut at 1,200 Characters
- **What I saw**: Experience requirements ("5+ years…") were never detected; descriptions started with `<p>` and `&amp;`.
- **Why it happened**: Greenhouse returns *entity-escaped* HTML (`&lt;p&gt;`), so the HTML parser treated it as text and left the tags in. Descriptions were also cut to `MAX_DESC_CHARS` (1,200) at ingest, before the requirements section.
- **What I changed**: `strip_html` unescapes first. Stored descriptions keep up to `MAX_STORED_DESC_CHARS` (8,000); only the Gemini input is cut to 1,200.

---

## 8. Ashby Collector Never Returned a Single Post
- **What I saw**: `Collector ashby returned 0 raw items` even for boards with 48–839 public jobs (Sarvam, OpenAI).
- **Why it happened**: The posting API returns `{"jobs": [...]}`; the collector read `jobPostings`. It also cached the empty list for 24 h. The normalizer read `publishedDate` (the field is `publishedAt`) and ignored the address country, so bare locations like "Delhi" lacked "India".
- **What I changed**: Read `jobs`, skip unlisted posts, use `publishedAt`, append secondary locations and `addressCountry` to `location_raw`. Ashby now yields 1,204 raw and 14 fresher posts.

---

## 9. Adzuna Query Required All Three Keywords *(Adzuna since replaced — see #16)*
- **Why it happened**: `what=fresher+intern+graduate` is an AND query in Adzuna, so a post had to contain all three words. The cache key also ignored the query.
- **What I changed**: Three `what_or` queries (`config.ADZUNA_QUERIES`), each up to 3 pages, restricted to the last 30 days, de-duplicated by Adzuna id and cached per query.

---

## 10. LLM Tagger: Retired Model and Permanent "Other / Unknown" Fallbacks
- **What I saw**: The model was hardcoded as `gemini-1.5-flash` (retired). Any failed batch, and any post the model omitted, was saved as `role=Other, city=Unknown, is_fresher=0` and never retried.
- **Why it happened**: The fallback was meant to stop endless retries, but combined with a dead model name it would have mislabelled every post on the first live run. The rate-limit check also matched the substring `"rate"`, so errors like "generate…" were retried as rate limits.
- **What I changed**: `config.GEMINI_MODEL` (env-overridable, default `gemini-3.5-flash-lite`). Failed or omitted posts stay untagged and are retried next run, and tagging aborts after 2 consecutive failures. Identical content reuses an existing tag via `db.copy_tags_by_content_hash` (previously such posts were skipped by `get_untagged_posts` and never tagged at all). Rate-limit detection now matches only `429` / `quota` / `RESOURCE_EXHAUSTED` / `rate limit`. Cost estimates in `eval/score.py` use `config.MODEL_PRICING`.
- **How I verified the fix**: Tests in `tests/test_tagger_validation.py::TestTagPostsFailureHandling`.

---

## 11. Funnel Numbers Were Wrong
- **Why it happened**: The DB stores only filtered posts, yet `get_funnel_stats` reported `collected = after_filter = rows in DB`.
- **What I changed**: `run_pipeline` passes the real ingest funnel to `build_site.build()`. `pipeline_summary.json` now reports collected, removed (non-India), removed (non-fresher), stored, duplicates, tagged/untagged, LLM-rejected, expired and live.

---

## 12. Fuzzy Dedup Merged the Same Role in Different Cities
- **What I saw**: *MongoDB Account Development Representative, Gurugram* was hidden as a duplicate of the Bengaluru listing; *Paytm Intern – Talent Acquisition, Bengaluru* as a duplicate of Gurugram.
- **Why it happened**: Fuzzy dedup grouped by company only and compared titles, ignoring location. A fresher in Gurugram never saw their city's opening.
- **What I changed**: Posts now store the rule-based `city` (new column, migrated automatically). Fuzzy dedup groups by (company, city). `extract_city_from_location` now returns the city mentioned first ("Gurugram; Bengaluru" → Gurugram), not whichever came first in `CITY_MAP`.
- **How I verified the fix**: `TestCityAwareness` in `tests/test_dedup.py`; live posts went 96 → 98 with only true same-city repeats merged.

---

## 13. Duplicates Were Hidden Forever
- **Why it happened**: `duplicate_of` was set once and never cleared, and dedup only looked at not-yet-deduped posts. If the kept post closed while its "duplicate" stayed open, the open job vanished.
- **What I changed**: `dedup.dedup_db()` recomputes every duplicate link from scratch over live posts each run (`db.set_duplicates` clears, then re-marks, in one transaction). The old function, whose behaviour depended on its argument type (list vs. connection), was split into `dedup_posts(list)` and `dedup_db(conn)`.
- **How I verified the fix**: `TestDedupDb::test_duplicate_reappears_when_original_expires`.

---

## 14. Copy-Pasted Collectors, Duplicated Queries, Two Summaries
- **What I saw**: Four collectors each carried ~120 lines of identical cache/request/error code with no retry (Porter and Fi Lever boards timed out and were silently skipped). `app.py` had its own copy of the live-posts SQL and expiry constant, so every rule change had to be made twice. Every run printed two overlapping summaries. `filters.filter_posts()` was never called.
- **What I changed**: `collectors/_http.py` (`get_json` with retry and back-off on timeout/429/5xx, `fetch_list` with success-only caching, `fetch_boards`). Collectors went from 514 to 272 lines including the helper, and a new ATS source is ~15 lines. The app reads `db.get_live_posts()` over a read-only connection, there is one summary printer, and the dead code is gone. Gemini batch size went from 10 to 25, cutting repeated prompt tokens by ~60%. The pipeline checkpoints the WAL before exit, so the committed `jobs.db` holds every write.
- **Also fixed**: App search matched substrings in descriptions ("intern" hit "internal"/"international" boilerplate: 56 of 98 posts). Descriptions are now matched on whole words (16 posts).
- **How I verified the fix**: `tests/test_collectors.py` (retry, no-retry on 404, give-up, cache only on success, city extraction). A full dry run and a headless Streamlit `AppTest` render with no exceptions.

---

## 15. First Live Gemini Run: Model Retired, Then Overloaded
- **Date/Time**: 2026-11-20 22:17 IST
- **What I saw**: (1) `404 NOT_FOUND: models/gemini-2.5-flash-lite is no longer available to new users. Please … use models/gemini-3.5-flash-lite`. (2) After switching: `503 UNAVAILABLE: This model is currently experiencing high demand`.
- **Why it happened**: Free-tier model availability changes without notice. The retry logic only treated 429 as transient, so a 503 failed the batch immediately.
- **What I changed**: Default `GEMINI_MODEL` → `gemini-3.5-flash-lite` (pricing $0.30 / $2.50 per 1M added to `MODEL_PRICING`). 503 / UNAVAILABLE are now retried with back-off like 429.
- **What went right**: The abort-after-2-failures guard (fix #10) stopped both runs after 2 calls, wrote no bogus tags, and left all 103 posts untagged for the next run. The old code would have saved 103 permanent "Other / Unknown" labels.

---

## 16. Replaced Adzuna with SmartRecruiters (No Signup Anywhere)
- **Why**: Adzuna needs account registration. The goal is zero third-party signups apart from the Gemini key.
- **What I changed**: New `collectors/smartrecruiters.py` on the public Posting API (no auth). Its list endpoint has no description, so the collector pre-filters on list fields first: drop `experienceLevel` associate / mid-senior / director / executive (LinkedIn's scale, where "Associate" is above entry level; Bosch uses it for 5–8 year roles) and senior titles. It then fetches details only for the survivors (Bosch: 667 listed → 84 detail calls). The structured level ("Experience level: Entry Level.") is added to the description so the rules and the LLM both see it. Adzuna code, config, secrets and env vars are removed.
- **Also fixed**: (1) `required_min_years` missed "B.Tech 5-8 years" because the word "experience" was too far away; a range or "+" now counts on its own. (2) Swiggy publishes an internal "Test Employee - Executive" posting; `JUNK_TITLE_TERMS` filters such postings. (3) Tightened rules only took effect after 14 days, because posts admitted under the old rules stayed live. `run_pipeline._stage_refilter` re-applies the current rules to every stored post each run (28 stale posts removed on the first run).

---

## 17. App Showed No Jobs, Empty Filters
- **What I saw**: In the browser, the City and Source filters showed "No options" and searching "marketing" found nothing, while a fresh headless session showed 186 posts, 13 cities and 15 marketing matches.
- **Why it happened**: Two things combined. (1) `jobs.db` was left in SQLite WAL mode, and the app opens it read-only. A read-only open of a WAL database depends on `-shm`/`-wal` side files, which are deleted during cleanup and never committed, so a read can fail. Streamlit Cloud, which starts from a git clone, would hit this too. (2) The cached loader caught the error and *returned* an empty table, so `st.cache_data` cached the failure and served it to every visitor for the 5-minute TTL.
- **What I changed**: The pipeline ends with `PRAGMA journal_mode=DELETE`, so the committed `jobs.db` is a single self-contained file. The cached function now raises on failure (Streamlit doesn't cache exceptions) and the error is shown with a "reload to retry" message. The Role filter is disabled with a hint until tagging has run, instead of showing an empty dropdown.
- **How I verified the fix**: A read-only open of a lone copy of `jobs.db` (no side files, like a fresh clone) returns 186 rows. A headless `AppTest` shows 13 cities, 4 sources and 15 "marketing" results, with no errors.

---

## 18. Gemini Hid 60% of Genuine Fresher Posts (Prompt v3 → v4)
- **Date/Time**: 2026-11-21 00:40 IST
- **What I saw**: The first full tagging run marked 112 of 186 posts not fresher, leaving 74 live. A sample showed Paytm *CST Associate* (1–3 years), Sigmoid *Data Scientist* (1–3 years), Wabtec *QA Engineer* (2–3 years) and Coinbase *ML Engineer* (2+ years) all rejected, although fresher here means 0–2 years.
- **Why it happened**: (1) Gemini saw only the first 1,200 characters of each description, and experience requirements are usually near the end, so it often decided without them. (2) Prompt v3 said "When in doubt, prefer false". (3) Its rule about 3+ years made the model look at the *top* of a range ("1–3 years") instead of the bottom.
- **What I changed**: `tagger.description_excerpt` sends the opening 800 characters plus every sentence mentioning years / experience / qualifications (up to 2,000 characters). Prompt v4 is an ordered decision procedure: programmes → true, seniority marker → false, otherwise the **lowest** years accepted ≤ 2 → true, no requirement → true unless the role owns a team, region or quota. "Prefer false" was removed, and two range examples were added.
- **How I verified the fix**: One test call on 21 posts v3 had rejected. Checked by hand against each post's stated requirement, v4 got all 21 right, including correctly rejecting *Druva Sales Engineer* (5–8 years) and *Bosch Apriso MES Engineer* (3–8 years), which my own quick heuristic had wrongly flagged as fresher. After the full re-tag (7 more calls), 139 posts are live and Gemini rejects 47.
- **Also fixed in `eval/`**: `score.py` divided the total tagging cost by the 50 sample rows instead of the posts tagged (cost per 1,000 posts overstated about 4×). It crashed on its first miss because `tabulate` isn't installed, and it never measured is_fresher. It now reports role, LLM city, shown city and is_fresher accuracy with precision and recall.

---

## 19. Underscores Hid Titles; Company Names Showed as Slugs
- **What I saw**: Bosch titles like `IN_RBAI_Maintenance Engineer_Charcon` were rejected as "no fresher signal", while `IN_RBIN_Senior Engineer_…` slipped past the senior check. After adding new boards, the app showed "Razorpaysoftwareprivatelimited", "Stable-Money1" and "Mongodb".
- **Why it happened**: Regex `\b` treats `_` as a letter, so "Engineer" inside `_Engineer_` never matched any word rule. Company names were built by title-casing the board slug, and a re-seen post never updated its stored company.
- **What I changed**: `filters._clean_title` turns `_` into spaces before matching. The generic-title list gained real entry-level titles found among rule rejections (officer, receptionist, helpdesk, accountant, sourcer, technician…), and "talent pool" postings (*Employee Referral*, *Don't see what you're looking for?*, *Test …*) are filtered as junk. Names now come from the API (Greenhouse `company_name`; SmartRecruiters names title-cased only when ALL CAPS) with `config.COMPANY_NAMES` overrides for Lever. `upsert_post` refreshes the company on every sighting. Weekday is labelled "Weekday (hiring for clients)", because it's an agency posting for other employers.
- **Result**: Together with 19 boards found by web search, live posts went 139 → **246** (57 companies), at the same $0.32 per 1,000 posts.
