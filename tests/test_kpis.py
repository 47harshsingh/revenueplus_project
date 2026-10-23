"""SQL KPI views vs independent pandas references (tests/kpi_reference.py).

Needs a throwaway Postgres: set TEST_DATABASE_URL. The database is rebuilt from scratch.
Skipped when TEST_DATABASE_URL is unset (e.g. in the load workflow).
"""

import os

import pandas as pd
import psycopg
import pytest

from data.generate import generate
from data.load import load_all
from tests import kpi_reference as ref

TOL = 0.001
TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


@pytest.fixture(scope="module")
def tables():
    return generate(42)


@pytest.fixture(scope="module")
def conn(tables):
    with psycopg.connect(TEST_DB) as c:
        with c.transaction():
            load_all(c, tables)
        yield c


def sql_df(conn, query: str) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(query)
        cols = [d.name for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def assert_close(sql: pd.DataFrame, expected: pd.DataFrame, keys: list[str], cols: list[str]):
    """Same rows on `keys`, and every column in `cols` equal within TOL (NULL == NaN)."""
    sql = sql.copy()
    for k in keys:
        if k in ("month", "quarter", "day"):
            sql[k] = pd.to_datetime(sql[k])
    merged = sql.merge(expected, on=keys, how="outer", suffixes=("_sql", "_pd"), indicator=True)
    assert (merged._merge == "both").all(), merged[merged._merge != "both"][keys].head()
    for col in cols:
        a = pd.to_numeric(merged[f"{col}_sql"], errors="coerce").astype(float)
        b = merged[f"{col}_pd"].astype(float)
        both_missing = a.isna() & b.isna()
        diff = (a - b).abs()
        bad = ~(both_missing | (diff <= TOL))
        assert not bad.any(), f"{col}: {bad.sum()} rows differ, e.g.\n{merged.loc[bad, keys + [f'{col}_sql', f'{col}_pd']].head()}"


def test_as_of(conn, tables):
    got = sql_df(conn, "SELECT as_of FROM v_as_of").as_of.iloc[0]
    assert pd.Timestamp(got) == ref.as_of(tables) == pd.Timestamp("2026-09-30")


def test_deal_facts_one_row_per_deal(conn, tables):
    n = sql_df(conn, "SELECT count(*) AS n, count(DISTINCT deal_id) AS d FROM v_deal_facts")
    assert n.n.iloc[0] == n.d.iloc[0] == len(tables["fact_deals"])


MONTHLY_COLS = ["won_count", "lost_count", "closed_count", "won_value", "cycle_days_sum",
                "new_deal_count", "new_pipeline_value",
                "win_rate", "avg_deal_size", "avg_sales_cycle_days"]


def test_monthly_region_kpis_match_pandas(conn, tables):
    sql = sql_df(conn, "SELECT * FROM v_monthly_region_kpis")
    expected = ref.monthly_region_kpis(tables)
    assert len(sql) == 18 * 4  # Apr-2025..Sep-2026, every region, empty or not
    assert_close(sql, expected, ["month", "region"], MONTHLY_COLS)


def test_metrics_md_worked_examples(conn):
    """The numbers quoted in METRICS.md must stay true."""
    west_aug = sql_df(conn, """SELECT won_count, lost_count, win_rate FROM v_monthly_region_kpis
                               WHERE region = 'West' AND month = '2025-08-01'""").iloc[0]
    assert (west_aug.won_count, west_aug.lost_count) == (23, 114)
    assert abs(float(west_aug.win_rate) - 0.1679) < TOL

    pooled = sql_df(conn, """SELECT sum(won_value) / NULLIF(sum(won_count), 0) AS deal_size,
                                    sum(cycle_days_sum)::numeric / NULLIF(sum(closed_count), 0) AS cycle_days
                             FROM v_monthly_region_kpis""").iloc[0]
    assert abs(float(pooled.deal_size) - 35171.61) < 0.01
    assert abs(float(pooled.cycle_days) - 49.69) < 0.01


def test_rollup_is_sum_then_divide(conn, tables):
    """All-region quarterly win rate from summed parts == computed straight from the deals."""
    rolled = sql_df(conn, """SELECT quarter, sum(won_count)::numeric
                                    / NULLIF(sum(won_count) + sum(lost_count), 0) AS win_rate
                             FROM v_monthly_region_kpis GROUP BY quarter""")
    d = tables["fact_deals"]
    c = d[d.closed_date >= ref.REPORTING_START]
    direct = (c.groupby(c.closed_date.dt.to_period("Q")).is_won
              .agg(lambda s: s.astype(float).mean()).rename("win_rate").reset_index())
    direct = direct.rename(columns={"closed_date": "quarter"})
    direct["quarter"] = direct.quarter.dt.to_timestamp()
    assert_close(rolled, direct, ["quarter"], ["win_rate"])

    # ...and averaging the regional ratios instead gives a different (wrong) number
    avg_of_avgs = sql_df(conn, """SELECT avg(win_rate) AS w FROM v_monthly_region_kpis
                                  WHERE quarter = '2025-07-01'""").w.iloc[0]
    true_q3 = direct.set_index("quarter").win_rate[pd.Timestamp("2025-07-01")]
    assert abs(float(avg_of_avgs) - true_q3) > 1e-4


def test_quota_attainment_match_pandas(conn, tables):
    sql = sql_df(conn, "SELECT * FROM v_quota_attainment")
    expected = ref.quota_attainment(tables)
    assert len(sql) == 20 * 6  # 20 reps x 6 reporting quarters, zero-filled
    assert_close(sql, expected, ["rep_id", "quarter"],
                 ["closed_won_value", "quarterly_quota", "attainment"])
    assert sql.is_complete_quarter.all()  # as_of 2026-09-30 closes Q3


def test_weak_reps_visible_in_view(conn, tables):
    """Ground-truth weak reps show up as the two lowest average attainments."""
    avg = sql_df(conn, """SELECT rep_id, avg(attainment) AS a FROM v_quota_attainment
                          GROUP BY rep_id ORDER BY a LIMIT 2""")
    gt = tables["ground_truth"]
    weak = gt[gt.metric == "quota_attainment"].set_index("rep_id").magnitude
    assert set(avg.rep_id) == set(weak.index)
    for rep_id, a in zip(avg.rep_id, avg.a):
        assert abs(float(a) - weak[rep_id]) < TOL


def test_daily_region_metrics_match_pandas(conn, tables):
    sql = sql_df(conn, "SELECT * FROM v_daily_region_metrics")
    expected = ref.daily_region_metrics(tables)
    assert len(sql) == len(expected)
    assert_close(sql, expected, ["day", "region"],
                 ["new_deal_count", "new_pipeline_value", "closed_won_count", "closed_won_value"])


def test_empty_segment_gives_null_not_error(conn):
    """NULLIF guard: a segment with no closed deals returns NULL ratios."""
    with conn.cursor() as cur:
        cur.execute("""SELECT won_count::numeric / NULLIF(won_count + lost_count, 0)
                       FROM (SELECT 0 AS won_count, 0 AS lost_count) x""")
        assert cur.fetchone()[0] is None


# ---------- materialized views ----------

FUNNEL_COLS = ["deals_created", "reached_prospecting", "reached_qualified", "reached_proposal",
               "reached_negotiation", "reached_won", "conv_prospecting_to_qualified",
               "conv_qualified_to_proposal", "conv_proposal_to_negotiation",
               "conv_negotiation_to_won"]


def test_funnel_match_pandas(conn, tables):
    sql = sql_df(conn, "SELECT * FROM mv_funnel_monthly")
    assert len(sql) == 18 * 4
    assert_close(sql, ref.funnel_monthly(tables), ["month", "region"], FUNNEL_COLS)


def test_funnel_is_monotonic(conn):
    """You can't reach a later stage without passing the earlier ones."""
    bad = sql_df(conn, """SELECT count(*) AS n FROM mv_funnel_monthly
                          WHERE NOT (deals_created = reached_prospecting
                                 AND reached_prospecting >= reached_qualified
                                 AND reached_qualified   >= reached_proposal
                                 AND reached_proposal    >= reached_negotiation
                                 AND reached_negotiation >= reached_won)""").n.iloc[0]
    assert bad == 0


def test_funnel_worked_example(conn):
    jan = sql_df(conn, """SELECT sum(reached_prospecting) p, sum(reached_qualified) q,
                                 sum(reached_proposal) pr, sum(reached_negotiation) n,
                                 sum(reached_won) w
                          FROM mv_funnel_monthly WHERE month = '2026-01-01'""").iloc[0]
    assert [int(x) for x in jan] == [597, 502, 369, 249, 153]  # METRICS.md example


VELOCITY_COLS = ["open_deals", "open_pipeline_value", "win_rate_180d", "avg_deal_size_180d",
                 "avg_sales_cycle_days_180d", "velocity_per_day"]


def test_pipeline_velocity_match_pandas(conn, tables):
    sql = sql_df(conn, "SELECT * FROM mv_pipeline_velocity")
    assert set(sql.region) == {"North", "South", "East", "West", "All"}
    assert_close(sql, ref.pipeline_velocity(tables), ["region"], VELOCITY_COLS)
    all_row = sql.set_index("region").loc["All"]
    assert int(all_row.open_deals) == 965                                # METRICS.md example
    assert abs(float(all_row.velocity_per_day) - 183399.18) < 0.01


def test_churn_risk_match_pandas(conn, tables):
    sql = sql_df(conn, "SELECT * FROM mv_churn_risk")
    expected = ref.churn_risk(tables)
    assert len(sql) == 600
    merged = sql.merge(expected, on="account_id", suffixes=("_sql", "_pd"))
    for col in ["inactive_60d", "last_two_lost", "declining_deal_size", "churn_risk_score"]:
        mismatch = merged[merged[f"{col}_sql"] != merged[f"{col}_pd"]]
        assert mismatch.empty, f"{col}: {len(mismatch)} accounts differ"


def test_churn_distribution_matches_metrics_md(conn):
    dist = sql_df(conn, """SELECT churn_risk_score AS s, count(*) AS n FROM mv_churn_risk
                           GROUP BY 1 ORDER BY 1""")
    assert dict(zip(dist.s, dist.n)) == {0: 219, 30: 317, 40: 8, 60: 40, 70: 15, 100: 1}


def test_refresh_kpis_picks_up_changes(conn):
    """A materialized view is stale until refreshed; refresh_kpis() must update all of them."""
    q = "SELECT open_pipeline_value FROM mv_pipeline_velocity WHERE region = 'All'"
    before = sql_df(conn, q).iloc[0, 0]
    with conn.transaction(force_rollback=True):  # undo everything afterwards
        with conn.cursor() as cur:
            cur.execute("""UPDATE fact_deals SET amount = amount + 1000000
                           WHERE deal_id = (SELECT min(deal_id) FROM fact_deals
                                            WHERE closed_date IS NULL)""")
        assert sql_df(conn, q).iloc[0, 0] == before            # stale: not refreshed yet
        with conn.cursor() as cur:
            cur.execute("SELECT refresh_kpis()")
        assert sql_df(conn, q).iloc[0, 0] == before + 1000000  # refreshed
