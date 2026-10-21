# RevenuePulse — Product Brief (draft, Harsh to edit)

## Problem
Sales leaders at a mid-sized B2B software company get answers to everyday questions slowly. "Why did West's win rate drop?" or "Which reps are behind quota?" waits on an analyst writing SQL. Problems like a sudden pipeline drop get noticed days late.

## Users
- **Sales manager / VP Sales:** checks headline KPIs and rep performance, and asks ad-hoc questions in plain English.
- **Sales ops analyst:** owns KPI definitions and trusts the numbers because they are defined once, in SQL.

## What it does (v1)
1. A dashboard of core sales KPIs (win rate, funnel, sales cycle, quota attainment, deal size, pipeline velocity, churn risk), filterable by period and region.
2. A rep view comparing one rep against the team.
3. An anomaly feed: days where new pipeline or closed-won value broke sharply from its recent baseline, per region.
4. An "Ask" page: a plain-English question becomes guarded, read-only SQL, and the answer comes back as a chart plus a 2-line narrative.
5. A daily digest on Telegram and email summarising yesterday's KPIs and anomalies.

## Out of scope (v1)
Authentication, user accounts, forecasting/ML, Docker, custom domain, real customer data, more than 5 pages.

## Success metrics
| Metric | Target |
|---|---|
| Chatbot accuracy on the 25-question eval set | baseline logged, then improved after one tuning round |
| Anomaly-detection recall on planted anomalies | measured and logged (target ≥ 0.8) |
| Anomaly-detection precision | measured and logged |
| Unsafe queries ("delete", "drop", system tables) executed | 0 |
| Cold usability | a first-time user on a phone gets an answer without help |

## Constraints
₹0 budget (free tiers only). Synthetic data only. Built in a 4-day sprint.
