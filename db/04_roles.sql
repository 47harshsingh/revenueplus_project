-- chatbot_ro: the only login the AI chatbot uses. It can read the KPI views and nothing else.
-- Applied by data/load.py after the views exist. Re-runnable.
-- The password is never in this file: load.py sets it from the CHAT_DB_PASSWORD env var.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'chatbot_ro') THEN
        CREATE ROLE chatbot_ro LOGIN;
    END IF;
END
$$;

-- No ALTER ROLE ... NOSUPERUSER here: only a superuser may even mention those attributes, and
-- Supabase's postgres login is not one. A role created by a non-superuser can't hold them
-- anyway; tests/test_roles.py checks it has none.

-- Applied by Postgres at every login, whatever the client asks for.
ALTER ROLE chatbot_ro SET default_transaction_read_only = on;
ALTER ROLE chatbot_ro SET statement_timeout = '5s';

-- Start from nothing, then grant exactly the allowlist.
REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM chatbot_ro;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM chatbot_ro;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM chatbot_ro;
-- Functions are executable by PUBLIC (every role) by default; only the owner may refresh.
REVOKE EXECUTE ON FUNCTION refresh_kpis() FROM PUBLIC;

GRANT USAGE ON SCHEMA public TO chatbot_ro;
GRANT SELECT ON
    v_as_of,
    v_deal_facts,
    v_monthly_region_kpis,
    v_quota_attainment,
    v_daily_region_metrics,
    mv_funnel_monthly,
    mv_pipeline_velocity,
    mv_churn_risk
TO chatbot_ro;
-- Deliberately NOT granted: fact_*, dim_* (raw tables), ground_truth (the eval answer key),
-- anomalies, and every function.
