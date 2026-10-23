"""Eval runner tests. Scoring is pure; the end-to-end runs need TEST_DATABASE_URL."""

import os
from pathlib import Path

import psycopg
import pytest
import yaml
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from api import db
from api.chat import prompt
from eval.run_eval import run, score, summarise, to_markdown

QUESTIONS = yaml.safe_load((Path(__file__).parents[1] / "eval" / "questions.yaml").read_text())
TEST_DB = os.environ.get("TEST_DATABASE_URL")


def answered(rows):
    return {"status": "answered", "rows": rows}


# ---------- scoring ----------

def test_value_within_tolerance_anywhere_in_rows():
    exp = {"value": 0.1679, "tolerance": 0.01}
    assert score(exp, answered([{"region": "West", "win_rate": 0.1681}]))
    assert score(exp, answered([{"a": 1}, {"pct": 16.79}]))  # percentage form accepted
    assert not score(exp, answered([{"win_rate": 0.19}]))
    assert not score(exp, answered([{"flag": True}]))       # booleans aren't numbers


def test_contains_needs_every_string_case_insensitive():
    exp = {"contains": ["Jamie Arnold", "Daniel Wagner"]}
    assert score(exp, answered([{"rep": "DANIEL WAGNER"}, {"rep": "jamie arnold"}]))
    assert not score(exp, answered([{"rep": "Daniel Wagner"}]))


def test_refusal_scoring():
    assert score({"refused": True}, {"status": "refused", "rows": []})
    assert not score({"refused": True}, answered([{"x": 1}]))


def test_refused_or_failed_answer_is_wrong_for_normal_questions():
    exp = {"value": 965, "tolerance": 0.01}
    assert not score(exp, {"status": "refused", "rows": [{"n": 965}]})
    assert not score(exp, {"status": "failed", "rows": []})


def test_errors_are_excluded_from_accuracy():
    results = [{"correct": True}, {"correct": False}, {"correct": None}]
    s = summarise(results, "some-model")
    assert (s["scored"], s["errors"], s["correct"], s["accuracy"]) == (2, 1, 1, 0.5)


# ---------- end to end through the real pipeline (fake LLMs) ----------

class OracleLLM:
    """Answers each question with its own source_sql: a perfect model."""
    by_question = {q["question"]: q.get("source_sql") for q in QUESTIONS}

    def generate_json(self, system, user, schema):
        sql = self.by_question[user]
        return prompt.SqlPlan(sql=sql, explanation="oracle",
                              refuse_reason=None if sql else "not allowed")

    def generate_text(self, system, user):
        return "oracle narrative"


class UselessLLM:
    """Always runs the same irrelevant query and never refuses."""

    def generate_json(self, system, user, schema):
        return prompt.SqlPlan(sql="SELECT as_of FROM v_as_of", explanation="useless")

    def generate_text(self, system, user):
        return ""


@pytest.fixture(scope="module")
def chat_pool():
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL not set")
    from data.generate import generate
    from data.load import load_all

    with psycopg.connect(TEST_DB) as admin:
        with admin.transaction():
            load_all(admin, generate(42), chat_password="chatbot-test-only")
    mp = pytest.MonkeyPatch()
    mp.setenv("CHAT_DATABASE_URL", make_conninfo(**{**conninfo_to_dict(TEST_DB),
                                                    "user": "chatbot_ro",
                                                    "password": "chatbot-test-only"}))
    mp.delenv("DATABASE_URL", raising=False)
    saved = (db.pool, db.chat_pool)
    db.open_pool()
    db.chat_pool.wait(timeout=10)
    prompt.schema_text.cache_clear()
    yield
    db.close_pool()
    db.pool, db.chat_pool = saved
    mp.undo()


def test_perfect_model_scores_25_of_25(chat_pool):
    results = run(QUESTIONS, OracleLLM(), delay=0)
    wrong = [(r["id"], r["status"], r.get("reason")) for r in results if not r["correct"]]
    assert wrong == []
    s = summarise(results, "oracle")
    assert (s["correct"], s["scored"], s["accuracy"]) == (25, 25, 1.0)
    assert "25/25 correct (100.0%)" in to_markdown(s, results)


def test_useless_model_scores_near_zero(chat_pool):
    s = summarise(run(QUESTIONS, UselessLLM(), delay=0), "useless")
    assert s["correct"] == 0  # the scorer isn't trivially generous


# ---------- provider errors ----------

import pytest as _pytest  # noqa: E402

from api.chat.llm import LLMUnavailable, normalise_model  # noqa: E402
from eval.run_eval import EvalAborted, ask  # noqa: E402


class FailingLLM:
    def __init__(self, *statuses):
        self.statuses, self.calls = list(statuses), 0

    def generate_json(self, system, user, schema):
        self.calls += 1
        raise LLMUnavailable("boom", status=self.statuses[min(self.calls - 1, len(self.statuses) - 1)])

    def generate_text(self, system, user):
        return ""


@_pytest.fixture
def no_prompt_db(monkeypatch):
    """ask() builds the prompt first; stub it so these tests need no database."""
    monkeypatch.setattr(prompt, "sql_system_prompt", lambda: "system")


@_pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_config_errors_abort_immediately_without_retry(no_prompt_db, status):
    llm = FailingLLM(status)
    with _pytest.raises(EvalAborted, match="GEMINI_MODEL"):
        ask("q", llm, rate_limit_wait=0, other_wait=0)
    assert llm.calls == 1


@_pytest.mark.parametrize("status", [429, 500, 503, None])
def test_transient_errors_retry_once_then_record_error(no_prompt_db, status):
    llm = FailingLLM(status)
    r = ask("q", llm, rate_limit_wait=0, other_wait=0)
    assert r["status"] == "error" and llm.calls == 2


@_pytest.mark.parametrize("raw, expected", [
    ("gemini-x-flash", "gemini-x-flash"),
    ("  gemini-x-flash \n", "gemini-x-flash"),
    ("models/gemini-x-flash", "gemini-x-flash"),
    ("'gemini-x-flash'", "gemini-x-flash"),
    ('"models/gemini-2.0-flash-001"', "gemini-2.0-flash-001"),
])
def test_model_name_is_normalised(raw, expected):
    assert normalise_model(raw) == expected


@_pytest.mark.parametrize("raw", [" model='models/gemini-3.8-flash'", "model=gemini", "gemini flash", ""])
def test_pasted_code_is_rejected_with_a_clear_message(raw):
    with _pytest.raises(LLMUnavailable, match="bare model id"):
        normalise_model(raw)


class MessageLLM(FailingLLM):
    def __init__(self, status, message):
        super().__init__(status)
        self.message = message

    def generate_json(self, system, user, schema):
        self.calls += 1
        raise LLMUnavailable(f"Gemini error {self.statuses[0]}: {self.message}",
                             status=self.statuses[0])


def test_daily_quota_aborts_instead_of_retrying(no_prompt_db):
    llm = MessageLLM(429, "Quota exceeded for metric: generate_content_free_tier_requests, "
                          "limit: 50, per day")
    with _pytest.raises(EvalAborted, match="DAILY quota"):
        ask("q", llm, rate_limit_wait=0, other_wait=0)
    assert llm.calls == 1


def test_per_minute_quota_is_retried(no_prompt_db):
    llm = MessageLLM(429, "Quota exceeded for metric: requests per minute")
    assert ask("q", llm, rate_limit_wait=0, other_wait=0)["status"] == "error"
    assert llm.calls == 2


def test_results_are_saved_after_every_question(no_prompt_db, monkeypatch):
    """A run killed by the job timeout must still leave what it already answered."""
    import eval.run_eval as runner
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    snapshots = []
    questions = [q for q in QUESTIONS if q["expected"].get("refused")][:3]
    llm = FailingLLM(503)
    run(questions, llm, delay=0, on_result=lambda rs: snapshots.append(len(rs)))
    assert snapshots == [1, 2, 3]
