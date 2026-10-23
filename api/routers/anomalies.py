from fastapi import APIRouter, Query

from api.db import fetch_all
from api.models import Anomaly

router = APIRouter(tags=["anomalies"])


@router.get("/anomalies", response_model=list[Anomaly])
def list_anomalies(limit: int = Query(20, ge=1, le=100)):
    return fetch_all("""
        SELECT id, detected_on, metric_date, metric, segment, value, baseline, score
        FROM anomalies
        ORDER BY metric_date DESC, id DESC
        LIMIT %(limit)s
    """, {"limit": limit})
