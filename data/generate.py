"""Seeded synthetic sales data for RevenuePulse.

generate(seed) returns one DataFrame per table in db/01_schema.sql.
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
        "quarterly_quota": 100_000.0,  # placeholder; calibrated against closed-won in Step 4
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


def make_deals(rng: np.random.Generator, reps: pd.DataFrame, accounts: pd.DataFrame,
               products: pd.DataFrame) -> pd.DataFrame:
    # Created date: weekdays 4x as likely as weekends.
    days = pd.date_range(WARMUP_START, HISTORY_END, freq="D")
    weights = np.where(days.dayofweek < 5, 4.0, 1.0)
    created = pd.DatetimeIndex(rng.choice(days, size=N_DEALS, p=weights / weights.sum()))

    # Account first, then a rep from the account's region, so regional numbers are coherent.
    acct_idx = rng.integers(0, len(accounts), size=N_DEALS)
    acct = accounts.iloc[acct_idx].reset_index(drop=True)
    reps_by_region = {r: reps.loc[reps.region == r, "rep_id"].to_numpy() for r in REGIONS}
    rep_id = np.array([rng.choice(reps_by_region[r]) for r in acct.region])

    prod_idx = rng.integers(0, len(products), size=N_DEALS)
    list_price = products.list_price.to_numpy()[prod_idx]
    size_mult = acct.company_size.map(SIZE_MULTIPLIER).to_numpy()
    amount = np.round(list_price * size_mult * rng.lognormal(0, 0.35, size=N_DEALS), 2)

    cycle = np.clip(rng.lognormal(np.log(CYCLE_MEDIAN_DAYS), 0.5, size=N_DEALS),
                    CYCLE_MIN_DAYS, CYCLE_MAX_DAYS).astype(int)
    would_close = created + pd.to_timedelta(cycle, unit="D")
    is_closed = would_close <= HISTORY_END

    won = rng.random(N_DEALS) < BASE_WIN_RATE
    open_stage = rng.choice(OPEN_STAGES, size=N_DEALS, p=OPEN_STAGE_WEIGHTS)
    lost_furthest = rng.choice(OPEN_STAGES, size=N_DEALS, p=LOST_FURTHEST_WEIGHTS)

    stage = np.where(~is_closed, open_stage, np.where(won, "Closed Won", "Closed Lost"))
    furthest = np.where(~is_closed, open_stage, np.where(won, "Closed Won", lost_furthest))

    deals = pd.DataFrame({
        "rep_id": rep_id,
        "account_id": acct.account_id.to_numpy(),
        "product_id": products.product_id.to_numpy()[prod_idx],
        "created_date": created,
        "closed_date": pd.Series(would_close).where(is_closed),
        "stage": stage,
        "furthest_stage": furthest,
        "amount": amount,
        "is_won": pd.Series(won, dtype="boolean").where(is_closed),
    })
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


def generate(seed: int = SEED) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    fake = Faker()
    fake.seed_instance(seed)

    reps = make_reps(rng, fake)
    accounts = make_accounts(rng, fake)
    products = make_products()
    deals = make_deals(rng, reps, accounts, products)
    return {
        "dim_rep": reps,
        "dim_account": accounts,
        "dim_product": products,
        "dim_date": make_dates(),
        "fact_deals": deals,
        "fact_activities": make_activities(rng, deals),
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
    print(f"CSVs written to {out}")


if __name__ == "__main__":
    main()
