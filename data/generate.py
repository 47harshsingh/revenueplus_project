"""Seeded synthetic sales data for RevenuePulse.

generate(seed) returns one DataFrame per table in db/01_schema.sql, including
ground_truth: a record of every pattern deliberately planted in the data, with its
magnitude measured from the generated rows, so evals can be scored automatically.

Same seed -> identical data, so tests, local loads and GitHub Actions all agree.
Run `python -m data.generate` to write CSVs to data/out/ for inspection.
"""

from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

SEED = 42
WARMUP_START = pd.Timestamp("2025-01-01")  # deals start here so Apr-2025 isn't a fake ramp-up
HISTORY_START = pd.Timestamp("2025-04-01")  # KPIs report from here
HISTORY_END = pd.Timestamp("2026-09-30")    # anything not closed by now stays open
DATE_DIM_END = pd.Timestamp("2027-12-31")   # room for the daily job to append

REGIONS = ["North", "South", "East", "West"]
REPS_PER_REGION = 5
N_ACCOUNTS = 600
N_DEALS = 12_000
ACTIVITIES_PER_DEAL = 5  # Poisson mean -> ~60k activities

INDUSTRIES = ["SaaS", "Retail", "Manufacturing", "Healthcare", "Finance", "Logistics"]
COMPANY_SIZES = ["SMB", "Mid", "Enterprise"]
SIZE_WEIGHTS = [0.60, 0.30, 0.10]
SIZE_MULTIPLIER = {"SMB": 1.0, "Mid": 2.5, "Enterprise": 6.0}

OPEN_STAGES = ["Prospecting", "Qualified", "Proposal", "Negotiation"]
OPEN_STAGE_WEIGHTS = [0.35, 0.30, 0.20, 0.15]
LOST_FURTHEST_WEIGHTS = [0.20, 0.30, 0.30, 0.20]  # how far a lost deal got before losing

BASE_WIN_RATE = 0.30
CYCLE_MEDIAN_DAYS = 45
CYCLE_MIN_DAYS, CYCLE_MAX_DAYS = 7, 240

ACTIVITY_TYPES = ["Call", "Email", "Meeting"]
ACTIVITY_WEIGHTS = [0.50, 0.35, 0.15]

PRODUCTS = [  # (name, line, list price)
    ("Core Starter", "Core Platform", 2_000),
    ("Core Team", "Core Platform", 6_000),
    ("Core Business", "Core Platform", 15_000),
    ("Core Enterprise", "Core Platform", 40_000),
    ("Insights Lite", "Analytics", 3_000),
    ("Insights Pro", "Analytics", 9_000),
    ("Insights Max", "Analytics", 25_000),
    ("Forecast Add-on", "Analytics", 12_000),
    ("Connect Basic", "Integrations", 2_500),
    ("Connect Plus", "Integrations", 7_500),
    ("Connect Premium", "Integrations", 20_000),
    ("Data Sync Suite", "Integrations", 60_000),
]

# ---------- planted patterns ----------

# 1. Seasonality: Q4 closes win more often and bigger; December peaks. Keyed by close month.
SEASON_WIN_MULT = {10: 1.05, 11: 1.08, 12: 1.20}
SEASON_AMOUNT_MULT = {10: 1.02, 11: 1.04, 12: 1.10}

# 2. West Q3 dip: West win probability x0.65 for deals closing Jul-Sep 2025.
WEST_DIP_START, WEST_DIP_END = pd.Timestamp("2025-07-01"), pd.Timestamp("2025-09-30")
WEST_DIP_MULT = 0.65

# 3. Two weak reps in different regions (North and East; West already carries the dip).
WEAK_REP_IDS = (4, 12)
WEAK_REP_WIN_MULT = 0.52
TEAM_ATTAINMENT_TARGET = 0.95  # quota is calibrated so the normal reps average this

# 4. Point anomalies on daily per-region series. All weekdays, all after a full 28-day
#    baseline window from HISTORY_START.
NEW_PIPELINE = "new_pipeline_value"   # sum(amount) of deals created that day
CLOSED_WON = "closed_won_value"       # sum(amount) of deals won that day
SPIKE_NEW_DEALS = 35   # ~7x a normal day's ~5 new deals per region
SPIKE_WON_DEALS = 15   # ~10x a normal day's ~1.5 wins per region
ANOMALIES = [  # (date, region, metric, direction)
    ("2025-05-14", "North", NEW_PIPELINE, "spike"),
    ("2025-06-18", "East", CLOSED_WON, "spike"),
    ("2025-08-06", "South", NEW_PIPELINE, "drop"),
    ("2025-10-22", "West", NEW_PIPELINE, "spike"),
    ("2025-11-12", "North", CLOSED_WON, "spike"),
    ("2026-01-21", "East", NEW_PIPELINE, "drop"),
    ("2026-03-11", "South", NEW_PIPELINE, "spike"),
    ("2026-05-06", "West", CLOSED_WON, "spike"),
    ("2026-06-17", "North", NEW_PIPELINE, "drop"),
    ("2026-08-19", "East", NEW_PIPELINE, "spike"),
]
BASELINE_WINDOW_DAYS = 28


def make_dates() -> pd.DataFrame:
    days = pd.date_range(WARMUP_START, DATE_DIM_END, freq="D")
    return pd.DataFrame({
        "date_key": days,
        "day": days.day,
        "month": days.month,
        "month_name": days.month_name(),
        "quarter": days.quarter,
        "year": days.year,
        "is_weekend": days.dayofweek >= 5,
    })


def make_reps(rng: np.random.Generator, fake: Faker) -> pd.DataFrame:
    n = len(REGIONS) * REPS_PER_REGION
    hire_offsets = rng.integers(0, 3 * 365, size=n)
    return pd.DataFrame({
        "rep_id": np.arange(1, n + 1),
        "rep_name": [fake.name() for _ in range(n)],
        "region": np.repeat(REGIONS, REPS_PER_REGION),
        "hire_date": pd.Timestamp("2022-01-01") + pd.to_timedelta(hire_offsets, unit="D"),
        "quarterly_quota": np.nan,  # set by calibrate_quota() once closed-won is known
    })


def make_accounts(rng: np.random.Generator, fake: Faker) -> pd.DataFrame:
    return pd.DataFrame({
        "account_id": np.arange(1, N_ACCOUNTS + 1),
        "account_name": [fake.unique.company() for _ in range(N_ACCOUNTS)],
        "industry": rng.choice(INDUSTRIES, size=N_ACCOUNTS),
        "company_size": rng.choice(COMPANY_SIZES, size=N_ACCOUNTS, p=SIZE_WEIGHTS),
        "region": rng.choice(REGIONS, size=N_ACCOUNTS),
    })


def make_products() -> pd.DataFrame:
    names, lines, prices = zip(*PRODUCTS)
    return pd.DataFrame({
        "product_id": np.arange(1, len(PRODUCTS) + 1),
        "product_name": names,
        "product_line": lines,
        "list_price": np.array(prices, dtype=float),
    })


def win_probability(close: pd.DatetimeIndex, region: np.ndarray, rep_id: np.ndarray) -> np.ndarray:
    p = np.full(len(close), BASE_WIN_RATE)
    p *= pd.Series(close.month).map(SEASON_WIN_MULT).fillna(1.0).to_numpy()
    in_dip = (region == "West") & (close >= WEST_DIP_START) & (close <= WEST_DIP_END)
    p *= np.where(in_dip, WEST_DIP_MULT, 1.0)
    p *= np.where(np.isin(rep_id, WEAK_REP_IDS), WEAK_REP_WIN_MULT, 1.0)
    return p


def draw_deals(rng: np.random.Generator, n: int, reps: pd.DataFrame, accounts: pd.DataFrame,
               products: pd.DataFrame, *, created: pd.DatetimeIndex | None = None,
               region: str | None = None, won_on: pd.Timestamp | None = None) -> pd.DataFrame:
    """Draw n deals with all outcome rules applied.

    created: fixed created dates (default: weekday-weighted over the whole history).
    region:  restrict accounts (and so reps) to one region.
    won_on:  force every deal to close won on this date (created = won_on - cycle).
    """
    pool = accounts if region is None else accounts[accounts.region == region]
    acct = pool.iloc[rng.integers(0, len(pool), size=n)].reset_index(drop=True)
    # Rep comes from the account's region, so regional numbers are coherent.
    reps_by_region = {r: reps.loc[reps.region == r, "rep_id"].to_numpy() for r in REGIONS}
    rep_id = np.array([rng.choice(reps_by_region[r]) for r in acct.region])
    acct_region = acct.region.to_numpy()

    prod_idx = rng.integers(0, len(products), size=n)
    list_price = products.list_price.to_numpy()[prod_idx]
    size_mult = acct.company_size.map(SIZE_MULTIPLIER).to_numpy()
    amount = list_price * size_mult * rng.lognormal(0, 0.35, size=n)

    cycle = np.clip(rng.lognormal(np.log(CYCLE_MEDIAN_DAYS), 0.5, size=n),
                    CYCLE_MIN_DAYS, CYCLE_MAX_DAYS).astype(int)
    cycle_td = pd.to_timedelta(cycle, unit="D")

    if won_on is not None:
        close = pd.DatetimeIndex([won_on] * n)
        created = close - cycle_td
        created = created.where(created >= WARMUP_START, WARMUP_START)
        is_closed = np.ones(n, dtype=bool)
        won = np.ones(n, dtype=bool)
    else:
        if created is None:
            days = pd.date_range(WARMUP_START, HISTORY_END, freq="D")
            weights = np.where(days.dayofweek < 5, 4.0, 1.0)  # weekdays 4x weekends
            created = pd.DatetimeIndex(rng.choice(days, size=n, p=weights / weights.sum()))
        close = created + cycle_td
        is_closed = np.asarray(close <= HISTORY_END)
        won = rng.random(n) < win_probability(close, acct_region, rep_id)

    amount *= pd.Series(close.month).map(SEASON_AMOUNT_MULT).fillna(1.0).to_numpy()
    open_stage = rng.choice(OPEN_STAGES, size=n, p=OPEN_STAGE_WEIGHTS)
    lost_furthest = rng.choice(OPEN_STAGES, size=n, p=LOST_FURTHEST_WEIGHTS)

    return pd.DataFrame({
        "rep_id": rep_id,
        "account_id": acct.account_id.to_numpy(),
        "product_id": products.product_id.to_numpy()[prod_idx],
        "created_date": created,
        "closed_date": pd.Series(close).where(is_closed),
        "stage": np.where(~is_closed, open_stage, np.where(won, "Closed Won", "Closed Lost")),
        "furthest_stage": np.where(~is_closed, open_stage,
                                   np.where(won, "Closed Won", lost_furthest)),
        "amount": np.round(amount, 2),
        "is_won": pd.Series(won, dtype="boolean").where(is_closed),
        "_region": acct_region,  # helper, dropped before return
    })


def make_deals(rng: np.random.Generator, reps: pd.DataFrame, accounts: pd.DataFrame,
               products: pd.DataFrame) -> pd.DataFrame:
    base = draw_deals(rng, N_DEALS, reps, accounts, products)
    parts = [base]
    for date, region, metric, direction in ANOMALIES:
        day = pd.Timestamp(date)
        if metric == NEW_PIPELINE and direction == "spike":
            parts.append(draw_deals(rng, SPIKE_NEW_DEALS, reps, accounts, products,
                                    created=pd.DatetimeIndex([day] * SPIKE_NEW_DEALS),
                                    region=region))
        elif metric == CLOSED_WON and direction == "spike":
            parts.append(draw_deals(rng, SPIKE_WON_DEALS, reps, accounts, products,
                                    region=region, won_on=day))
    deals = pd.concat(parts, ignore_index=True)

    # Pipeline drops: that region creates nothing that day.
    for date, region, metric, direction in ANOMALIES:
        if metric == NEW_PIPELINE and direction == "drop":
            gone = (deals.created_date == pd.Timestamp(date)) & (deals._region == region)
            deals = deals[~gone]

    deals = deals.drop(columns="_region")
    deals = deals.sort_values("created_date", kind="stable").reset_index(drop=True)
    deals.insert(0, "deal_id", np.arange(1, len(deals) + 1))
    return deals


def make_activities(rng: np.random.Generator, deals: pd.DataFrame) -> pd.DataFrame:
    counts = rng.poisson(ACTIVITIES_PER_DEAL, size=len(deals))
    idx = np.repeat(np.arange(len(deals)), counts)
    start = deals.created_date.to_numpy()[idx]
    end = deals.closed_date.fillna(HISTORY_END).to_numpy()[idx]
    span_days = (end - start).astype("timedelta64[D]").astype(int)
    offset = np.floor(rng.random(len(idx)) * (span_days + 1)).astype(int)
    return pd.DataFrame({
        "activity_id": np.arange(1, len(idx) + 1),
        "deal_id": deals.deal_id.to_numpy()[idx],
        "rep_id": deals.rep_id.to_numpy()[idx],
        "activity_date": start + offset.astype("timedelta64[D]"),
        "activity_type": rng.choice(ACTIVITY_TYPES, size=len(idx), p=ACTIVITY_WEIGHTS),
    })


# ---------- measurement helpers (also used by tests and plots) ----------

def with_region(deals: pd.DataFrame, reps: pd.DataFrame) -> pd.DataFrame:
    return deals.assign(region=deals.rep_id.map(reps.set_index("rep_id").region))


def reporting_quarters() -> pd.PeriodIndex:
    return pd.period_range(HISTORY_START, HISTORY_END, freq="Q")


def rep_quarter_closed_won(deals: pd.DataFrame, reps: pd.DataFrame) -> pd.DataFrame:
    """Closed-won amount per rep per reporting quarter, zeros included (rep x quarter grid)."""
    won = deals[deals.is_won == True]
    won = won[won.closed_date >= HISTORY_START]
    totals = won.groupby([won.rep_id, won.closed_date.dt.to_period("Q")]).amount.sum()
    grid = pd.MultiIndex.from_product([reps.rep_id, reporting_quarters()],
                                      names=["rep_id", "quarter"])
    return totals.reindex(grid, fill_value=0.0).rename("closed_won").reset_index()


def calibrate_quota(reps: pd.DataFrame, deals: pd.DataFrame) -> pd.DataFrame:
    """One team-wide quarterly quota, set so normal reps average TEAM_ATTAINMENT_TARGET."""
    cw = rep_quarter_closed_won(deals, reps)
    normal_avg = cw[~cw.rep_id.isin(WEAK_REP_IDS)].closed_won.mean()
    quota = round(normal_avg / TEAM_ATTAINMENT_TARGET, -3)
    return reps.assign(quarterly_quota=float(quota))


def daily_series(deals: pd.DataFrame, reps: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Daily value per region (date x region, zeros filled) for one anomaly metric."""
    d = with_region(deals, reps)
    if metric == NEW_PIPELINE:
        d, date_col = d, "created_date"
    else:
        d, date_col = d[d.is_won == True], "closed_date"
    s = d.groupby([d[date_col], d.region]).amount.sum().unstack(fill_value=0.0)
    days = pd.date_range(WARMUP_START, HISTORY_END, freq="D")
    return s.reindex(index=days, columns=REGIONS, fill_value=0.0)


def trailing_median(series: pd.Series, day: pd.Timestamp) -> float:
    window = series.loc[day - pd.Timedelta(days=BASELINE_WINDOW_DAYS): day - pd.Timedelta(days=1)]
    return float(window.median())


def quarterly_win_rate(deals: pd.DataFrame, reps: pd.DataFrame) -> pd.DataFrame:
    d = with_region(deals, reps)
    d = d[d.closed_date.notna() & (d.closed_date >= HISTORY_START)]
    return (d.groupby([d.region, d.closed_date.dt.to_period("Q")]).is_won.mean()
            .astype(float).unstack(level=0))


def make_ground_truth(deals: pd.DataFrame, reps: pd.DataFrame) -> pd.DataFrame:
    rows = []

    # 1. Seasonality: Q4-2025 closed-won value vs the average Q2/Q3 quarter.
    won = deals[(deals.is_won == True) & (deals.closed_date >= HISTORY_START)]
    by_q = won.groupby(won.closed_date.dt.to_period("Q")).amount.sum()
    q4 = by_q[pd.Period("2025Q4")]
    mid_year = by_q[[p for p in by_q.index if p.quarter in (2, 3)]].mean()
    rows.append(dict(kind="pattern", region=None, rep_id=None,
                     start_date="2025-10-01", end_date="2025-12-31", metric="closed_won_value",
                     magnitude=round(q4 / mid_year - 1, 4),
                     description="Seasonality: Q4 closes more deal value than an average Q2/Q3 "
                                 "quarter; December is the peak month. Magnitude = relative uplift."))

    # 2. West Q3-2025 win-rate dip vs West's other reporting quarters.
    wr = quarterly_win_rate(deals, reps)["West"]
    dip_q = pd.Period("2025Q3")
    rows.append(dict(kind="pattern", region="West", rep_id=None,
                     start_date=str(WEST_DIP_START.date()), end_date=str(WEST_DIP_END.date()),
                     metric="win_rate",
                     magnitude=round(wr[dip_q] / wr.drop(dip_q).mean() - 1, 4),
                     description="West win rate drops in Jul-Sep 2025, then recovers. "
                                 "Magnitude = relative change vs West's other quarters."))

    # 3. Weak reps: average quarterly attainment over the reporting period.
    cw = rep_quarter_closed_won(deals, reps)
    cw["attainment"] = cw.closed_won / reps.quarterly_quota.iloc[0]
    team = cw[~cw.rep_id.isin(WEAK_REP_IDS)].attainment.mean()
    for rep_id in WEAK_REP_IDS:
        rep = reps.set_index("rep_id").loc[rep_id]
        rows.append(dict(kind="pattern", region=rep.region, rep_id=rep_id,
                         start_date=str(HISTORY_START.date()), end_date=str(HISTORY_END.date()),
                         metric="quota_attainment",
                         magnitude=round(cw[cw.rep_id == rep_id].attainment.mean(), 4),
                         description=f"Weak rep {rep.rep_name}: average quarterly quota attainment "
                                     f"well below the rest of the team ({team:.0%})."))

    # 4. Point anomalies: value / trailing 28-day median of the same region and metric.
    series = {m: daily_series(deals, reps, m) for m in (NEW_PIPELINE, CLOSED_WON)}
    for date, region, metric, direction in ANOMALIES:
        day = pd.Timestamp(date)
        s = series[metric][region]
        baseline = trailing_median(s, day)
        rows.append(dict(kind="anomaly", region=region, rep_id=None,
                         start_date=date, end_date=date, metric=metric,
                         magnitude=round(s[day] / baseline, 4) if baseline else 0.0,
                         description=f"{direction.capitalize()} in daily {metric} for {region}. "
                                     f"Magnitude = value / trailing {BASELINE_WINDOW_DAYS}-day median."))

    gt = pd.DataFrame(rows)
    gt["rep_id"] = gt.rep_id.astype("Int64")
    gt["start_date"] = pd.to_datetime(gt.start_date)
    gt["end_date"] = pd.to_datetime(gt.end_date)
    return gt[["kind", "description", "region", "rep_id", "start_date", "end_date",
               "metric", "magnitude"]]


def generate(seed: int = SEED) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    fake = Faker()
    fake.seed_instance(seed)

    reps = make_reps(rng, fake)
    accounts = make_accounts(rng, fake)
    products = make_products()
    deals = make_deals(rng, reps, accounts, products)
    reps = calibrate_quota(reps, deals)
    return {
        "dim_rep": reps,
        "dim_account": accounts,
        "dim_product": products,
        "dim_date": make_dates(),
        "fact_deals": deals,
        "fact_activities": make_activities(rng, deals),
        "ground_truth": make_ground_truth(deals, reps),
    }


def main() -> None:
    tables = generate()
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)
    for name, df in tables.items():
        df.to_csv(out / f"{name}.csv", index=False)
        print(f"{name:16} {len(df):>7,} rows")

    deals = tables["fact_deals"]
    closed = deals[deals.closed_date.notna()]
    cycle = (closed.closed_date - closed.created_date).dt.days
    print(f"\nclosed deals: {len(closed):,}  open: {deals.closed_date.isna().sum():,}")
    print(f"win rate: {closed.is_won.mean():.3f}  median cycle: {cycle.median():.0f} days")
    print(f"quarterly quota: {tables['dim_rep'].quarterly_quota.iloc[0]:,.0f}")
    print("\nground truth:")
    with pd.option_context("display.width", 140, "display.max_colwidth", 40):
        print(tables["ground_truth"].drop(columns="description").to_string(index=False))
    print(f"\nCSVs written to {out}")


if __name__ == "__main__":
    main()
