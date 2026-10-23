-- KPI views. Definitions live in METRICS.md; this file implements them.
-- Plain views: no stored data, always current. Applied after 01_schema.sql.
-- Ratios always sit next to their additive parts, so rollups can sum-then-divide.

-- The "now" for every as-of KPI: the latest date in the data, never the real calendar date.
CREATE VIEW v_as_of AS
SELECT greatest(
    (SELECT max(created_date)  FROM fact_deals),
    (SELECT max(closed_date)   FROM fact_deals),
    (SELECT max(activity_date) FROM fact_activities)
) AS as_of;

-- One row per deal with its dimensions joined in. The base for every other view
-- (and the main table the chatbot will query).
CREATE VIEW v_deal_facts AS
SELECT d.deal_id,
       d.created_date,
       d.closed_date,
       date_trunc('month', d.created_date)::date   AS created_month,
       date_trunc('month', d.closed_date)::date    AS closed_month,
       date_trunc('quarter', d.closed_date)::date  AS closed_quarter,
       d.stage,
       d.furthest_stage,
       d.is_won,
       d.amount,
       d.closed_date - d.created_date              AS cycle_days,   -- null while open
       d.rep_id,      r.rep_name, r.region,
       d.account_id,  a.account_name, a.industry, a.company_size,
       d.product_id,  p.product_name, p.product_line
FROM fact_deals d
JOIN dim_rep r     USING (rep_id)
JOIN dim_account a USING (account_id)
JOIN dim_product p USING (product_id);

-- Month x region: win rate, deal size, sales cycle (by closed_date) and new pipeline
-- (by created_date). Every month x region in the reporting window gets a row, even if empty.
CREATE VIEW v_monthly_region_kpis AS
WITH months AS (
    SELECT DISTINCT date_trunc('month', date_key)::date AS month
    FROM dim_date, v_as_of
    WHERE date_key BETWEEN DATE '2025-04-01' AND as_of
),
regions AS (SELECT DISTINCT region FROM dim_rep),
closed AS (
    SELECT closed_month AS month, region,
           count(*) FILTER (WHERE is_won)                 AS won_count,
           count(*) FILTER (WHERE NOT is_won)             AS lost_count,
           count(*)                                       AS closed_count,
           coalesce(sum(amount) FILTER (WHERE is_won), 0) AS won_value,
           sum(cycle_days)                                AS cycle_days_sum
    FROM v_deal_facts
    WHERE closed_date >= DATE '2025-04-01'
    GROUP BY 1, 2
),
created AS (
    SELECT created_month AS month, region,
           count(*)    AS new_deal_count,
           sum(amount) AS new_pipeline_value
    FROM v_deal_facts
    WHERE created_date >= DATE '2025-04-01'
    GROUP BY 1, 2
)
SELECT m.month,
       date_trunc('quarter', m.month)::date            AS quarter,
       r.region,
       coalesce(c.won_count, 0)                        AS won_count,
       coalesce(c.lost_count, 0)                       AS lost_count,
       coalesce(c.closed_count, 0)                     AS closed_count,
       coalesce(c.won_value, 0)                        AS won_value,
       coalesce(c.cycle_days_sum, 0)                   AS cycle_days_sum,
       coalesce(n.new_deal_count, 0)                   AS new_deal_count,
       coalesce(n.new_pipeline_value, 0)               AS new_pipeline_value,
       c.won_count::numeric / NULLIF(c.won_count + c.lost_count, 0) AS win_rate,
       c.won_value / NULLIF(c.won_count, 0)                         AS avg_deal_size,
       c.cycle_days_sum::numeric / NULLIF(c.closed_count, 0)        AS avg_sales_cycle_days
FROM months m
CROSS JOIN regions r
LEFT JOIN closed  c ON c.month = m.month AND c.region = r.region
LEFT JOIN created n ON n.month = m.month AND n.region = r.region;

-- Rep x quarter quota attainment. Zero-filled: a rep with no wins shows 0, not a missing row.
CREATE VIEW v_quota_attainment AS
WITH quarters AS (
    SELECT DISTINCT date_trunc('quarter', date_key)::date AS quarter
    FROM dim_date, v_as_of
    WHERE date_key BETWEEN DATE '2025-04-01' AND as_of
),
won AS (
    SELECT rep_id, closed_quarter AS quarter, sum(amount) AS closed_won_value
    FROM v_deal_facts
    WHERE is_won AND closed_date >= DATE '2025-04-01'
    GROUP BY 1, 2
)
SELECT r.rep_id, r.rep_name, r.region, q.quarter,
       coalesce(w.closed_won_value, 0)                                AS closed_won_value,
       r.quarterly_quota,
       coalesce(w.closed_won_value, 0) / NULLIF(r.quarterly_quota, 0) AS attainment,
       -- false for the quarter still in progress at as_of
       (q.quarter + INTERVAL '3 months' - INTERVAL '1 day')::date <= (SELECT as_of FROM v_as_of)
                                                                      AS is_complete_quarter
FROM dim_rep r
CROSS JOIN quarters q
LEFT JOIN won w ON w.rep_id = r.rep_id AND w.quarter = q.quarter;

-- Day x region series, zero-filled from the first date in the data to as_of.
-- Input for the anomaly detector (M7).
CREATE VIEW v_daily_region_metrics AS
WITH days AS (
    SELECT date_key AS day
    FROM dim_date, v_as_of
    WHERE date_key BETWEEN (SELECT min(created_date) FROM fact_deals) AND as_of
),
regions AS (SELECT DISTINCT region FROM dim_rep),
created AS (
    SELECT created_date AS day, region, count(*) AS n, sum(amount) AS v
    FROM v_deal_facts GROUP BY 1, 2
),
won AS (
    SELECT closed_date AS day, region, count(*) AS n, sum(amount) AS v
    FROM v_deal_facts WHERE is_won GROUP BY 1, 2
)
SELECT d.day, r.region,
       coalesce(c.n, 0) AS new_deal_count,
       coalesce(c.v, 0) AS new_pipeline_value,
       coalesce(w.n, 0) AS closed_won_count,
       coalesce(w.v, 0) AS closed_won_value
FROM days d
CROSS JOIN regions r
LEFT JOIN created c ON c.day = d.day AND c.region = r.region
LEFT JOIN won     w ON w.day = d.day AND w.region = r.region;
