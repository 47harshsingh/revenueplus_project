# RevenuePulse KPI definitions

These are the **only** definitions of each KPI. The SQL views in `db/02_views.sql` and
`db/03_matviews.sql` implement them, and the API, the chatbot and the daily digest all read
those views. Every KPI has a pytest that recomputes it in pandas and must match SQL within 0.001.

Worked examples below come from the seeded data (`seed=42`), so you can check them.

## Ground rules

| Rule | Meaning |
|---|---|
| **Reporting window** | 2025-04-01 → `as_of`. Deals created 2025-01-01 → 2025-03-31 exist only as warm-up pipeline and are excluded from period KPIs. |
| **`as_of`** | The latest date in the data: `max(created_date, closed_date, activity_date)`, currently **2026-09-30**. All "as of now" KPIs use it, never the real calendar date. The daily job moves it forward. Exposed as `v_as_of`. |
| **Outcome date** | Win rate, deal size, sales cycle and quota attainment are counted by **`closed_date`** ("when did we win or lose it"). |
| **Pipeline date** | Funnel and new pipeline are counted by **`created_date`** ("when did it enter the pipeline"). |
| **Region** | The region of the deal's rep. That's always the same as the account's region in this data. |
| **Division** | Every denominator is wrapped in `NULLIF(x, 0)`. An empty segment gives `NULL` ("no data"), never 0 and never an error. |
| **Rollups** | Views expose the additive parts (counts, sums) next to every ratio. To roll up (month → quarter, regions → all), **sum the parts, then divide**. Never average the ratios. See the trap below. |

### The average-of-averages trap

Average won-deal size, Apr-2025 → Sep-2026, by company size:

| Size | Won deals | Avg deal size |
|---|---|---|
| Enterprise | 283 | 107,625 |
| Mid | 912 | 44,839 |
| SMB | 1,799 | 18,873 |
| **Average of the 3 averages** | | **57,112** (wrong) |
| **Pooled: total won value ÷ total won deals** | 2,994 | **35,172** (right) |

The wrong answer is 62% too high, because it gives 283 Enterprise deals the same weight as
1,799 SMB deals. This is why the views carry the counts and sums, not just the ratios.

## KPIs

### 1. Win rate
- **Definition:** of the deals that closed in the period, the share that were won.
- **Formula:** `won_count / NULLIF(won_count + lost_count, 0)`
- **Date basis:** `closed_date`. **Open deals are excluded.** A deal still in Proposal is neither a win nor a loss.
- **Grain / object:** month × region in `v_monthly_region_kpis` (`won_count`, `lost_count`, `win_rate`).
- **Example:** West, Aug-2025: 23 won, 114 lost, so 23 / 137 = **16.8%**. This is inside the planted West dip; West's normal level is about 28–30%.

### 2. Average deal size
- **Definition:** the mean amount of deals **won** in the period.
- **Formula:** `won_value / NULLIF(won_count, 0)`
- **Date basis:** `closed_date`.
- **Grain / object:** `v_monthly_region_kpis` (`won_value`, `won_count`, `avg_deal_size`).
- **Example:** Apr-2025 → Sep-2026, all regions: 2,994 won deals, so **35,172** on average.

### 3. Average sales cycle
- **Definition:** the mean number of days from creation to close, for deals closed in the period (won **and** lost).
- **Formula:** `cycle_days_sum / NULLIF(closed_count, 0)`, where `cycle_days = closed_date - created_date`.
- **Date basis:** `closed_date`. Open deals are excluded, because their cycle isn't finished.
- **Grain / object:** `v_monthly_region_kpis` (`cycle_days_sum`, `closed_count`, `avg_sales_cycle_days`).
- **Example:** Apr-2025 → Sep-2026, all regions: **49.7 days**.

### 4. New pipeline
- **Definition:** the number and total amount of deals **created** in the period, whatever happened to them later.
- **Formula:** `new_deal_count = count(*)`, `new_pipeline_value = sum(amount)`.
- **Date basis:** `created_date`.
- **Grain / object:** month × region in `v_monthly_region_kpis`. Day × region (zero-filled, used by the anomaly detector) in `v_daily_region_metrics`.

### 5. Quota attainment
- **Definition:** a rep's closed-won value in a quarter, as a share of their quarterly quota.
- **Formula:** `closed_won_value / NULLIF(quarterly_quota, 0)`
- **Date basis:** `closed_date`, by calendar quarter.
- **Grain / object:** rep × quarter in `v_quota_attainment`. The grid is **zero-filled**: a rep with no wins in a quarter shows 0.0, not a missing row. Otherwise "average attainment" would silently skip their worst quarters.
- **Example:** rep 4, 2025-Q3: 629,596 won ÷ 964,000 quota = **65.3%**. The team averages about 95%.

### 6. Stage funnel
- **Definition:** of the deals created in the period, how many reached each stage, and the conversion from each stage to the next.
- **Stage order:** Prospecting → Qualified → Proposal → Negotiation → Closed Won.
- **"Reached a stage"** means `furthest_stage` is at or beyond that stage. A deal lost at Proposal counts as reaching Prospecting, Qualified and Proposal. An open deal counts up to its current stage.
- **Formulas:**
  - `reached_<stage>` = count of deals whose furthest stage ≥ that stage
  - `conv_<a>_to_<b>` = `reached_b / NULLIF(reached_a, 0)`
- **Date basis:** `created_date`. Recent cohorts are still maturing: their later-stage counts will grow as open deals progress. Compare cohorts that are at least about 3 months old.
- **Grain / object:** month × region in `mv_funnel_monthly` (materialized).
- **Example:** deals created Jan-2026, all regions:

  | Stage | Prospecting | Qualified | Proposal | Negotiation | Closed Won |
  |---|---|---|---|---|---|
  | Deals | 597 | 502 | 369 | 249 | 153 |
  | Conversion from previous | – | 84.1% | 73.5% | 67.5% | 61.4% |

### 7. Pipeline velocity
- **Definition:** the expected closed-won value the current open pipeline produces **per day**.
- **Formula:** `(open_deals × win_rate × avg_deal_size) / NULLIF(avg_sales_cycle_days, 0)`
- **Inputs:**
  - `open_deals`: deals open as of `as_of`.
  - `win_rate`, `avg_deal_size`, `avg_sales_cycle_days`: computed on deals closed in the **trailing 180 days** up to `as_of`. Long enough to be stable, short enough to reflect current performance. The project plan doesn't fix a window; this one is our choice.
- **Grain / object:** one row per region plus an `All` row, in `mv_pipeline_velocity` (materialized).
- **Example:** All, as of 2026-09-30: 965 open × 28.07% × 34,069 ÷ 50.31 days = **183,399 per day**.

### 8. Churn-risk score
- **Definition:** a rule-based 0–100 score per account, as of `as_of`. Higher means more at risk.
- **Components** (they add up):

  | Points | Rule |
  |---|---|
  | +40 | **more than 60 days** since the last activity on any of the account's deals, or no activity ever |
  | +30 | the account's **last 2 closed deals** (by `closed_date`) were both lost |
  | +30 | the account's **last 3 deals** (by `created_date`) have strictly decreasing amounts |

- **Grain / object:** one row per account in `mv_churn_risk` (materialized), with each component as its own column so the score is explainable.
- **Accounts with too little history** (fewer than 2 closed deals, or fewer than 3 deals) score 0 on that component.
- **Current distribution (600 accounts):**

  | Score | 0 | 30 | 40 | 60 | 70 | 100 |
  |---|---|---|---|---|---|---|
  | Accounts | 219 | 317 | 8 | 40 | 15 | 1 |

- **Known weakness:** with an overall loss rate of about 72%, "last 2 closed deals lost" happens to about half of all accounts by chance, so a score of 30 carries almost no signal. Only scores ≥ 60 (56 accounts) separate meaningfully. A base-rate-aware rule (for example, an account's win rate well below its region's) would be stronger; that's a v2 change.

## Refreshing

Materialized views (funnel, velocity, churn) store their results and go stale when data changes.
Refresh them with:

```sql
SELECT refresh_kpis();
```

`data/load.py` recreates them from scratch on every load, so they're fresh after it. The daily job (M7)
must call `refresh_kpis()` after appending data. `tests/test_kpis.py` proves that a change is
invisible until the refresh, and visible after it.
