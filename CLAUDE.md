# RevenuePulse — Project Rules

RevenuePulse is a deployed AI sales-operations copilot for a fictional B2B software company: a synthetic Postgres star schema, KPI views, a FastAPI API, a Gemini text-to-SQL chatbot with guardrails, a daily GitHub Actions job, and a Next.js dashboard. The full spec is in `PROJECT_PLAN.md`.

Harsh is an aspiring data analyst, strong in SQL and Python/pandas, and **new** to Git, the terminal, APIs, FastAPI, Next.js/React, deployment and GitHub Actions. The goal is learning the full workflow, not just getting code.

## Working agreement

1. **Explain before building.** Before each milestone step, give a short plain-English explanation of the concept (an analogy, then what it means in this project). Keep it under 150 words unless asked for more.
2. **Plan first.** Use plan mode for anything touching more than one file. List the files to create or change and why. Flag any decision Harsh should make, with a recommendation.
3. **Small steps.** One feature per change. After each step, give the exact command to run to see it working, and wait for Harsh to confirm before moving on.
4. **Summarise after building.** After each step, write 3 bullets: what changed, why it's built this way, and one thing that would break if it were done differently.
5. **Log questions.** When Harsh asks "why", answer it and append the question to `QUESTIONS.md` with a one-line answer.
6. **Commits.** Suggest a commit after every working step. Use short, imperative messages (`add dim_rep table with PK`). Never commit `.env`.
7. **Never handle secrets.** Don't ask Harsh to paste API keys into chat. Say which variable name goes in `.env`, and read it from the environment in code.
8. **Ask before installing** any package or adding any external service, and say why it's needed.
9. **Don't over-engineer.** No auth, no Docker, no forecasting, no extra pages. Those are v2.
10. **Use connectors carefully.** The Supabase MCP connection is read-only: use it to inspect tables and check results, never to change the schema. All schema changes go through files in `db/` so the project can be rebuilt from scratch.

## Fixed decisions

| Decision | Choice |
|---|---|
| Budget | ₹0, free tiers only |
| Database | Supabase Postgres (free project; pauses after 7 days of no activity, which the daily job prevents) |
| Backend | Python 3.12, FastAPI, Pydantic, `psycopg` (v3) with a connection pool, deployed on Render (one free web service) |
| LLM | Gemini Flash-class model via Google AI Studio (free tier) as primary; Groq as fallback. **Model name comes from the `GEMINI_MODEL` env var — never hard-code it** |
| SQL safety | `sqlglot` for parsing; a dedicated read-only Postgres role for the chatbot |
| Frontend | Next.js (App Router) + TypeScript + Tailwind + shadcn/ui + Recharts, deployed on Vercel |
| Automation | GitHub Actions (cron + manual `workflow_dispatch`) |
| Alerts | Telegram bot + Gmail SMTP (app password) |
| Data | Synthetic only (Faker + numpy, `seed=42`). The Gemini free tier may use prompts for training, so no real data ever |
| Tests | `pytest` for data checks, KPIs and endpoints |

### Workflow decisions (made in M1)

- **Where checks run:** development happens in a Claude Code cloud session. Claude runs the local checks (pytest, uvicorn, curl, npm) and shows the output. Harsh verifies the deployed Render and Vercel URLs himself.
- **Branches:** work goes on a `claude/...` feature branch, then a PR, and Harsh merges it to `main`. Render and Vercel deploy from `main` only.
- **Python dependencies:** `requirements.txt` + venv. Render runs Python 3.12 (`PYTHON_VERSION` env var).
- **shadcn/ui:** deferred until the first real component needs it (M8).

### Data decisions (made in M2)

- **Load path:** the cloud container can't reach Supabase. `data/load.py` runs in a manual GitHub Actions workflow (`load.yml`), with `DATABASE_URL` stored as a GitHub secret. Everything is tested against local Postgres 16 in the container first.
- **Supabase:** the project lives in US West, next to Render (Oregon). Use the **Session pooler** connection string: Direct is IPv6-only, and the Transaction pooler breaks psycopg prepared statements.
- **Schema deviations from PROJECT_PLAN.md:**
  - `fact_deals.furthest_stage` exists, because the funnel needs it.
  - Deals are generated from 2025-01-01 as warm-up, while KPIs report from 2025-04-01.
  - `dim_date` covers 2025-01-01 to 2027-12-31.
- **RLS:** RLS is enabled on every table, which blocks Supabase's public REST API. Views (M3) run as their owner, so they are unaffected.

## Environment variables (`.env.example`)

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
