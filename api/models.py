from datetime import date
from enum import Enum

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


class Region(str, Enum):
    north = "North"
    south = "South"
    east = "East"
    west = "West"


class Metric(str, Enum):
    win_rate = "win_rate"
    avg_deal_size = "avg_deal_size"
    avg_sales_cycle_days = "avg_sales_cycle_days"
    won_value = "won_value"
    new_pipeline_value = "new_pipeline_value"
    new_deal_count = "new_deal_count"


class Grain(str, Enum):
    month = "month"
    quarter = "quarter"


class Period(BaseModel):
    """The whole calendar months actually covered (start/end are snapped to months)."""
    start_month: date | None
    end_month: date | None


class KpiSummary(BaseModel):
    period: Period
    region: Region | None
    win_rate: float | None
    won_count: int
    lost_count: int
    avg_deal_size: float | None
    won_value: float
    avg_sales_cycle_days: float | None
    new_deal_count: int
    new_pipeline_value: float
    pipeline_velocity_per_day: float | None  # as of the latest data date, not the period


class TrendPoint(BaseModel):
    period: date
    value: float | None
    numerator: float
    denominator: float | None  # null for plain totals (won_value, new_pipeline_value, ...)


class TrendResponse(BaseModel):
    metric: Metric
    grain: Grain
    region: Region | None
    points: list[TrendPoint]
