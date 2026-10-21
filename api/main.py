import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.models import HealthResponse

app = FastAPI(title="RevenuePulse API")

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


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    # No database call: this must answer even when the DB is down or asleep.
    return HealthResponse(status="ok")
