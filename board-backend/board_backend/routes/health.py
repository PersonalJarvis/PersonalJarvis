"""``GET /healthz`` — public, kein Auth.

Liefert nur Liveness/Schema-Status. Wird von Docker-Healthchecks gepollt.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import text

from .. import __version__
from ..schemas import HealthResponse

router = APIRouter(tags=["public"])


@router.get("/healthz", response_model=HealthResponse)
def healthz(request: Request) -> HealthResponse:
    schema_ok = True
    try:
        with request.app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        schema_ok = False
    return HealthResponse(ok=True, version=__version__, schema_ok=schema_ok)
