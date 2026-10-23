import os
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from api import db
from api.models import HealthResponse
from api.routers import kpis


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.open_pool()
    yield
    db.close_pool()


app = FastAPI(title="RevenuePulse API", lifespan=lifespan)

# Comma-separated list, e.g. "https://revenuepulse.vercel.app,http://localhost:3000"
allowed_origins = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# Database missing, asleep or unreachable -> 503 "try again", never a 500 crash.
@app.exception_handler(db.DatabaseUnavailable)
@app.exception_handler(PoolTimeout)
@app.exception_handler(psycopg.OperationalError)
async def database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "database unavailable"})


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    # No database call: this must answer even when the DB is down or asleep.
    return HealthResponse(status="ok")


app.include_router(kpis.router)
