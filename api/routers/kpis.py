from datetime import date

from fastapi import APIRouter, HTTPException, Query
from psycopg import sql

from api.db import fetch_all, fetch_one
from api.models import Grain, KpiSummary, Metric, Period, Region, TrendPoint, TrendResponse

router = APIRouter(tags=["kpis"])

# Optional filters: a NULL parameter means "no filter". Dates snap to whole months.
FILTERS = """
    (%(start)s::date IS NULL OR month >= date_trunc('month', %(start)s::date))
AND (%(end)s::date   IS NULL OR month <= date_trunc('month', %(end)s::date))
AND (%(region)s::text IS NULL OR region = %(region)s::text)
"""


def check_range(start: date | None, end: date | None) -> None:
    if start and end and start > end:
        raise HTTPException(status_code=422, detail="start must be on or before end")


@router.get("/kpis", response_model=KpiSummary)
def kpis(
    start: date | None = Query(None, description="Snapped to the start of its month"),
    end: date | None = Query(None, description="Snapped to the end of its month"),
    region: Region | None = None,
):
    check_range(start, end)
    params = {"start": start, "end": end, "region": region.value if region else None}
    # Sum the additive parts first, then divide (METRICS.md: never average the ratios).
    row = fetch_one("""
        SELECT min(month) AS start_month, max(month) AS end_month,
               coalesce(sum(won_count), 0)          AS won_count,
               coalesce(sum(lost_count), 0)         AS lost_count,
               coalesce(sum(won_value), 0)          AS won_value,
               coalesce(sum(new_deal_count), 0)     AS new_deal_count,
               coalesce(sum(new_pipeline_value), 0) AS new_pipeline_value,
               sum(won_count)::numeric / NULLIF(sum(won_count) + sum(lost_count), 0) AS win_rate,
               sum(won_value) / NULLIF(sum(won_count), 0)                            AS avg_deal_size,
               sum(cycle_days_sum)::numeric / NULLIF(sum(closed_count), 0)           AS avg_sales_cycle_days
        FROM v_monthly_region_kpis
        WHERE """ + FILTERS, params)
    velocity = fetch_one(
        "SELECT velocity_per_day FROM mv_pipeline_velocity WHERE region = coalesce(%(region)s, 'All')",
        params,
    )
    return KpiSummary(
        period=Period(start_month=row["start_month"], end_month=row["end_month"]),
        region=region,
        pipeline_velocity_per_day=velocity["velocity_per_day"] if velocity else None,
        **{k: row[k] for k in ("win_rate", "won_count", "lost_count", "avg_deal_size", "won_value",
                               "avg_sales_cycle_days", "new_deal_count", "new_pipeline_value")},
    )


# metric -> (numerator, denominator); denominator None = plain total. Fixed SQL, never user text.
METRIC_SQL = {
    Metric.win_rate: ("sum(won_count)", "sum(won_count) + sum(lost_count)"),
    Metric.avg_deal_size: ("sum(won_value)", "sum(won_count)"),
    Metric.avg_sales_cycle_days: ("sum(cycle_days_sum)", "sum(closed_count)"),
    Metric.won_value: ("sum(won_value)", None),
    Metric.new_pipeline_value: ("sum(new_pipeline_value)", None),
    Metric.new_deal_count: ("sum(new_deal_count)", None),
}


@router.get("/kpis/trend", response_model=TrendResponse)
def kpi_trend(metric: Metric, grain: Grain = Grain.month, region: Region | None = None):
    num, den = METRIC_SQL[metric]
    query = sql.SQL("""
        SELECT {grain} AS period, {num} AS numerator, {den} AS denominator
        FROM v_monthly_region_kpis
        WHERE (%(region)s::text IS NULL OR region = %(region)s::text)
        GROUP BY 1 ORDER BY 1
    """).format(grain=sql.Identifier(grain.value), num=sql.SQL(num),
                den=sql.SQL(den) if den else sql.NULL)
    rows = fetch_all(query, {"region": region.value if region else None})
    points = []
    for r in rows:
        n, d = float(r["numerator"]), r["denominator"]
        value = n if d is None else (n / float(d) if d else None)
        points.append(TrendPoint(period=r["period"], value=value, numerator=n,
                                 denominator=None if d is None else float(d)))
    return TrendResponse(metric=metric, grain=grain, region=region, points=points)
