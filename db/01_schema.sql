-- RevenuePulse star schema.
-- Re-runnable: drops everything and rebuilds from scratch.
-- Grain: fact_deals = one deal (opportunity); fact_activities = one call/email/meeting.

DROP TABLE IF EXISTS anomalies, ground_truth, fact_activities, fact_deals,
    dim_date, dim_product, dim_account, dim_rep CASCADE;

-- ---------- dimensions ----------

CREATE TABLE dim_rep (
    rep_id          integer PRIMARY KEY,
    rep_name        text    NOT NULL,
    region          text    NOT NULL CHECK (region IN ('North', 'South', 'East', 'West')),
    hire_date       date    NOT NULL,
    quarterly_quota numeric(12, 2) NOT NULL CHECK (quarterly_quota > 0)
);

CREATE TABLE dim_account (
    account_id   integer PRIMARY KEY,
    account_name text NOT NULL,
    industry     text NOT NULL,
    company_size text NOT NULL CHECK (company_size IN ('SMB', 'Mid', 'Enterprise')),
    region       text NOT NULL CHECK (region IN ('North', 'South', 'East', 'West'))
);

CREATE TABLE dim_product (
    product_id   integer PRIMARY KEY,
    product_name text NOT NULL,
    product_line text NOT NULL,
    list_price   numeric(12, 2) NOT NULL CHECK (list_price > 0)
);

-- Covers 2025-01-01 (warm-up start) to 2027-12-31 so the daily job never hits a missing date.
CREATE TABLE dim_date (
    date_key   date     PRIMARY KEY,
    day        smallint NOT NULL,
    month      smallint NOT NULL,
    month_name text     NOT NULL,
    quarter    smallint NOT NULL,
    year       smallint NOT NULL,
    is_weekend boolean  NOT NULL
);

-- ---------- facts ----------

CREATE TABLE fact_deals (
    deal_id        integer PRIMARY KEY,
    rep_id         integer NOT NULL REFERENCES dim_rep (rep_id),
    account_id     integer NOT NULL REFERENCES dim_account (account_id),
    product_id     integer NOT NULL REFERENCES dim_product (product_id),
    created_date   date    NOT NULL REFERENCES dim_date (date_key),
    closed_date    date             REFERENCES dim_date (date_key),  -- null while open
    stage          text    NOT NULL CHECK (stage IN ('Prospecting', 'Qualified', 'Proposal',
                                                     'Negotiation', 'Closed Won', 'Closed Lost')),
    -- Furthest stage the deal reached; needed for the funnel because a lost deal's
    -- stage is just 'Closed Lost'.
    furthest_stage text    NOT NULL CHECK (furthest_stage IN ('Prospecting', 'Qualified', 'Proposal',
                                                              'Negotiation', 'Closed Won')),
    amount         numeric(12, 2) NOT NULL CHECK (amount > 0),
    is_won         boolean,                                          -- null while open

    CONSTRAINT closed_after_created CHECK (closed_date >= created_date),
    -- stage, is_won and closed_date must agree with each other
    CONSTRAINT outcome_consistent CHECK (
        (stage = 'Closed Won'  AND is_won IS TRUE  AND closed_date IS NOT NULL
                               AND furthest_stage = 'Closed Won')
     OR (stage = 'Closed Lost' AND is_won IS FALSE AND closed_date IS NOT NULL
                               AND furthest_stage <> 'Closed Won')
     OR (stage NOT IN ('Closed Won', 'Closed Lost') AND is_won IS NULL AND closed_date IS NULL
                               AND furthest_stage = stage)
    )
);

CREATE TABLE fact_activities (
    activity_id   integer PRIMARY KEY,
    deal_id       integer NOT NULL REFERENCES fact_deals (deal_id),
    rep_id        integer NOT NULL REFERENCES dim_rep (rep_id),
    activity_date date    NOT NULL REFERENCES dim_date (date_key),
    activity_type text    NOT NULL CHECK (activity_type IN ('Call', 'Email', 'Meeting'))
);

-- ---------- evaluation + detection ----------

-- What was deliberately planted in the synthetic data; evals and recall are scored against it.
CREATE TABLE ground_truth (
    id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind        text NOT NULL CHECK (kind IN ('pattern', 'anomaly')),
    description text NOT NULL,
    region      text,
    rep_id      integer REFERENCES dim_rep (rep_id),
    start_date  date NOT NULL,
    end_date    date NOT NULL,
    metric      text NOT NULL,
    magnitude   numeric NOT NULL,
    CHECK (end_date >= start_date)
);

-- Written by the daily job's detector (M7).
CREATE TABLE anomalies (
    id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    detected_on date    NOT NULL,
    metric_date date    NOT NULL,
    metric      text    NOT NULL,
    segment     text    NOT NULL,
    value       numeric NOT NULL,
    baseline    numeric NOT NULL,
    score       numeric NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (metric_date, metric, segment)
);

-- ---------- indexes ----------

CREATE INDEX ON fact_deals (rep_id);
CREATE INDEX ON fact_deals (account_id);
CREATE INDEX ON fact_deals (product_id);
CREATE INDEX ON fact_deals (created_date);
CREATE INDEX ON fact_deals (closed_date);
CREATE INDEX ON fact_deals (stage);
CREATE INDEX ON fact_activities (deal_id);
CREATE INDEX ON fact_activities (rep_id);
CREATE INDEX ON fact_activities (activity_date);

-- ---------- lock down Supabase's auto-generated REST API ----------
-- Supabase exposes every public table over HTTP to anyone with the project's anon key.
-- RLS with no policies blocks that. Our own connections (table owner) are unaffected.
ALTER TABLE dim_rep         ENABLE ROW LEVEL SECURITY;
ALTER TABLE dim_account     ENABLE ROW LEVEL SECURITY;
ALTER TABLE dim_product     ENABLE ROW LEVEL SECURITY;
ALTER TABLE dim_date        ENABLE ROW LEVEL SECURITY;
ALTER TABLE fact_deals      ENABLE ROW LEVEL SECURITY;
ALTER TABLE fact_activities ENABLE ROW LEVEL SECURITY;
ALTER TABLE ground_truth    ENABLE ROW LEVEL SECURITY;
ALTER TABLE anomalies       ENABLE ROW LEVEL SECURITY;
