"""Repository setups: how the organization prepares folders of its repositories (admins only)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from api.auth.access import _require_admin
from api.services.repository_setups import RepositorySetup, RepositorySetupStore

router = APIRouter(prefix="/v1", tags=["repository-setups"])


def _store(request: Request) -> RepositorySetupStore:
    return RepositorySetupStore(getattr(request.app.state, "db_manager", None))


@router.get("/repository-setups", response_model=list[RepositorySetup])
async def list_repository_setups(request: Request) -> list[RepositorySetup]:
    """The organization's repository setups, by repository and folder."""
    org_id, _, _ = _require_admin(request)
    return await _store(request).list(org_id)


@router.post("/repository-setups", response_model=RepositorySetup)
async def save_repository_setup(setup: RepositorySetup, request: Request) -> RepositorySetup:
    """Create a repository setup, or update one (with its ``id``)."""
    org_id, _, _ = _require_admin(request)
    if setup.id and not any(s.id == setup.id for s in await _store(request).list(org_id)):
        raise HTTPException(status_code=404, detail="Repository setup not found")
    return await _store(request).save(org_id, setup)


@router.delete("/repository-setups/{setup_id}")
async def delete_repository_setup(setup_id: str, request: Request) -> dict[str, str]:
    """Delete a repository setup; workspaces it already prepared stay as they are."""
    org_id, _, _ = _require_admin(request)
    if not await _store(request).delete(org_id, setup_id):
        raise HTTPException(status_code=404, detail="Repository setup not found")
    return {"status": "deleted"}
