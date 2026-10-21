"""Structural checks on the generated data (pure pandas, no database)."""

import pandas as pd
import pytest

from data.generate import HISTORY_END, OPEN_STAGES, REGIONS, generate


@pytest.fixture(scope="module")
def tables():
    return generate(42)


def within(n, target, tol=0.10):
    return abs(n - target) <= tol * target


def test_row_counts(tables):
    assert len(tables["dim_rep"]) == 20
    assert len(tables["dim_product"]) == 12
    assert within(len(tables["dim_account"]), 600)
    assert within(len(tables["fact_deals"]), 12_000)
    assert within(len(tables["fact_activities"]), 60_000)


def test_five_reps_per_region(tables):
    counts = tables["dim_rep"].region.value_counts()
    assert set(counts.index) == set(REGIONS)
    assert (counts == 5).all()


def test_primary_keys_unique(tables):
    pks = {"dim_rep": "rep_id", "dim_account": "account_id", "dim_product": "product_id",
           "dim_date": "date_key", "fact_deals": "deal_id", "fact_activities": "activity_id"}
    for table, pk in pks.items():
        assert tables[table][pk].is_unique, table


def test_no_orphan_foreign_keys(tables):
    deals, acts = tables["fact_deals"], tables["fact_activities"]
    dates = set(tables["dim_date"].date_key)
    assert deals.rep_id.isin(tables["dim_rep"].rep_id).all()
    assert deals.account_id.isin(tables["dim_account"].account_id).all()
    assert deals.product_id.isin(tables["dim_product"].product_id).all()
    assert deals.created_date.isin(dates).all()
    assert deals.closed_date.dropna().isin(dates).all()
    assert acts.deal_id.isin(deals.deal_id).all()
    assert acts.rep_id.isin(tables["dim_rep"].rep_id).all()
    assert acts.activity_date.isin(dates).all()


def test_deal_rep_matches_account_region(tables):
    deals = tables["fact_deals"]
    rep_region = deals.rep_id.map(tables["dim_rep"].set_index("rep_id").region)
    acct_region = deals.account_id.map(tables["dim_account"].set_index("account_id").region)
    assert (rep_region == acct_region).all()


def test_no_deal_closed_before_created(tables):
    closed = tables["fact_deals"].dropna(subset=["closed_date"])
    assert (closed.closed_date >= closed.created_date).all()
    assert (closed.closed_date <= HISTORY_END).all()


def test_outcome_consistent(tables):
    """Mirrors the outcome_consistent CHECK constraint in db/01_schema.sql."""
    d = tables["fact_deals"]
    won = (d.stage == "Closed Won") & (d.is_won == True) & d.closed_date.notna() \
        & (d.furthest_stage == "Closed Won")
    lost = (d.stage == "Closed Lost") & (d.is_won == False) & d.closed_date.notna() \
        & (d.furthest_stage != "Closed Won")
    open_ = d.stage.isin(OPEN_STAGES) & d.is_won.isna() & d.closed_date.isna() \
        & (d.furthest_stage == d.stage)
    assert (won | lost | open_).all()


def test_activities_within_deal_lifespan(tables):
    deals = tables["fact_deals"].set_index("deal_id")
    acts = tables["fact_activities"]
    start = acts.deal_id.map(deals.created_date)
    end = acts.deal_id.map(deals.closed_date.fillna(HISTORY_END))
    assert ((acts.activity_date >= start) & (acts.activity_date <= end)).all()
    assert (acts.rep_id == acts.deal_id.map(deals.rep_id)).all()


def test_deterministic_for_same_seed(tables):
    again = generate(42)
    for name, df in tables.items():
        pd.testing.assert_frame_equal(df, again[name], obj=name)


def test_different_seed_differs(tables):
    other = generate(7)
    assert not tables["fact_deals"].equals(other["fact_deals"])


# ---------- planted patterns: each must be measurable in the generated data ----------

from data.generate import (  # noqa: E402
    ANOMALIES, BASELINE_WINDOW_DAYS, WEAK_REP_IDS, daily_series, quarterly_win_rate,
    rep_quarter_closed_won,
)


def gt_row(tables, metric, **match):
    gt = tables["ground_truth"]
    rows = gt[(gt.metric == metric) & (gt.kind == "pattern")]
    for col, val in match.items():
        rows = rows[rows[col] == val]
    assert len(rows) == 1
    return rows.iloc[0]


def test_ground_truth_shape(tables):
    gt = tables["ground_truth"]
    assert len(gt) == 14
    assert (gt.kind == "pattern").sum() == 4
    assert (gt.kind == "anomaly").sum() == 10
    assert gt.rep_id.dropna().isin(tables["dim_rep"].rep_id).all()


def test_seasonality_q4_uplift_and_december_peak(tables):
    assert 0.20 <= gt_row(tables, "closed_won_value").magnitude <= 0.40
    d = tables["fact_deals"]
    won = d[(d.is_won == True) & (d.closed_date >= "2025-04-01")]
    by_month = won.groupby(won.closed_date.dt.to_period("M")).amount.sum()
    assert by_month.idxmax() == pd.Period("2025-12")


def test_west_q3_dip_only_in_west(tables):
    assert -0.45 <= gt_row(tables, "win_rate", region="West").magnitude <= -0.25
    wr = quarterly_win_rate(tables["fact_deals"], tables["dim_rep"])
    q3 = pd.Period("2025Q3")
    for region in ["North", "South", "East"]:
        assert abs(wr.loc[q3, region] / wr[region].drop(q3).mean() - 1) < 0.15, region
    # recovers: West Q4-2025 back within 15% of its non-dip average
    west = wr["West"]
    assert abs(west[pd.Period("2025Q4")] / west.drop(q3).mean() - 1) < 0.15


def test_weak_reps_far_below_team(tables):
    reps = tables["dim_rep"]
    cw = rep_quarter_closed_won(tables["fact_deals"], reps)
    att = (cw.closed_won / reps.quarterly_quota.iloc[0]).groupby(cw.rep_id).mean()
    normal = att.drop(list(WEAK_REP_IDS))
    assert 0.90 <= normal.mean() <= 1.00
    weak_regions = reps.set_index("rep_id").loc[list(WEAK_REP_IDS)].region
    assert weak_regions.nunique() == 2
    for rep_id in WEAK_REP_IDS:
        assert 0.45 <= att[rep_id] <= 0.65
        assert att[rep_id] < normal.min()


def test_anomalies_on_weekdays_and_measurable(tables):
    deals, reps = tables["fact_deals"], tables["dim_rep"]
    series = {m: daily_series(deals, reps, m) for m in {a[2] for a in ANOMALIES}}
    for date, region, metric, direction in ANOMALIES:
        day = pd.Timestamp(date)
        assert day.dayofweek < 5, date
        assert day - pd.Timedelta(days=BASELINE_WINDOW_DAYS) >= pd.Timestamp("2025-04-01")
        s = series[metric][region]
        window = s.loc[day - pd.Timedelta(days=BASELINE_WINDOW_DAYS): day - pd.Timedelta(days=1)]
        med = window.median()
        mad = (window - med).abs().median()
        z = (s[day] - med) / (1.4826 * mad)
        if direction == "spike":
            assert z > 3.5, (date, region, z)
        else:
            assert s[day] == 0, (date, region)
