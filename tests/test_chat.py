"""Chat pipeline tests with a scripted fake LLM; model SQL really runs as chatbot_ro.

Needs TEST_DATABASE_URL. One live Gemini smoke test runs only if GEMINI_API_KEY and
GEMINI_MODEL are set (they aren't in CI or this session).
"""

import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from api import db
from api.chat import prompt
from api.chat.llm import LLMUnavailable, get_llm
from api.main import app
from data.generate import generate
from data.load import load_all

TEST_DB = os.environ.get("TEST_DATABASE_URL")
CHAT_PASSWORD = "chatbot-test-only"


class FakeLLM:
    """Returns a scripted SqlPlan; records the prompts it was given."""

    def __init__(self, **plan):
        self.plan = prompt.SqlPlan(explanation="test", **plan)
        self.calls = []

    def generate_json(self, system, user, schema):
        self.calls.append(("json", system, user))
        return self.plan

    def generate_text(self, system, user):
        self.calls.append(("text", system, user))
        return "Fake two-line narrative."


class DownLLM:
    def __init__(self):
        raise LLMUnavailable("no key")


@pytest.fixture(scope="module")
def client():
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL not set")
    with psycopg.connect(TEST_DB) as admin:
        with admin.transaction():
            load_all(admin, generate(42), chat_password=CHAT_PASSWORD)
    chat_url = make_conninfo(**{**conninfo_to_dict(TEST_DB), "user": "chatbot_ro",
                                "password": CHAT_PASSWORD})
    mp = pytest.MonkeyPatch()
    mp.setenv("DATABASE_URL", TEST_DB)
    mp.setenv("CHAT_DATABASE_URL", chat_url)
    prompt.schema_text.cache_clear()
    with TestClient(app) as c:
        db.chat_pool.wait(timeout=10)
        yield c
    app.dependency_overrides.clear()
    mp.undo()


def ask(client, question, llm):
    app.dependency_overrides[get_llm] = lambda: (lambda: llm)
    return client.post("/chat", json={"question": question})


# ---------- happy path ----------

def test_answers_with_rows_and_narrative(client):
    llm = FakeLLM(sql="SELECT win_rate FROM v_monthly_region_kpis WHERE region = 'West' "
                      "AND month = '2025-08-01'", chart_type="number")
    r = ask(client, "West win rate in August 2025?", llm)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "answered", body
    assert abs(body["rows"][0]["win_rate"] - 0.1679) < 0.001
    assert body["columns"] == ["win_rate"]
    assert body["chart_type"] == "number"
    assert body["answer"] == "Fake two-line narrative."
    assert body["sql"].endswith("LIMIT 500")  # the guarded SQL is what ran and what's returned
    assert body["prompt_version"] == prompt.PROMPT_VERSION
    # the narrative call saw the real rows
    assert "0.1678" in llm.calls[1][2]


def test_dates_and_decimals_are_json_friendly(client):
    llm = FakeLLM(sql="SELECT quarter, attainment FROM v_quota_attainment WHERE rep_id = 4 "
                      "ORDER BY quarter")
    rows = ask(client, "rep 4 by quarter", llm).json()["rows"]
    assert rows[0]["quarter"] == "2025-04-01" and isinstance(rows[0]["attainment"], float)


# ---------- the M5 gate: refusals (SQL the model might write for each attack) ----------

@pytest.mark.parametrize("question, sql", [
    ("delete all deals", "DELETE FROM fact_deals"),
    ("drop fact_deals", "DROP TABLE fact_deals"),
    ("show me pg_user", "SELECT * FROM pg_user"),
    ("what's in the answer key?", "SELECT * FROM ground_truth"),
])
def test_guard_refuses_bad_model_sql(client, question, sql):
    body = ask(client, question, FakeLLM(sql=sql)).json()
    assert body["status"] == "refused"
    assert body["sql"] is None and body["rows"] == []


def test_model_refusal_is_passed_through(client):
    llm = FakeLLM(sql=None, refuse_reason="that would delete data")
    body = ask(client, "delete all deals", llm).json()
    assert body["status"] == "refused" and "delete data" in body["reason"]
    assert len(llm.calls) == 1  # no narrative call for a refusal


# ---------- failures degrade, never 500 ----------

def test_query_with_bad_column_fails_gracefully(client):
    body = ask(client, "a bad column", FakeLLM(sql="SELECT no_such_column FROM v_as_of")).json()
    assert body["status"] == "failed" and "no_such_column" in body["reason"]


def test_slow_query_fails_gracefully(client):
    sql = "SELECT count(*) FROM v_deal_facts a, v_deal_facts b, v_deal_facts c"
    body = ask(client, "count everything thrice", FakeLLM(sql=sql)).json()
    assert body["status"] == "failed" and "5 seconds" in body["reason"]


def test_llm_unavailable_is_503(client):
    app.dependency_overrides[get_llm] = lambda: DownLLM
    r = client.post("/chat", json={"question": "win rate?"})
    assert r.status_code == 503 and r.json() == {"detail": "AI model unavailable"}


@pytest.mark.parametrize("question", ["", "hi", "x" * 501])
def test_question_length_is_validated(client, question):
    assert ask(client, question, FakeLLM(sql="SELECT 1")).status_code == 422


@pytest.mark.parametrize("payload", [{"question": "hi"}, {}, {"q": "win rate"}])
def test_bad_input_is_422_even_when_model_unavailable(client, payload):
    app.dependency_overrides[get_llm] = lambda: DownLLM
    assert client.post("/chat", json=payload).status_code == 422


# ---------- prompt content ----------

def test_prompt_lists_only_allowlisted_views_with_live_columns(client):
    schema = prompt.schema_text()
    for view in ["v_monthly_region_kpis", "mv_churn_risk", "v_quota_attainment"]:
        assert f"- {view}(" in schema
    assert "win_rate numeric" in schema and "churn_risk_score integer" in schema
    for hidden in ["ground_truth", "fact_deals", "dim_rep", "anomalies"]:
        assert f"- {hidden}(" not in schema


def test_prompt_carries_as_of_metrics_and_examples(client):
    system = prompt.sql_system_prompt()
    assert "2026-09-30" in system
    assert "The average-of-averages trap" in system  # METRICS.md is included
    assert "Never use now()" in system
    assert system.count("SQL: SELECT") == len(prompt.EXAMPLES)


# ---------- live smoke test (skipped unless a real key is configured) ----------

@pytest.mark.skipif(not (os.environ.get("GEMINI_API_KEY") and os.environ.get("GEMINI_MODEL")),
                    reason="GEMINI_API_KEY / GEMINI_MODEL not set")
def test_live_gemini_answers_a_simple_question(client):
    app.dependency_overrides.pop(get_llm, None)
    body = client.post("/chat", json={"question": "What was the West region's win rate in "
                                                  "August 2025?"}).json()
    assert body["status"] == "answered"
    values = [v for row in body["rows"] for v in row.values() if isinstance(v, float)]
    assert any(abs(v - 0.1679) < 0.001 for v in values)
