"""chatbot_ro must read the KPI views and nothing else. Every test logs in AS chatbot_ro.

Needs TEST_DATABASE_URL (an admin login that can create roles); skipped without it.
"""

import os

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from data.generate import generate
from data.load import load_all, scram_sha256

TEST_DB = os.environ.get("TEST_DATABASE_URL")
CHAT_PASSWORD = "chatbot-test-only"  # throwaway; real one comes from CHAT_DB_PASSWORD
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")

ALLOWED = ["v_as_of", "v_deal_facts", "v_monthly_region_kpis", "v_quota_attainment",
           "v_daily_region_metrics", "mv_funnel_monthly", "mv_pipeline_velocity", "mv_churn_risk"]
DENIED = ["fact_deals", "fact_activities", "dim_rep", "dim_account", "dim_product", "dim_date",
          "ground_truth", "anomalies"]


@pytest.fixture(scope="module")
def chat():
    with psycopg.connect(TEST_DB) as admin:
        with admin.transaction():
            load_all(admin, generate(42), chat_password=CHAT_PASSWORD)
    url = make_conninfo(**{**conninfo_to_dict(TEST_DB), "user": "chatbot_ro",
                           "password": CHAT_PASSWORD})
    with psycopg.connect(url, autocommit=True) as c:
        yield c


def test_login_works_with_client_side_scram_hash(chat):
    assert chat.execute("SELECT current_user").fetchone()[0] == "chatbot_ro"


def test_scram_hash_never_contains_plaintext():
    assert CHAT_PASSWORD not in scram_sha256(CHAT_PASSWORD)
    assert scram_sha256("x") != scram_sha256("x")  # random salt each time


@pytest.mark.parametrize("view", ALLOWED)
def test_can_read_allowlisted_views(chat, view):
    chat.execute(f"SELECT * FROM {view} LIMIT 1").fetchall()


def test_views_return_real_data_despite_rls(chat):
    assert chat.execute("SELECT count(*) FROM v_deal_facts").fetchone()[0] == 12166


@pytest.mark.parametrize("table", DENIED)
def test_cannot_read_raw_tables_or_answer_key(chat, table):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        chat.execute(f"SELECT * FROM {table} LIMIT 1")


def test_cannot_refresh_or_call_functions(chat):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        chat.execute("SELECT refresh_kpis()")


@pytest.mark.parametrize("statement", [
    "DELETE FROM fact_deals",
    "DROP TABLE fact_deals",
    "CREATE TABLE junk (a int)",
    "INSERT INTO anomalies (detected_on, metric_date, metric, segment, value, baseline, score) "
    "VALUES (now(), now(), 'x', 'y', 1, 1, 1)",
])
def test_cannot_write(chat, statement):
    # read-only transaction OR missing privilege: either way Postgres refuses
    with pytest.raises((psycopg.errors.ReadOnlySqlTransaction,
                        psycopg.errors.InsufficientPrivilege)):
        chat.execute(statement)


def test_session_is_read_only_and_time_limited(chat):
    assert chat.execute("SHOW default_transaction_read_only").fetchone()[0] == "on"
    assert chat.execute("SHOW statement_timeout").fetchone()[0] == "5s"


def test_long_query_is_killed_at_5_seconds(chat):
    with pytest.raises(psycopg.errors.QueryCanceled):
        chat.execute("SELECT pg_sleep(10)")


def test_role_has_no_special_powers(chat):
    row = chat.execute("""SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls
                          FROM pg_roles WHERE rolname = 'chatbot_ro'""").fetchone()
    assert row == (False, False, False, False)
