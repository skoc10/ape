"""Workspace settings: white-label branding (task 4.1) and the GitHub connection (4.4)."""

import re
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from ape.core.db import Database
from ape.core.deps import CurrentPrincipal
from ape.core.tenancy import Principal
from ape.repos import org_settings
from ape.storage import ObjectStore

router = APIRouter(prefix="/v1/settings", tags=["settings"])
log = structlog.get_logger(__name__)

MANAGER_ROLES = {"owner", "admin"}
MAX_LOGO_BYTES = 512 * 1024

# Raster formats only. SVG is refused on purpose: it can carry scripts and external
# references, and the logo ends up inside every customer's report.
_LOGO_SIGNATURES = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
}


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _store(request: Request) -> ObjectStore:
    store: ObjectStore = request.app.state.object_store
    return store


Db = Annotated[Database, Depends(_db)]
Store = Annotated[ObjectStore, Depends(_store)]


def _require_manager(principal: Principal) -> None:
    if principal.role not in MANAGER_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only owners and admins can change settings")


class Branding(BaseModel):
    company_name: str | None = None
    primary_color: str | None = None
    has_logo: bool = False


class SettingsOut(BaseModel):
    branding: Branding
    github_installation_id: int | None
    github_repo: str | None
    manifest_path: str | None
    can_manage: bool


class BrandingIn(BaseModel):
    company_name: str | None = Field(default=None, max_length=80)
    primary_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _out(row: dict[str, Any], principal: Principal) -> SettingsOut:
    branding = row["branding"] or {}
    return SettingsOut(
        branding=Branding(
            company_name=branding.get("company_name"),
            primary_color=branding.get("primary_color"),
            has_logo=bool(branding.get("logo_key")),
        ),
        github_installation_id=row["github_installation_id"],
        github_repo=row["github_repo"],
        manifest_path=row["manifest_path"],
        can_manage=principal.role in MANAGER_ROLES,
    )


@router.get("")
async def get_settings(principal: CurrentPrincipal, db: Db) -> SettingsOut:
    async with db.tenant(principal.org_id) as tc:
        row = await org_settings.get(tc)
    return _out(row, principal)


@router.put("/branding")
async def update_branding(body: BrandingIn, principal: CurrentPrincipal, db: Db) -> SettingsOut:
    _require_manager(principal)
    name = body.company_name.strip() if body.company_name else None
    if name and _CONTROL.search(name):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "company name contains control characters"
        )
    patch = {
        "company_name": name or None,
        "primary_color": body.primary_color.lower() if body.primary_color else None,
    }
    async with db.tenant(principal.org_id) as tc:
        row = await org_settings.merge_branding(tc, patch)
    log.info("branding_updated")
    return _out(row, principal)


@router.put("/logo")
async def upload_logo(
    request: Request, principal: CurrentPrincipal, db: Db, store: Store
) -> SettingsOut:
    _require_manager(principal)
    declared = int(request.headers.get("content-length") or 0)
    if declared > MAX_LOGO_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "logo is larger than 512 KiB")
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > MAX_LOGO_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "logo is larger than 512 KiB")
    # The type is decided by the bytes, not by what the client claims.
    content_type = next(
        (t for t, magic in _LOGO_SIGNATURES.items() if bytes(data).startswith(magic)), None
    )
    if content_type is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "logo must be a PNG or JPEG image"
        )
    key = await store.put_logo(principal.org_id, bytes(data), content_type)
    async with db.tenant(principal.org_id) as tc:
        row = await org_settings.merge_branding(
            tc, {"logo_key": key, "logo_content_type": content_type}
        )
    log.info("logo_uploaded", bytes=len(data), content_type=content_type)
    return _out(row, principal)


@router.delete("/logo")
async def delete_logo(principal: CurrentPrincipal, db: Db, store: Store) -> SettingsOut:
    _require_manager(principal)
    await store.delete_logo(principal.org_id)
    async with db.tenant(principal.org_id) as tc:
        row = await org_settings.merge_branding(tc, {"logo_key": None, "logo_content_type": None})
    return _out(row, principal)


@router.get("/logo")
async def get_logo(principal: CurrentPrincipal, store: Store) -> Response:
    found = await store.get_logo(principal.org_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no logo")
    data, content_type = found
    if content_type not in _LOGO_SIGNATURES:
        content_type = "application/octet-stream"
    return Response(
        data,
        media_type=content_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
