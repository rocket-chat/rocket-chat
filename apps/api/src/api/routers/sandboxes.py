"""The sandbox catalogue: what members can pick, and what administrators offer."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth.access import _get_request_identity, _require_admin
from api.services.audit import AuditService
from api.services.sandbox_catalogue import (
    CAPABILITIES,
    CatalogueError,
    SandboxCatalogue,
    SandboxEntry,
)

router = APIRouter(prefix="/v1", tags=["Sandboxes"])


class SandboxView(BaseModel):
    id: str
    name: str
    image: str
    description: str
    capabilities: list[str]
    is_default: bool


class SandboxCatalogueView(BaseModel):
    sandboxes: list[SandboxView]
    capabilities: dict[str, str] = Field(description="Every capability tag and what it needs")


class SandboxBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    image: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=500)
    capabilities: list[str] = Field(default_factory=list)
    is_default: bool = False


class SandboxPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    image: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=500)
    capabilities: list[str] | None = None
    is_default: bool | None = None


def catalogue_for(request: Request) -> SandboxCatalogue:
    return SandboxCatalogue(getattr(request.app.state, "db_manager", None))


def _view(entry: SandboxEntry) -> SandboxView:
    return SandboxView(**{k: getattr(entry, k) for k in SandboxView.model_fields})


async def _audit(request: Request, org_id: str, user_id: str, action: str, entry_id: str) -> None:
    await AuditService(getattr(request.app.state, "db_manager", None)).record(
        "settings_change",
        org_id=org_id,
        actor_user_id=user_id,
        result_summary=f"sandbox catalogue: {action}",
        metadata={"domain": "sandbox_catalogue", "sandbox_id": entry_id, "action": action},
    )


@router.get("/sandboxes", response_model=SandboxCatalogueView)
async def list_sandboxes(request: Request) -> SandboxCatalogueView:
    org_id, _, _ = _get_request_identity(request)
    entries = await catalogue_for(request).entries(org_id)
    return SandboxCatalogueView(sandboxes=[_view(e) for e in entries], capabilities=CAPABILITIES)


@router.post("/admin/sandboxes", response_model=SandboxView)
async def create_sandbox(body: SandboxBody, request: Request) -> SandboxView:
    org_id, user_id, _ = _require_admin(request)
    try:
        entry = await catalogue_for(request).create(org_id, **body.model_dump())
    except CatalogueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    await _audit(request, org_id, user_id, "created", entry.id)
    return _view(entry)


@router.patch("/admin/sandboxes/{sandbox_id}", response_model=SandboxView)
async def update_sandbox(sandbox_id: str, body: SandboxPatch, request: Request) -> SandboxView:
    org_id, user_id, _ = _require_admin(request)
    try:
        entry = await catalogue_for(request).update(
            org_id, sandbox_id, **body.model_dump(exclude_unset=True)
        )
    except CatalogueError as err:
        status = 404 if str(err) == "No such sandbox." else 422
        raise HTTPException(status_code=status, detail=str(err)) from err
    await _audit(request, org_id, user_id, "updated", sandbox_id)
    return _view(entry)


@router.delete("/admin/sandboxes/{sandbox_id}")
async def delete_sandbox(sandbox_id: str, request: Request) -> dict[str, str]:
    org_id, user_id, _ = _require_admin(request)
    try:
        await catalogue_for(request).delete(org_id, sandbox_id)
    except CatalogueError as err:
        status = 404 if str(err) == "No such sandbox." else 409
        raise HTTPException(status_code=status, detail=str(err)) from err
    await _audit(request, org_id, user_id, "deleted", sandbox_id)
    return {"status": "deleted"}
