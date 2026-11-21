# Job Radar — Tagger Evaluation Report

Model `gemini-3.5-flash-lite`, prompt `v4`. Hand-checked sample (n=50), drawn by
`eval/make_sample.py` (stratified by predicted role, seed 42).

## Accuracy

| Metric | Correct | Accuracy |
|---|---|---|
| Role (LLM) | 42 / 50 | 84.0% |
| City (LLM tag) | 49 / 50 | 98.0% |
| City shown on site (rules first, LLM fallback) | 48 / 50 | 96.0% |
| Role and city (LLM) both correct | 41 / 50 | 82.0% |
| is_fresher (0–2 yrs) | 41 / 50 | 82.0% |

is_fresher precision 97.2% (of posts the model shows as fresher, how many truly are) ·
recall 81.4% (of truly fresher posts, how many the model keeps).

## Misses

| title | model_role | true_role | model_city | true_city | model_is_fresher | true_is_fresher |
|---|---|---|---|---|---|---|
| HR Executive - Event Management | HR and Recruiting | Operations | Bengaluru | Bengaluru | 1 | 1 |
| Product Research Specialist (W from Groww) | Product | Finance | Bengaluru | Bengaluru | 0 | 1 |
| Content Developer - Learning and Curriculum | Content and Writing | Content and Writing | Bengaluru | Bengaluru | 0 | 1 |
| Sales Development Representative - SMB | Sales and Business Development | Sales and Business Development | Bengaluru | Bengaluru | 0 | 1 |
| Associate, Marketing Technology | Marketing and Growth | Marketing and Growth | Bengaluru | Bengaluru | 0 | 1 |
| Community Coordinator - Work Management | Customer Support | Operations | Bengaluru | Bengaluru | 1 | 1 |
| Customer Experience Engineer | Customer Support | Customer Support | Pune | Pune | 0 | 1 |
| Solutions Engineer | Software Engineering | Sales and Business Development | Bengaluru | Bengaluru | 1 | 1 |
| Associate Counsellor Technical Marketing | Marketing and Growth | Marketing and Growth | Hyderabad | Bengaluru | 1 | 1 |
| Business Systems Analyst, ServiceNow | Data and Analytics | Data and Analytics | Bengaluru | Bengaluru | 0 | 1 |
| Instructional Designer- ValMo Training | Design | Content and Writing | Bengaluru | Bengaluru | 1 | 1 |
| Interaction Designer | Design | Data and Analytics | Bengaluru | Bengaluru | 1 | 1 |
| Cloud Native Technical Services Engineer | Customer Support | Customer Support | Bengaluru | Bengaluru | 1 | 0 |
| Data Analyst - Revenue Operations & Reporting | Data and Analytics | Data and Analytics | Mumbai | Mumbai | 0 | 1 |
| Talent Engagement Associate — Outbound Recruiting | Sales and Business Development | HR and Recruiting | Unknown | Unknown | 1 | 1 |
| 2026/02: HW Developer for Electronic Control Unit | Software Engineering | Other | Bengaluru | Bengaluru | 0 | 0 |
| Designer | Design | Design | Gurugram | Gurugram | 0 | 1 |

## Token Usage & Cost

| | Value |
|---|---|
| Posts tagged by an LLM call (prompt v4) | 321 |
| Input / output tokens | 145,370 / 24,032 |
| Paid-equivalent cost, all tagged posts | $0.1037 |
| **Paid-equivalent cost per 1,000 posts** | **$0.32** |
| **Actual cost** | **$0** (Gemini free tier) |

_Prices: gemini-3.5-flash-lite — $0.3/1M input, $2.5/1M output (verify at ai.google.dev/gemini-api/docs/pricing)._
