from datetime import date

from fastapi import APIRouter, Query

from api.db import fetch_one
from api.models import FunnelResponse, FunnelStage, Period, Region
from api.routers.kpis import FILTERS, check_range

router = APIRouter(tags=["funnel"])

STAGES = [("Prospecting", "reached_prospecting"), ("Qualified", "reached_qualified"),
          ("Proposal", "reached_proposal"), ("Negotiation", "reached_negotiation"),
          ("Closed Won", "reached_won")]


@router.get("/funnel", response_model=FunnelResponse)
def funnel(
    start: date | None = Query(None, description="Created-date month, snapped to month start"),
    end: date | None = Query(None, description="Created-date month, snapped to month end"),
    region: Region | None = None,
):
    check_range(start, end)
    row = fetch_one("""
        SELECT min(month) AS start_month, max(month) AS end_month,
               coalesce(sum(deals_created), 0)       AS deals_created,
               coalesce(sum(reached_prospecting), 0) AS reached_prospecting,
               coalesce(sum(reached_qualified), 0)   AS reached_qualified,
               coalesce(sum(reached_proposal), 0)    AS reached_proposal,
               coalesce(sum(reached_negotiation), 0) AS reached_negotiation,
               coalesce(sum(reached_won), 0)         AS reached_won
        FROM mv_funnel_monthly
        WHERE """ + FILTERS,
        {"start": start, "end": end, "region": region.value if region else None})
    stages, prev = [], None
    for name, col in STAGES:
        reached = row[col]
        conv = None if prev is None else (reached / prev if prev else None)
        stages.append(FunnelStage(stage=name, reached=reached, conversion_from_previous=conv))
        prev = reached
    return FunnelResponse(period=Period(start_month=row["start_month"], end_month=row["end_month"]),
                          region=region, deals_created=row["deals_created"], stages=stages)
