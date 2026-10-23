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


STAGE_RANK = {"Prospecting": 1, "Qualified": 2, "Proposal": 3, "Negotiation": 4, "Closed Won": 5}


def funnel_monthly(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    d = deals_with_region(t)
    d = d[d.created_date >= REPORTING_START].copy()
    d["month"] = d.created_date.dt.to_period("M")
    d["rank"] = d.furthest_stage.map(STAGE_RANK)
    stages = ["prospecting", "qualified", "proposal", "negotiation", "won"]
    g = d.groupby(["month", "region"])
    out = pd.DataFrame({"deals_created": g.size()})
    for i, name in enumerate(stages, start=1):
        out[f"reached_{name}"] = g["rank"].apply(lambda s, i=i: (s >= i).sum())
    months = pd.period_range(REPORTING_START, as_of(t), freq="M")
    out = out.reindex(pd.MultiIndex.from_product([months, REGIONS], names=["month", "region"]),
                      fill_value=0)
    for a, b in zip(stages, stages[1:]):
        out[f"conv_{a}_to_{b}"] = out[f"reached_{b}"] / out[f"reached_{a}"].replace(0, float("nan"))
    out = out.reset_index()
    out["month"] = out.month.dt.to_timestamp()
    return out


def pipeline_velocity(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    d = deals_with_region(t)
    now = as_of(t)
    recent = d[(d.closed_date > now - pd.Timedelta(days=180)) & (d.closed_date <= now)]
    open_ = d[d.closed_date.isna() & (d.created_date <= now)]
    rows = []
    for region in REGIONS + ["All"]:
        r = recent if region == "All" else recent[recent.region == region]
        o = open_ if region == "All" else open_[open_.region == region]
        won = r[r.is_won == True]
        win_rate = len(won) / len(r)
        size = won.amount.mean()
        cycle = (r.closed_date - r.created_date).dt.days.mean()
        rows.append(dict(region=region, open_deals=len(o), open_pipeline_value=o.amount.sum(),
                         win_rate_180d=win_rate, avg_deal_size_180d=size,
                         avg_sales_cycle_days_180d=cycle,
                         velocity_per_day=len(o) * win_rate * size / cycle))
    return pd.DataFrame(rows)


def churn_risk(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    d, acts = t["fact_deals"], t["fact_activities"]
    now = as_of(t)
    accounts = t["dim_account"].account_id

    acct_of_deal = d.set_index("deal_id").account_id
    last_act = acts.assign(account_id=acts.deal_id.map(acct_of_deal)) \
        .groupby("account_id").activity_date.max().reindex(accounts)
    inactive = last_act.isna() | ((now - last_act).dt.days > 60)

    closed = d[d.closed_date.notna()].sort_values(["account_id", "closed_date", "deal_id"])
    last2 = closed.groupby("account_id").tail(2).groupby("account_id").is_won \
        .agg(lambda s: len(s) == 2 and not s.astype(bool).any())
    lost2 = last2.reindex(accounts, fill_value=False).astype(bool)

    by_created = d.sort_values(["account_id", "created_date", "deal_id"])
    last3 = by_created.groupby("account_id").tail(3).groupby("account_id").amount \
        .agg(lambda s: len(s) == 3 and s.iloc[0] > s.iloc[1] > s.iloc[2])
    declining = last3.reindex(accounts, fill_value=False).astype(bool)

    return pd.DataFrame({
        "account_id": accounts.values,
        "inactive_60d": inactive.values,
        "last_two_lost": lost2.values,
        "declining_deal_size": declining.values,
        "churn_risk_score": (40 * inactive.astype(int) + 30 * lost2.astype(int)
                             + 30 * declining.astype(int)).values,
    })
