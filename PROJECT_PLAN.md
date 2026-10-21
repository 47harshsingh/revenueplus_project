# RevenuePulse — Project Plan for Claude Code

> **How to use this file:** put it in the root of an empty `revenuepulse` folder, start Claude Code there, and send:
> *"Read PROJECT_PLAN.md fully. Then create CLAUDE.md from sections 2, 3 and 11, and propose the plan for Milestone 1 in plan mode. Don't write code yet."*
>
> **Who I am:** Harsh, an aspiring data analyst. I'm strong in SQL and Python/pandas. I'm **new** to Git, the terminal, APIs, FastAPI, Next.js/React, deployment and GitHub Actions. I'm building this in a 4-day sprint to learn the full workflow of shipping a data application.

---

## 1. What we are building

RevenuePulse is a deployed **AI sales-operations copilot** for a fictional B2B software company.

1. **Data layer:** synthetic sales data (18 months) in a Postgres star schema, with planted patterns and anomalies recorded as ground truth.
2. **Metrics layer:** sales KPIs defined once in SQL views.
3. **API:** FastAPI serves the KPIs as JSON. No raw tables are ever exposed.
4. **AI chatbot:** plain-English questions → Gemini writes SQL → guardrails check it → it runs read-only → answer as chart + 2-line narrative. Scored against a 25-question eval set.
5. **Automation:** a daily GitHub Actions job appends new data, refreshes views, detects anomalies and sends an AI-written digest to Telegram and email.
6. **Frontend:** a Next.js dashboard with 5 pages, deployed on Vercel.

**Definition of done:** a public URL where a stranger can view KPIs and ask the chatbot a question, plus a README showing measured chatbot accuracy and anomaly-detection recall/precision.

---

## 2. Working agreement (how you work with me) — copy into CLAUDE.md

I'm here to learn the workflow, not just get code. In every session:

1. **Explain before building.** Before each milestone step, give me a short plain-English explanation of the concept (an analogy, then what it means in this project). Keep it under 150 words unless I ask for more.
2. **Plan first.** Use plan mode for anything touching more than one file. List the files you'll create or change and why. Flag any decision I should make, with your recommendation.
3. **Small steps.** One feature per change. After each step, tell me the exact command to run to see it working, and wait for me to confirm before moving on.
4. **Summarise after building.** After each step, write 3 bullets: what changed, why it's built this way, and one thing that would break if it were done differently.
5. **Log my questions.** When I ask "why", answer it and append the question to `QUESTIONS.md` with a one-line answer.
6. **Commits.** Suggest a commit after every working step. Use short, imperative messages (`add dim_rep table with PK`). Never commit `.env`.
7. **Never handle my secrets.** Don't ask me to paste API keys into chat. Tell me which variable name to put in `.env` and read it from the environment in code.
8. **Ask before installing** any package or adding any external service, and say why it's needed.
9. **Don't over-engineer.** No auth, no Docker, no forecasting, no extra pages. Those are v2.
10. **Use connectors carefully.** The Supabase MCP connection is read-only: use it to inspect tables and check results, never to change the schema. All schema changes go through files in `db/` so the project can be rebuilt from scratch.

---

## 3. Fixed decisions

| Decision | Choice |
|---|---|
| Budget | ₹0, free tiers only |
| Database | Supabase Postgres (free project; pauses after 7 days of no activity, which the daily job prevents) |
| Backend | Python 3.12, FastAPI, Pydantic, `psycopg` (v3) with a connection pool, deployed on Render (one free web service) |
| LLM | Gemini Flash-class model via Google AI Studio (free tier) as primary; Groq as fallback. **Model name comes from the `GEMINI_MODEL` env var — never hard-code it**, because Google retires model versions often |
| SQL safety | `sqlglot` for parsing; a dedicated read-only Postgres role for the chatbot |
| Frontend | Next.js (App Router) + TypeScript + Tailwind + shadcn/ui + Recharts, deployed on Vercel |
| Automation | GitHub Actions (cron + manual `workflow_dispatch`) |
| Alerts | Telegram bot + Gmail SMTP (app password) |
| Data | Synthetic only (Faker + numpy, `seed=42`). The Gemini free tier may use prompts for training, so no real data ever |
| Tests | `pytest` for data checks, KPIs and endpoints |

---

## 4. Repository structure

```
revenuepulse/
├── CLAUDE.md                 # project rules (created from this file)
├── PROJECT_PLAN.md           # this file
├── PRD.md                    # 1-page product brief
├── README.md
├── LEARNINGS.md              # written by Harsh, not by Claude
├── QUESTIONS.md              # Harsh's "why" questions + short answers
├── EVALS.md                  # chatbot accuracy + anomaly recall log
├── METRICS.md                # KPI definitions (the chatbot reads this)
├── .env.example              # variable names only, committed
├── .gitignore                # .env, venv/, node_modules/, __pycache__/, .next/
├── db/
│   ├── 01_schema.sql
│   ├── 02_views.sql
│   ├── 03_matviews.sql
│   └── 04_roles.sql          # read-only chatbot role
├── data/
│   ├── generate.py           # seeded synthetic data + planted patterns
│   └── load.py
├── api/
│   ├── main.py
│   ├── db.py                 # connection pool
│   ├── models.py             # Pydantic response models
│   ├── routers/              # kpis.py, reps.py, funnel.py, anomalies.py, chat.py
│   └── chat/                 # prompt.py, guard.py, llm.py
├── jobs/
│   └── daily.py
├── eval/
│   ├── questions.yaml        # 25 questions + expected answers
│   └── run_eval.py
├── tests/
├── web/                      # Next.js app
└── .github/workflows/
    ├── daily.yml
    └── keepalive.yml
```

---

## 5. Data model (star schema)

**Grain:** one row of `fact_deals` = one deal (opportunity). One row of `fact_activities` = one sales activity (call, email or meeting).

| Table | Columns (type) |
|---|---|
| `dim_rep` | `rep_id` (int PK), `rep_name` (text), `region` (text: North/South/East/West), `hire_date` (date), `quarterly_quota` (numeric) |
| `dim_account` | `account_id` (int PK), `account_name` (text), `industry` (text), `company_size` (text: SMB/Mid/Enterprise), `region` (text) |
| `dim_product` | `product_id` (int PK), `product_name` (text), `product_line` (text), `list_price` (numeric) |
| `dim_date` | `date_key` (date PK), `day`, `month`, `month_name`, `quarter`, `year`, `is_weekend` |
| `fact_deals` | `deal_id` (int PK), `rep_id` FK, `account_id` FK, `product_id` FK, `created_date` FK→dim_date, `closed_date` (date, null if open), `stage` (text: Prospecting/Qualified/Proposal/Negotiation/Closed Won/Closed Lost), `amount` (numeric), `is_won` (bool, null if open) |
| `fact_activities` | `activity_id` (int PK), `deal_id` FK, `rep_id` FK, `activity_date` FK→dim_date, `activity_type` (text: Call/Email/Meeting) |
| `ground_truth` | `id`, `kind` (pattern/anomaly), `description`, `region`, `rep_id`, `start_date`, `end_date`, `metric`, `magnitude` |
| `anomalies` | `id`, `detected_on`, `metric_date`, `metric`, `segment`, `value`, `baseline`, `score`, `created_at`, unique on (`metric_date`, `metric`, `segment`) |

**Volume (stay well under Supabase's 500 MB):** 20 reps (5 per region), ~600 accounts, 12 products, ~12,000 deals, ~60,000 activities.
**Date range:** 2025-04-01 to 2026-09-30 for history. The daily job continues from there.
**Indexes:** every FK column on the fact tables, plus `fact_deals(closed_date)` and `fact_deals(stage)`.

### Planted patterns (recorded in `ground_truth`)

| # | Pattern | Detail |
|---|---|---|
| 1 | Seasonality | Q4 closes ~30% more deal value than Q2–Q3; December peaks |
| 2 | West Q3 dip | West region win rate drops ~35% (relative) in Jul–Sep 2025, then recovers |
| 3 | Two weak reps | 2 named reps (different regions) at ~50–60% quota attainment vs ~90–100% team average |
| 4 | Point anomalies | 10 specific days with a sharp spike or drop in daily new pipeline or closed-won value (mix of regions) |

Patterns must be **obvious in a chart but not on the first row**. Log each one in `ground_truth` with exact dates and magnitude, so evals and recall can be scored automatically.

---

## 6. Metrics (write METRICS.md first, then the SQL)

| KPI | Definition |
|---|---|
| Win rate | won ÷ (won + lost). **Open deals excluded.** By period of `closed_date` |
| Stage funnel | share of deals that reached each next stage (deals created in the period) |
| Average sales cycle | mean days from `created_date` to `closed_date`, closed deals only |
| Quota attainment | closed-won amount ÷ `quarterly_quota`, per rep per quarter |
| Average deal size | mean `amount` of won deals |
| Pipeline velocity | (open opps × win rate × avg deal size) ÷ avg sales cycle (days) = value per day |
| Churn-risk score | rule-based per account: no activity in 60 days (+40), last 2 deals lost (+30), declining deal size (+30). Score 0–100 |

Rules: use `NULLIF` on every denominator. Use `closed_date` for outcome metrics and `created_date` for pipeline metrics, and say which in METRICS.md. Heavy views (velocity, funnel, churn risk) are materialized views with a refresh command. Each KPI gets a pytest that recomputes it in pandas and compares to SQL within 0.001.

---

## 7. API

| Method + path | Returns |
|---|---|
| `GET /health` | `{"status": "ok"}` (no database call) |
| `GET /kpis?start=&end=&region=` | headline KPIs for the period |
| `GET /kpis/trend?metric=&grain=month` | time series for one KPI |
| `GET /funnel?start=&end=` | stage counts + conversion |
| `GET /reps` and `GET /reps/{rep_id}` | rep list; one rep vs team |
| `GET /anomalies?limit=20` | newest detected anomalies |
| `POST /chat` | `{question}` → `{answer, sql, rows, chart}` |

Requirements: Pydantic response model on every endpoint; parameterised SQL only (no f-strings with user input); connection pool; CORS allows only the Vercel URL and `http://localhost:3000`; missing rep → 404, bad input → 422, never 500. All config from env vars.

---

## 8. AI chatbot

Flow: question → prompt (allowed views + their columns + METRICS.md + 3 example question/SQL pairs) → Gemini returns JSON `{sql, chart_type, explanation}` → guardrails → execute → second call writes a 2-line narrative.

**Guardrails (all required):**
1. `sqlglot` parses the query; reject anything that isn't exactly one `SELECT`.
2. Allowlist: only the KPI views/materialized views, never raw fact tables or system tables.
3. Force `LIMIT 500` and `SET statement_timeout = '5s'`.
4. Execute as the read-only role from `db/04_roles.sql` (SELECT on allowed views only), via a separate connection string `CHAT_DATABASE_URL`.
5. Rate limit `/chat` per IP (e.g. 10/minute) and cache answers to the suggested questions.

**Evals:** Harsh writes `eval/questions.yaml` (25 questions with expected answers drawn from `ground_truth`). `run_eval.py` compares numbers in code with tolerance and prints accuracy. **Log baseline accuracy in EVALS.md before any prompt tuning.** Change one thing at a time and re-run.

Groq fallback when Gemini errors or returns 429.

---

## 9. Automation

`jobs/daily.py`:
1. Generate and **upsert** the next day's synthetic deals/activities (idempotent: running twice must not duplicate rows).
2. Refresh materialized views.
3. Detect anomalies on daily new pipeline value and closed-won value, per region: robust z-score = (value − rolling 28-day median) ÷ (1.4826 × MAD); flag when |z| > 3.5. Upsert into `anomalies`.
4. Gemini writes a 5-line digest of yesterday's KPIs and anomalies → Telegram + Gmail.
5. On failure, send a Telegram alert with the error.

`daily.yml`: cron `30 2 * * *` (02:30 UTC = 08:00 IST) + `workflow_dispatch`. Secrets from GitHub Actions secrets.
`keepalive.yml`: GET `/health` on Render every 14 minutes.
Also write `eval/anomaly_recall.py`: run the detector over history and report recall and precision against `ground_truth` anomalies → EVALS.md.

---

## 10. Frontend

Pages: **Overview** (KPI cards + trend chart + region filter), **Reps** (pick a rep, see KPIs vs team), **Funnel**, **Anomalies** (newest first, with what and why), **Ask** (chat with 4 suggested questions, chart, "show SQL" toggle).
Every data view has loading, error and empty states, and copes with a ~1-minute Render cold start (show "Waking up the API…"). Only `NEXT_PUBLIC_API_URL` is exposed to the browser. **No AI calls from the browser.** Clean and plain beats fancy.

---

## 11. Environment variables (`.env.example`)

```
DATABASE_URL=            # Supabase pooled connection string (admin, for loading + jobs)
CHAT_DATABASE_URL=       # same database, read-only chatbot role
GEMINI_API_KEY=
GEMINI_MODEL=            # a current Flash model name from Google AI Studio
GROQ_API_KEY=
GROQ_MODEL=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
GMAIL_ADDRESS=
GMAIL_APP_PASSWORD=
DIGEST_TO_EMAIL=
ALLOWED_ORIGINS=         # Vercel URL, http://localhost:3000
NEXT_PUBLIC_API_URL=     # Render URL (frontend only)
```

---

## 12. Milestones (4-day sprint)

Each milestone ends with a **check** I run myself. Don't start the next milestone until the check passes.

| # | Day | Milestone | Check |
|---|---|---|---|
| M1 | 1 | Repo, `.gitignore`, `CLAUDE.md`, `PRD.md`, `.env.example`, FastAPI `/health` deployed on Render, Next.js page deployed on Vercel showing "API: ok" | Vercel URL on my phone shows "API: ok" |
| M2 | 1 | `db/01_schema.sql`, `data/generate.py`, `data/load.py`, `ground_truth` filled | Sanity queries: row counts, zero orphan FKs, no deal closed before created; a pandas chart shows the West Q3 dip |
| M3 | 2 | METRICS.md, views + materialized views, KPI pytest suite | All KPI tests pass |
| M4 | 2 | All API endpoints + tests, CORS, redeploy | Every endpoint works on Render `/docs`; `/reps/99999` → 404, `/reps/abc` → 422 |
| M5 | 3 | Read-only role, chatbot + guardrails, eval runner, baseline logged | Baseline in EVALS.md; "delete all deals", "drop fact_deals" and "show me pg_user" are all rejected |
| M6 | 3 | One round of prompt tuning, Groq fallback, rate limit + cache | Accuracy re-measured and logged |
| M7 | 4 | Daily job, both workflows, anomaly recall script | Manual run twice → no duplicate rows; digest arrives on Telegram; recall + precision in EVALS.md |
| M8 | 4 | 5 frontend pages, final deploy, README with architecture diagram, KPI definitions and eval numbers | A friend uses it cold on their phone without help |

---

## 13. Out of scope for v1

Authentication, user accounts, forecasting/ML models, Docker, custom domain, real customer data, more than 5 pages.
