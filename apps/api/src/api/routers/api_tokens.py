"""Personal API tokens for scripts and other machine clients."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth.access import _get_request_identity, _require_admin
from api.auth.api_tokens import (
    DEFAULT_LIFETIME_DAYS,
    MAX_LIFETIME_DAYS,
    MAX_TOKENS_PER_MEMBER,
    ApiTokens,
    StoredToken,
    TooManyTokens,
)
from api.auth.identity import resolve_identity
from api.services.audit import AuditService

router = APIRouter(prefix="/v1", tags=["API tokens"])


class CreateApiTokenRequest(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=100, description="What the token is for, to recognise it"
    )
    expires_in_days: int = Field(
        default=DEFAULT_LIFETIME_DAYS,
        ge=1,
        le=MAX_LIFETIME_DAYS,
        description="Days until the token stops working",
    )


class ApiTokenView(BaseModel):
    id: str = Field(..., description="Token id (the secret is never shown again)")
    name: str
    user_id: str = Field(..., description="The member the token acts as")
    guest: bool = Field(default=False, description="A guest token, acting as its own guest member")
    created_at: int = Field(..., description="Unix seconds")
    expires_at: int = Field(..., description="Unix seconds")
    last_used_at: int | None = Field(
        default=None, description="Unix seconds, to the minute; null if never used"
    )


class CreatedApiToken(ApiTokenView):
    token: str = Field(
        ..., description="The token to send as `Authorization: Bearer <token>`; shown only once"
    )


class RevokedApiToken(BaseModel):
    revoked: bool


def _tokens(request: Request) -> ApiTokens:
    tokens = ApiTokens(getattr(request.app.state, "db_manager", None))
    if not tokens.available:
        raise HTTPException(status_code=503, detail="API tokens need the PostgreSQL store")
    return tokens


def _view(token: StoredToken) -> ApiTokenView:
    return ApiTokenView(
        id=token.id,
        name=token.name,
        user_id=token.user_id,
        guest=token.guest,
        created_at=token.created_at,
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
    )


async def _audit(request: Request, org_id: str, actor: str, summary: str, token_id: str) -> None:
    await AuditService(getattr(request.app.state, "db_manager", None)).record(
        "credential_change",
        org_id=org_id,
        actor_user_id=actor,
        result_summary=summary,
        metadata={"api_token_id": token_id},
    )


@router.get("/user/api-tokens", response_model=list[ApiTokenView])
async def list_my_api_tokens(request: Request) -> list[ApiTokenView]:
    """The signed-in member's API tokens (never their secrets)."""
    org_id, user_id, _ = _get_request_identity(request)
    return [_view(t) for t in await _tokens(request).list(org_id, user_id)]


@router.post("/user/api-tokens", response_model=CreatedApiToken, status_code=201)
async def create_my_api_token(body: CreateApiTokenRequest, request: Request) -> CreatedApiToken:
    """Create an API token acting as the signed-in member, with the role they hold when it is
    used. The token is in this response only. An API token cannot create another one."""
    org_id, user_id, _ = _get_request_identity(request)
    caller = resolve_identity(request)
    if caller is not None and caller.source == "api_token":
        raise HTTPException(
            status_code=403, detail="Sign in to create an API token; a token cannot create one"
        )
    try:
        stored, token = await _tokens(request).create(
            org_id, user_id, body.name, body.expires_in_days
        )
    except TooManyTokens as err:
        raise HTTPException(
            status_code=409,
            detail=f"You already have {MAX_TOKENS_PER_MEMBER} API tokens; revoke one first",
        ) from err
    await _audit(request, org_id, user_id, f"created API token {body.name!r}", stored.id)
    return CreatedApiToken(**_view(stored).model_dump(), token=token)


@router.delete("/user/api-tokens/{token_id}", response_model=RevokedApiToken)
async def revoke_my_api_token(token_id: str, request: Request) -> RevokedApiToken:
    """Revoke one of the signed-in member's API tokens; it stops working at once."""
    org_id, user_id, _ = _get_request_identity(request)
    revoked = await _tokens(request).revoke(org_id, token_id, user_id)
    if revoked is None:
        raise HTTPException(status_code=404, detail="API token not found")
    await _audit(request, org_id, user_id, f"revoked API token {revoked.name!r}", token_id)
    return RevokedApiToken(revoked=True)


@router.get("/admin/api-tokens", response_model=list[ApiTokenView])
async def list_org_api_tokens(request: Request) -> list[ApiTokenView]:
    """Every member's API tokens in the organization (administrators)."""
    org_id, _, _ = _require_admin(request)
    return [_view(t) for t in await _tokens(request).list(org_id, None)]


@router.post("/admin/api-tokens", response_model=CreatedApiToken, status_code=201)
async def create_guest_api_token(body: CreateApiTokenRequest, request: Request) -> CreatedApiToken:
    """Create a guest token (administrators), for a client without a Rocket account, as a Slack
    user who never linked theirs. It acts as its own guest member, which owns the sessions it
    starts, and can only hold conversations. The token is in this response only."""
    org_id, admin_user, _ = _require_admin(request)
    stored, token = await _tokens(request).create_guest(
        org_id, body.name, body.expires_in_days, admin_user
    )
    await _audit(request, org_id, admin_user, f"created guest API token {body.name!r}", stored.id)
    return CreatedApiToken(**_view(stored).model_dump(), token=token)


@router.delete("/admin/api-tokens/{token_id}", response_model=RevokedApiToken)
async def revoke_org_api_token(token_id: str, request: Request) -> RevokedApiToken:
    """Revoke any member's API token in the organization (administrators)."""
    org_id, admin_user, _ = _require_admin(request)
    revoked = await _tokens(request).revoke(org_id, token_id, None)
    if revoked is None:
        raise HTTPException(status_code=404, detail="API token not found")
    await _audit(
        request,
        org_id,
        admin_user,
        f"revoked API token {revoked.name!r} of {revoked.user_id}",
        token_id,
    )
    return RevokedApiToken(revoked=True)
