from fastapi import APIRouter
from pydantic import BaseModel

from ape.core.deps import CurrentPrincipal, TenantConn
from ape.repos import context

router = APIRouter(prefix="/v1", tags=["session"])


class Me(BaseModel):
    user_id: str
    org_id: str
    role: str
    # What PostgreSQL sees as the tenant for this request's transaction.
    db_org_id: str | None


@router.get("/me")
async def me(principal: CurrentPrincipal, tc: TenantConn) -> Me:
    return Me(
        user_id=principal.user_id,
        org_id=principal.org_id,
        role=principal.role,
        db_org_id=await context.current_org_id(tc),
    )
