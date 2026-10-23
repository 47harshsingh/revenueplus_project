"""The chat pipeline: question -> model SQL -> guard -> run as chatbot_ro -> narrative.

Used by POST /chat and by eval/run_eval.py, so the eval measures exactly what users get.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

import psycopg
from pydantic import BaseModel

from api.chat import prompt
from api.chat.guard import GuardRejected, check_sql
from api.chat.llm import LLM
from api.db import chat_fetch_all


class ChatResult(BaseModel):
    question: str
    status: Literal["answered", "refused", "failed"]
    answer: str
    sql: str | None = None             # the guarded SQL that actually ran
    columns: list[str] = []
    rows: list[dict] = []
    chart_type: str = "table"
    explanation: str | None = None
    reason: str | None = None          # why it was refused / failed
    prompt_version: str = prompt.PROMPT_VERSION


def _jsonable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def answer_question(question: str, llm: LLM) -> ChatResult:
    plan = llm.generate_json(prompt.sql_system_prompt(), question, prompt.SqlPlan)

    if plan.refuse_reason or not plan.sql:
        reason = plan.refuse_reason or "the model did not produce a query"
        return ChatResult(question=question, status="refused", reason=reason,
                          answer=f"I can't help with that: {reason}")

    try:
        safe_sql = check_sql(plan.sql)
    except GuardRejected as e:
        return ChatResult(question=question, status="refused", reason=str(e),
                          answer=f"I can only run read-only queries on the sales KPIs ({e}).")

    try:
        raw = chat_fetch_all(safe_sql)
    except psycopg.errors.QueryCanceled:
        return ChatResult(question=question, status="failed", sql=safe_sql,
                          reason="query took longer than 5 seconds",
                          answer="That question needs a query that's too slow; try narrowing it.")
    except psycopg.errors.InsufficientPrivilege as e:  # the role caught what the guard missed
        return ChatResult(question=question, status="refused", sql=safe_sql,
                          reason=e.diag.message_primary, answer="I can't access that data.")
    except psycopg.Error as e:  # e.g. the model used a column that doesn't exist
        return ChatResult(question=question, status="failed", sql=safe_sql,
                          reason=e.diag.message_primary or type(e).__name__,
                          answer="I wrote a query that didn't run; try rephrasing.")

    rows = [{k: _jsonable(v) for k, v in r.items()} for r in raw]
    columns = list(raw[0].keys()) if raw else []
    answer = llm.generate_text(prompt.NARRATIVE_SYSTEM, prompt.narrative_user_prompt(question, rows))
    return ChatResult(question=question, status="answered", answer=answer, sql=safe_sql,
                      columns=columns, rows=rows, chart_type=plan.chart_type,
                      explanation=plan.explanation)
