"""Prompts for the chatbot.

The SQL prompt lists the allowlisted views with their LIVE columns (read from the database
once), the KPI definitions from METRICS.md, the data's as_of date and worked examples.
PROMPT_VERSION is logged with every eval run, so accuracy changes can be tied to edits.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from api.chat.guard import ALLOWED_TABLES
from api.db import chat_fetch_all

PROMPT_VERSION = "v1-baseline"
METRICS_MD = Path(__file__).resolve().parents[2] / "METRICS.md"
NARRATIVE_ROWS = 50


class SqlPlan(BaseModel):
    """What the model must return for every question."""
    sql: str | None = Field(None, description="One PostgreSQL SELECT, or null when refusing")
    chart_type: Literal["number", "bar", "line", "table"] = "table"
    explanation: str = Field(description="One sentence: how the query answers the question")
    refuse_reason: str | None = Field(
        None, description="Set (and sql null) if the question asks to change data, reach other "
                          "tables or system information, or is not about this sales data")


EXAMPLES = [
    ("What was the West region's win rate in August 2025?",
     "SELECT win_rate FROM v_monthly_region_kpis WHERE region = 'West' AND month = '2025-08-01'",
     "number"),
    ("Quarterly win rate across all regions",
     "SELECT quarter, round(sum(won_count)::numeric / NULLIF(sum(won_count) + sum(lost_count), 0), 4)"
     " AS win_rate FROM v_monthly_region_kpis GROUP BY quarter ORDER BY quarter",
     "line"),
    ("Which reps have the lowest average quota attainment?",
     "SELECT rep_name, region, round(avg(attainment), 4) AS avg_attainment FROM v_quota_attainment"
     " WHERE is_complete_quarter GROUP BY rep_name, region ORDER BY avg_attainment LIMIT 5",
     "bar"),
]


@lru_cache(maxsize=1)
def schema_text() -> str:
    """Allowlisted views and their columns, straight from pg_catalog (matviews included)."""
    rows = chat_fetch_all("""
        SELECT c.relname AS view, a.attname AS col, format_type(a.atttypid, a.atttypmod) AS type
        FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = ANY(%(views)s)
          AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY c.relname, a.attnum
    """, {"views": sorted(ALLOWED_TABLES)})
    views: dict[str, list[str]] = {}
    for r in rows:
        views.setdefault(r["view"], []).append(f"{r['col']} {r['type']}")
    return "\n".join(f"- {v}({', '.join(cols)})" for v, cols in views.items())


def as_of() -> str:
    return str(chat_fetch_all("SELECT as_of FROM v_as_of")[0]["as_of"])


def sql_system_prompt() -> str:
    examples = "\n\n".join(f"Q: {q}\nSQL: {s}\nchart_type: {c}" for q, s, c in EXAMPLES)
    return f"""You are RevenuePulse's SQL analyst. The data is a synthetic B2B software
company's sales pipeline. Turn the user's question into ONE PostgreSQL SELECT query.

Rules:
- Use ONLY these views (no other tables exist for you):
{schema_text()}
- Never write, change or delete data. If the question asks to, or asks about other tables,
  users, passwords, system catalogs, or anything that is not this sales data: set
  refuse_reason, leave sql null.
- "Now", "current", "this quarter", "latest" mean the data's as_of date, {as_of()}.
  Never use now(), current_date or current_timestamp.
- Follow the KPI definitions below exactly. For ratios over several rows, divide summed parts
  (sum(won_count) / NULLIF(sum(won_count) + sum(lost_count), 0)); never average a ratio column.
- Prefer the pre-aggregated views (v_monthly_region_kpis, v_quota_attainment, mv_*) over
  v_deal_facts. Months and quarters are first-of-period dates, e.g. '2025-07-01'.
- Regions are 'North', 'South', 'East', 'West'. Round ratios to 4 decimals.
- Give every output column a clear name.
- chart_type: number = one value, bar = comparing categories, line = over time, table = other.

KPI definitions (METRICS.md):
{METRICS_MD.read_text()}

Examples:
{examples}"""


NARRATIVE_SYSTEM = """You explain query results to a sales manager in exactly 2 short
sentences. Use only numbers that appear in the rows; never invent or estimate. Format
ratios as percentages with 1 decimal and money with thousands separators. If there are
no rows, say no data matched."""


def narrative_user_prompt(question: str, rows: list[dict]) -> str:
    sample = rows[:NARRATIVE_ROWS]
    more = f"\n({len(rows) - len(sample)} more rows not shown)" if len(rows) > len(sample) else ""
    return f"Question: {question}\nRows (JSON): {json.dumps(sample, default=str)}{more}"
