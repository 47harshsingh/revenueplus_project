-- Heavier KPIs as materialized views: results are stored, so reads are instant,
-- but they only change when refreshed. Run `SELECT refresh_kpis();` after any data change.
-- Definitions live in METRICS.md. Applied after 02_views.sql.

-- Stage funnel: deals created per month x region, how many reached each stage.
-- "Reached" = furthest_stage at or beyond that stage.
CREATE MATERIALIZED VIEW mv_funnel_monthly AS
WITH months AS (
    SELECT DISTINCT date_trunc('month', date_key)::date AS month
    FROM dim_date, v_as_of
    WHERE date_key BETWEEN DATE '2025-04-01' AND as_of
),
regions AS (SELECT DISTINCT region FROM dim_rep),
ranked AS (
    SELECT created_month AS month, region,
           CASE furthest_stage
               WHEN 'Prospecting' THEN 1 WHEN 'Qualified' THEN 2 WHEN 'Proposal' THEN 3
               WHEN 'Negotiation' THEN 4 WHEN 'Closed Won' THEN 5 END AS stage_rank
    FROM v_deal_facts
    WHERE created_date >= DATE '2025-04-01'
),
counts AS (
    SELECT month, region,
           count(*)                                  AS deals_created,
           count(*) FILTER (WHERE stage_rank >= 1)   AS reached_prospecting,
           count(*) FILTER (WHERE stage_rank >= 2)   AS reached_qualified,
           count(*) FILTER (WHERE stage_rank >= 3)   AS reached_proposal,
           count(*) FILTER (WHERE stage_rank >= 4)   AS reached_negotiation,
           count(*) FILTER (WHERE stage_rank >= 5)   AS reached_won
    FROM ranked
    GROUP BY 1, 2
)
SELECT m.month, r.region,
       coalesce(c.deals_created, 0)       AS deals_created,
       coalesce(c.reached_prospecting, 0) AS reached_prospecting,
       coalesce(c.reached_qualified, 0)   AS reached_qualified,
       coalesce(c.reached_proposal, 0)    AS reached_proposal,
       coalesce(c.reached_negotiation, 0) AS reached_negotiation,
       coalesce(c.reached_won, 0)         AS reached_won,
       c.reached_qualified::numeric   / NULLIF(c.reached_prospecting, 0) AS conv_prospecting_to_qualified,
       c.reached_proposal::numeric    / NULLIF(c.reached_qualified, 0)   AS conv_qualified_to_proposal,
       c.reached_negotiation::numeric / NULLIF(c.reached_proposal, 0)    AS conv_proposal_to_negotiation,
       c.reached_won::numeric         / NULLIF(c.reached_negotiation, 0) AS conv_negotiation_to_won
FROM months m
CROSS JOIN regions r
LEFT JOIN counts c ON c.month = m.month AND c.region = r.region;

CREATE UNIQUE INDEX ON mv_funnel_monthly (month, region);

-- Pipeline velocity as of v_as_of, per region plus 'All'.
-- Rate inputs use deals closed in the trailing 180 days.
CREATE MATERIALIZED VIEW mv_pipeline_velocity AS
WITH a AS (SELECT as_of FROM v_as_of),
recent AS (
    SELECT coalesce(region, 'All') AS region,
           count(*) FILTER (WHERE is_won)                 AS won_180d,
           count(*) FILTER (WHERE NOT is_won)             AS lost_180d,
           coalesce(sum(amount) FILTER (WHERE is_won), 0) AS won_value_180d,
           sum(cycle_days)                                AS cycle_days_sum_180d,
           count(*)                                       AS closed_180d
    FROM v_deal_facts, a
    WHERE closed_date > a.as_of - 180 AND closed_date <= a.as_of
    GROUP BY GROUPING SETS ((region), ())
),
open_now AS (
    SELECT coalesce(region, 'All') AS region,
           count(*)    AS open_deals,
           sum(amount) AS open_pipeline_value
    FROM v_deal_facts, a
    WHERE closed_date IS NULL AND created_date <= a.as_of
    GROUP BY GROUPING SETS ((region), ())
),
rates AS (
    SELECT r.*,
           r.won_180d::numeric / NULLIF(r.won_180d + r.lost_180d, 0) AS win_rate_180d,
           r.won_value_180d / NULLIF(r.won_180d, 0)                  AS avg_deal_size_180d,
           r.cycle_days_sum_180d::numeric / NULLIF(r.closed_180d, 0) AS avg_sales_cycle_days_180d
    FROM recent r
)
SELECT o.region,
       (SELECT as_of FROM a)    AS as_of,
       o.open_deals,
       o.open_pipeline_value,
       r.won_180d, r.lost_180d, r.won_value_180d, r.cycle_days_sum_180d, r.closed_180d,
       r.win_rate_180d,
       r.avg_deal_size_180d,
       r.avg_sales_cycle_days_180d,
       o.open_deals * r.win_rate_180d * r.avg_deal_size_180d
           / NULLIF(r.avg_sales_cycle_days_180d, 0) AS velocity_per_day
FROM open_now o
JOIN rates r USING (region);

CREATE UNIQUE INDEX ON mv_pipeline_velocity (region);

-- Rule-based churn-risk score per account as of v_as_of. Each component is its own column
-- so the score is explainable.
CREATE MATERIALIZED VIEW mv_churn_risk AS
WITH a AS (SELECT as_of FROM v_as_of),
last_activity AS (
    SELECT d.account_id, max(x.activity_date) AS last_activity_date
    FROM fact_activities x JOIN fact_deals d USING (deal_id)
    GROUP BY 1
),
last_closed AS (  -- most recent closed deals first
    SELECT account_id, is_won,
           row_number() OVER (PARTITION BY account_id ORDER BY closed_date DESC, deal_id DESC) AS rn
    FROM fact_deals WHERE closed_date IS NOT NULL
),
last_two AS (
    SELECT account_id, count(*) = 2 AND bool_and(NOT is_won) AS last_two_lost
    FROM last_closed WHERE rn <= 2 GROUP BY 1
),
last_created AS (  -- most recent deals first
    SELECT account_id, amount,
           row_number() OVER (PARTITION BY account_id ORDER BY created_date DESC, deal_id DESC) AS rn
    FROM fact_deals
),
last_three AS (  -- oldest of the three (rn 3) > middle (rn 2) > newest (rn 1)
    SELECT account_id,
           count(*) = 3
           AND max(amount) FILTER (WHERE rn = 3) > max(amount) FILTER (WHERE rn = 2)
           AND max(amount) FILTER (WHERE rn = 2) > max(amount) FILTER (WHERE rn = 1)
               AS declining_deal_size
    FROM last_created WHERE rn <= 3 GROUP BY 1
),
parts AS (
    SELECT acc.account_id, acc.account_name, acc.region, a.as_of,
           la.last_activity_date,
           a.as_of - la.last_activity_date                                   AS days_since_last_activity,
           (la.last_activity_date IS NULL OR a.as_of - la.last_activity_date > 60) AS inactive_60d,
           coalesce(l2.last_two_lost, false)                                 AS last_two_lost,
           coalesce(l3.declining_deal_size, false)                           AS declining_deal_size
    FROM dim_account acc
    CROSS JOIN a
    LEFT JOIN last_activity la USING (account_id)
    LEFT JOIN last_two l2      USING (account_id)
    LEFT JOIN last_three l3    USING (account_id)
)
SELECT *,
       40 * inactive_60d::int + 30 * last_two_lost::int + 30 * declining_deal_size::int
           AS churn_risk_score
FROM parts;

CREATE UNIQUE INDEX ON mv_churn_risk (account_id);

-- Re-run every materialized view. Call after any data change (the daily job does).
CREATE OR REPLACE FUNCTION refresh_kpis() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    REFRESH MATERIALIZED VIEW mv_funnel_monthly;
    REFRESH MATERIALIZED VIEW mv_pipeline_velocity;
    REFRESH MATERIALIZED VIEW mv_churn_risk;
END;
$$;
