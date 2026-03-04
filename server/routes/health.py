"""
health.py — Health check endpoint.
"""

from fastapi import APIRouter, Depends

from server.models.schemas import HealthResponse
from server import deps

router = APIRouter(tags=["health"])


@router.get("/api/health", response_model=HealthResponse)
def health_check(
    db=Depends(deps.get_db),
    trino=Depends(deps.get_trino),
):
    trino_status = "disabled"
    if trino:
        try:
            result = trino.mcp.health_check()
            trino_status = "ok" if result.get("trino") == "ok" else "unreachable"
        except Exception:
            trino_status = "unreachable"

    return HealthResponse(app="ok", trino=trino_status, database="ok")
