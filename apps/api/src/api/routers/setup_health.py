"""Setup health: what the organization is missing, for administrators."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.auth.access import _require_admin
from api.bootstrap.runtime import default_model
from api.services.setup_health import setup_health

router = APIRouter(prefix="/v1", tags=["Administration"])


class HealthCheckView(BaseModel):
    id: str
    title: str
    status: str = Field(..., description="ok, warning, error or unknown")
    detail: str
    fix_label: str = Field("", description="What to do, when something is missing")
    fix_href: str = Field("", description="Where to do it (a settings page or the docs)")


@router.get("/admin/setup-health", response_model=list[HealthCheckView])
async def get_setup_health(request: Request) -> list[HealthCheckView]:
    """A provider key and a default model it can call, embeddings, web search, the GitHub App,
    and the sandbox images (administrators)."""
    org_id, _, _ = _require_admin(request)
    checks = await setup_health(
        getattr(request.app.state, "db_manager", None),
        org_id,
        getattr(request.app.state, "sandbox_driver", None),
        default_model(),
    )
    return [HealthCheckView(**asdict(check)) for check in checks]
