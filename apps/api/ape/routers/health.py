import structlog
from fastapi import APIRouter, Request, Response, status

from ape.core.db import Database
from ape.repos import context

router = APIRouter(tags=["health"])
log = structlog.get_logger(__name__)


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict[str, str]:
    """Readiness: the database answers."""
    db: Database = request.app.state.db
    try:
        async with db.system() as sc:
            await context.ping(sc)
    except Exception as exc:
        log.warning("readiness_check_failed", error_type=type(exc).__name__)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable"}
    return {"status": "ok"}
