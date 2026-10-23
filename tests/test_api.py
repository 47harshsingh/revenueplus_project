"""API endpoint tests.

The data tests need a throwaway Postgres (TEST_DATABASE_URL); they load generate(42) and
check the API returns the same numbers METRICS.md quotes. The no-database tests always run.
"""

import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from api import db
from api.main import app
from data.generate import generate
from data.load import load_all

TEST_DB = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


@pytest.fixture(scope="module")
def client():
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL not set")
    with psycopg.connect(TEST_DB) as conn:
        with conn.transaction():
            load_all(conn, generate(42))
    mp = pytest.MonkeyPatch()
    mp.setenv("DATABASE_URL", TEST_DB)
    with TestClient(app) as c:  # runs the lifespan: opens the pool
        db.pool.wait(timeout=10)
        yield c
    mp.undo()


@pytest.fixture
def no_db_client(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(app) as c:
        yield c


# ---------- no database ----------

def test_without_database_data_endpoints_503_but_health_ok(no_db_client):
    assert no_db_client.get("/health").status_code == 200
    r = no_db_client.get("/kpis")
    assert r.status_code == 503
    assert r.json() == {"detail": "database unavailable"}


def test_validation_happens_before_database(no_db_client):
    """Bad input is the caller's fault: 422 even when the DB is down."""
    assert no_db_client.get("/kpis", params={"region": "Mars"}).status_code == 422
    assert no_db_client.get("/kpis", params={"start": "not-a-date"}).status_code == 422
    assert no_db_client.get("/kpis/trend", params={"metric": "profit"}).status_code == 422


# ---------- /kpis ----------

@needs_db
def test_kpis_full_window_matches_metrics_md(client):
    r = client.get("/kpis")
    assert r.status_code == 200
    k = r.json()
    assert k["period"] == {"start_month": "2025-04-01", "end_month": "2026-09-01"}
    assert k["won_count"] == 2994
    assert abs(k["avg_deal_size"] - 35171.61) < 0.01
    assert abs(k["avg_sales_cycle_days"] - 49.69) < 0.01
    assert abs(k["pipeline_velocity_per_day"] - 183399.18) < 0.01


@needs_db
def test_kpis_month_and_region_filter(client):
    # mid-month dates snap to the whole month of August
    k = client.get("/kpis", params={"start": "2025-08-10", "end": "2025-08-20",
                                    "region": "West"}).json()
    assert k["period"] == {"start_month": "2025-08-01", "end_month": "2025-08-01"}
    assert (k["won_count"], k["lost_count"]) == (23, 114)
    assert abs(k["win_rate"] - 0.1679) < 0.001


@needs_db
def test_kpis_start_after_end_is_422(client):
    r = client.get("/kpis", params={"start": "2026-01-01", "end": "2025-01-01"})
    assert r.status_code == 422


@needs_db
def test_kpis_outside_data_is_empty_not_error(client):
    k = client.get("/kpis", params={"start": "2030-01-01", "end": "2030-12-31"}).json()
    assert k["period"] == {"start_month": None, "end_month": None}
    assert k["won_count"] == 0 and k["win_rate"] is None


# ---------- /kpis/trend ----------

@needs_db
def test_trend_monthly_win_rate_west(client):
    t = client.get("/kpis/trend", params={"metric": "win_rate", "region": "West"}).json()
    assert len(t["points"]) == 18
    aug = next(p for p in t["points"] if p["period"] == "2025-08-01")
    assert (aug["numerator"], aug["denominator"]) == (23, 137)
    assert abs(aug["value"] - 0.1679) < 0.001


@needs_db
def test_trend_quarterly_is_sum_then_divide(client):
    q = client.get("/kpis/trend", params={"metric": "avg_deal_size", "grain": "quarter"}).json()
    assert len(q["points"]) == 6
    total_value = sum(p["numerator"] for p in q["points"])
    total_won = sum(p["denominator"] for p in q["points"])
    assert abs(total_value / total_won - 35171.61) < 0.01


@needs_db
def test_trend_plain_total_has_no_denominator(client):
    t = client.get("/kpis/trend", params={"metric": "new_deal_count"}).json()
    assert all(p["denominator"] is None and p["value"] == p["numerator"] for p in t["points"])


# ---------- safety ----------

@needs_db
def test_pool_connections_are_read_only(client):
    with db.pool.connection() as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("DELETE FROM fact_deals")
