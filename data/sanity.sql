-- Post-load sanity checks. Each row: check name, actual value, expected value.
-- load.py fails (and rolls back the whole load) if any actual <> expected.
-- Most of these are also enforced by constraints; checking anyway proves the data, not just the schema.

SELECT 'orphan deal -> rep' AS check_name,
       (SELECT count(*) FROM fact_deals d LEFT JOIN dim_rep r USING (rep_id)
        WHERE r.rep_id IS NULL) AS actual, 0 AS expected
UNION ALL
SELECT 'orphan deal -> account',
       (SELECT count(*) FROM fact_deals d LEFT JOIN dim_account a USING (account_id)
        WHERE a.account_id IS NULL), 0
UNION ALL
SELECT 'orphan deal -> product',
       (SELECT count(*) FROM fact_deals d LEFT JOIN dim_product p USING (product_id)
        WHERE p.product_id IS NULL), 0
UNION ALL
SELECT 'orphan activity -> deal',
       (SELECT count(*) FROM fact_activities a LEFT JOIN fact_deals d USING (deal_id)
        WHERE d.deal_id IS NULL), 0
UNION ALL
SELECT 'deal closed before created',
       (SELECT count(*) FROM fact_deals WHERE closed_date < created_date), 0
UNION ALL
SELECT 'open deal with outcome set',
       (SELECT count(*) FROM fact_deals
        WHERE (closed_date IS NULL) <> (is_won IS NULL)), 0
UNION ALL
SELECT 'activity outside deal lifespan',
       (SELECT count(*) FROM fact_activities a JOIN fact_deals d USING (deal_id)
        WHERE a.activity_date < d.created_date
           OR a.activity_date > coalesce(d.closed_date, DATE '2026-09-30')), 0
UNION ALL
SELECT 'activity rep differs from deal rep',
       (SELECT count(*) FROM fact_activities a JOIN fact_deals d USING (deal_id)
        WHERE a.rep_id <> d.rep_id), 0
UNION ALL
SELECT 'reps per region <> 5',
       (SELECT count(*) FROM (SELECT region FROM dim_rep GROUP BY region
                              HAVING count(*) <> 5) x), 0
UNION ALL
SELECT 'ground_truth rows',
       (SELECT count(*) FROM ground_truth), 14;
