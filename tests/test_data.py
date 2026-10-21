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
