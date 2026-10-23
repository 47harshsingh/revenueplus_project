"""Pandas reference implementations of the KPIs in METRICS.md.

Written from the definitions, independently of the SQL, so the KPI tests are a real
second opinion: if SQL and pandas disagree, one of them misread METRICS.md.
"""

import pandas as pd

REPORTING_START = pd.Timestamp("2025-04-01")
REGIONS = ["North", "South", "East", "West"]


def deals_with_region(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return t["fact_deals"].merge(t["dim_rep"][["rep_id", "region"]], on="rep_id")


def as_of(t: dict[str, pd.DataFrame]) -> pd.Timestamp:
    d, a = t["fact_deals"], t["fact_activities"]
    return max(d.created_date.max(), d.closed_date.max(), a.activity_date.max())


def monthly_region_kpis(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    d = deals_with_region(t)
    months = pd.period_range(REPORTING_START, as_of(t), freq="M")
    grid = pd.MultiIndex.from_product([months, REGIONS], names=["month", "region"])

    closed = d[d.closed_date >= REPORTING_START].copy()
    closed["month"] = closed.closed_date.dt.to_period("M")
    closed["won"] = closed.is_won.astype(bool)
    closed["cycle"] = (closed.closed_date - closed.created_date).dt.days
    c = closed.groupby(["month", "region"]).agg(
        won_count=("won", "sum"),
        closed_count=("won", "size"),
        won_value=("amount", lambda s: s[closed.loc[s.index, "won"]].sum()),
        cycle_days_sum=("cycle", "sum"),
    )
    c["lost_count"] = c.closed_count - c.won_count

    created = d[d.created_date >= REPORTING_START].copy()
    created["month"] = created.created_date.dt.to_period("M")
    n = created.groupby(["month", "region"]).agg(
        new_deal_count=("amount", "size"), new_pipeline_value=("amount", "sum"))

    out = c.join(n, how="outer").reindex(grid).fillna(0)
    out["win_rate"] = out.won_count / (out.won_count + out.lost_count).replace(0, float("nan"))
    out["avg_deal_size"] = out.won_value / out.won_count.replace(0, float("nan"))
    out["avg_sales_cycle_days"] = out.cycle_days_sum / out.closed_count.replace(0, float("nan"))
    out = out.reset_index()
    out["month"] = out.month.dt.to_timestamp()
    return out


def quota_attainment(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    d, reps = t["fact_deals"], t["dim_rep"]
    quarters = pd.period_range(REPORTING_START, as_of(t), freq="Q")
    won = d[(d.is_won == True) & (d.closed_date >= REPORTING_START)]
    s = won.groupby([won.rep_id, won.closed_date.dt.to_period("Q")]).amount.sum()
    grid = pd.MultiIndex.from_product([reps.rep_id, quarters], names=["rep_id", "quarter"])
    out = s.reindex(grid, fill_value=0.0).rename("closed_won_value").reset_index()
    out = out.merge(reps[["rep_id", "quarterly_quota"]], on="rep_id")
    out["attainment"] = out.closed_won_value / out.quarterly_quota
    out["quarter"] = out.quarter.dt.to_timestamp()
    return out


def daily_region_metrics(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    d = deals_with_region(t)
    days = pd.date_range(d.created_date.min(), as_of(t), freq="D")
    grid = pd.MultiIndex.from_product([days, REGIONS], names=["day", "region"])
    created = d.groupby(["created_date", "region"]).amount.agg(["size", "sum"])
    created.index.names = ["day", "region"]
    won = d[d.is_won == True].groupby(["closed_date", "region"]).amount.agg(["size", "sum"])
    won.index.names = ["day", "region"]
    out = pd.DataFrame(index=grid)
    out["new_deal_count"] = created["size"]
    out["new_pipeline_value"] = created["sum"]
    out["closed_won_count"] = won["size"]
    out["closed_won_value"] = won["sum"]
    return out.fillna(0).reset_index()
