from fastapi import APIRouter, HTTPException, Path

from api.db import fetch_all, fetch_one
from api.models import RepDetail, RepQuarter, RepSummary

router = APIRouter(tags=["reps"])

# One row per rep: average over complete quarters + the latest complete quarter.
REP_SUMMARY = """
    WITH q AS (SELECT * FROM v_quota_attainment WHERE is_complete_quarter),
    latest AS (SELECT DISTINCT ON (rep_id) rep_id, quarter, attainment
               FROM q ORDER BY rep_id, quarter DESC)
    SELECT r.rep_id, r.rep_name, r.region, r.quarterly_quota,
           (SELECT avg(attainment) FROM q WHERE q.rep_id = r.rep_id) AS avg_attainment,
           l.quarter AS latest_quarter, l.attainment AS latest_quarter_attainment
    FROM dim_rep r LEFT JOIN latest l USING (rep_id)
"""


@router.get("/reps", response_model=list[RepSummary])
def list_reps():
    return fetch_all(REP_SUMMARY + " ORDER BY r.rep_id")


@router.get("/reps/{rep_id}", response_model=RepDetail)
def rep_detail(rep_id: int = Path(ge=1, le=2_147_483_647)):  # Postgres integer range
    rep = fetch_one(REP_SUMMARY + " WHERE r.rep_id = %(rep_id)s", {"rep_id": rep_id})
    if rep is None:
        raise HTTPException(status_code=404, detail=f"rep {rep_id} not found")

    quarters = fetch_all("""
        SELECT quarter,
               max(attainment) FILTER (WHERE rep_id = %(rep_id)s)         AS rep_attainment,
               sum(closed_won_value) / NULLIF(sum(quarterly_quota), 0)   AS team_avg_attainment
        FROM v_quota_attainment
        GROUP BY quarter ORDER BY quarter
    """, {"rep_id": rep_id})

    wins = fetch_one("""
        SELECT count(*) FILTER (WHERE rep_id = %(rep_id)s AND is_won)     AS won_count,
               count(*) FILTER (WHERE rep_id = %(rep_id)s AND NOT is_won) AS lost_count,
               avg(is_won::int) FILTER (WHERE rep_id = %(rep_id)s)        AS win_rate,
               avg(is_won::int)                                           AS team_win_rate
        FROM v_deal_facts
        WHERE closed_date >= DATE '2025-04-01'
    """, {"rep_id": rep_id})

    return RepDetail(rep=RepSummary(**rep), quarters=[RepQuarter(**q) for q in quarters], **wins)
