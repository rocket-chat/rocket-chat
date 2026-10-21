"""The version of the running control plane."""

import os

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.auth.access import _get_request_identity

router = APIRouter(prefix="/v1", tags=["System"])

DEV_VERSION = "dev"


class VersionInfo(BaseModel):
    version: str = Field(
        ...,
        description="The release this server was built from (`0.5.0`), or `dev` for any build "
        "that is not a tagged release",
    )


def running_version() -> str:
    # Only the release workflow sets ROCKET_VERSION: the manifests carry the last release number
    # on every commit after it, so reading them would label unreleased builds as released.
    return os.getenv("ROCKET_VERSION", "").strip().removeprefix("v") or DEV_VERSION


@router.get("/version", response_model=VersionInfo)
async def get_version(request: Request) -> VersionInfo:
    """The running release, or `dev` for a build that is not a tagged release."""
    _get_request_identity(request)
    return VersionInfo(version=running_version())
