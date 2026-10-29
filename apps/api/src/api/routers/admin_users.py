"""Organization user administration (list members, change roles).

Admin/owner-only, scoped to the caller's organization. Role changes are written to the
``users`` table; because a session's roles are baked into its token at login, a change
takes effect when the affected user **reloads / re-authenticates** — the API says so
explicitly in its response so the cockpit can prompt the admin.
"""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from api.auth.access import _require_admin as require_admin
from api.auth.local import LocalAccounts, generate_temporary_password
from api.auth.roles import invalidate_role
from api.db.models import UserModel
from api.services.audit import AuditService

router = APIRouter(prefix="/v1", tags=["Admin · Users"])

# Roles a user may hold. `owner` and `admin` both carry administrative privileges.
# ``guest`` is internal (guest tokens, Slack guests), never a role an administrator assigns.
ALLOWED_USER_ROLES: tuple[str, ...] = ("owner", "admin", "developer", "readonly")
_ADMIN_ROLES = ("owner", "admin")


class UserSummary(BaseModel):
    id: str
    email: str
    name: str
    role: str
    created_at: int


class UserPage(BaseModel):
    users: list[UserSummary]
    total: int


class UpdateUserRoleRequest(BaseModel):
    role: str = Field(..., description="One of owner | admin | developer | readonly")


class UpdateUserRoleResponse(BaseModel):
    user: UserSummary
    # The token carries the roles, so the change applies when the user next authenticates.
    note: str = "The user must reload / sign in again for the new role to take effect."


def _require_admin(request: Request) -> tuple[str, str, list[str]]:
    return require_admin(request)


def _to_summary(user: UserModel) -> UserSummary:
    return UserSummary(
        id=user.id, email=user.email, name=user.name, role=user.role, created_at=user.created_at
    )


@router.get("/admin/users", response_model=UserPage)
async def list_org_users(
    request: Request,
    q: str | None = Query(default=None, description="Filter by email or name (substring)."),
    role: str | None = Query(default=None, description="Filter by exact role."),
    limit: int = Query(default=100, ge=1, le=500),
) -> UserPage:
    """List the organization's members (admin/owner only)."""
    org_id, _, _ = _require_admin(request)
    db_mgr = getattr(request.app.state, "db_manager", None)
    if db_mgr is None:
        raise HTTPException(status_code=503, detail="Database is not available")

    async with db_mgr.session(tenant_org_id=org_id) as session:
        # ``users`` has no row-level security: every read names the organization.
        conditions = [UserModel.org_id == org_id]
        if q:
            like = f"%{q.strip()}%"
            conditions.append((UserModel.email.ilike(like)) | (UserModel.name.ilike(like)))
        if role:
            conditions.append(UserModel.role == role)
        stmt = (
            select(UserModel)
            .where(*conditions)
            .order_by(UserModel.created_at.asc(), UserModel.id.asc())
            .limit(limit)
        )
        users = list((await session.execute(stmt)).scalars().all())
        total = int(
            (
                await session.execute(
                    select(func.count()).select_from(UserModel).where(*conditions)
                )
            ).scalar_one()
        )
    return UserPage(users=[_to_summary(u) for u in users], total=total)


@router.patch("/admin/users/{user_id}", response_model=UpdateUserRoleResponse)
async def update_user_role(
    user_id: str,
    body: UpdateUserRoleRequest,
    request: Request,
) -> UpdateUserRoleResponse:
    """Change a member's role (admin/owner only). Takes effect on their next login."""
    org_id, admin_id, _ = _require_admin(request)
    new_role = body.role.strip().lower()
    if new_role not in ALLOWED_USER_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid role '{body.role}'. Allowed: {', '.join(ALLOWED_USER_ROLES)}.",
        )
    db_mgr = getattr(request.app.state, "db_manager", None)
    if db_mgr is None:
        raise HTTPException(status_code=503, detail="Database is not available")

    async with db_mgr.session(tenant_org_id=org_id) as session:
        target = await session.get(UserModel, user_id)
        if target is None or target.org_id != org_id:
            raise HTTPException(status_code=404, detail="User not found in this organization")

        # Never strand the organization without an administrator.
        if target.role in _ADMIN_ROLES and new_role not in _ADMIN_ROLES:
            admin_count = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(UserModel)
                        .where(UserModel.org_id == org_id, UserModel.role.in_(_ADMIN_ROLES))
                    )
                ).scalar_one()
            )
            if admin_count <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="Cannot remove the last administrator of the organization.",
                )

        target.role = new_role
        await session.flush()
        invalidate_role(user_id)
        summary = _to_summary(target)

    await AuditService(db_mgr).record(
        "settings_change",
        org_id=org_id,
        actor_user_id=admin_id,
        result_summary=f"changed role of {summary.email} to '{new_role}'",
        metadata={"target_user_id": user_id, "role": new_role},
    )
    return UpdateUserRoleResponse(user=summary)


class CreateLocalUserRequest(BaseModel):
    email: str = Field(..., max_length=255)
    name: str = Field(..., min_length=1, max_length=255)
    role: str = Field(default="developer")


class TemporaryCredential(BaseModel):
    user: UserSummary
    # Shown once, never stored in clear: hand it over out of band; the member must change it.
    temporary_password: str


def _local_accounts(request: Request) -> LocalAccounts:
    accounts = LocalAccounts(getattr(request.app.state, "db_manager", None))
    if not accounts.available:
        raise HTTPException(
            status_code=409,
            detail="Local accounts are not enabled (set LOCAL_AUTH_ENABLED=true; needs PostgreSQL).",
        )
    return accounts


@router.post("/admin/users", response_model=TemporaryCredential)
async def create_local_user(body: CreateLocalUserRequest, request: Request) -> TemporaryCredential:
    """Create a password-login member with a one-time temporary password (admin/owner only)."""
    org_id, admin_id, _ = _require_admin(request)
    accounts = _local_accounts(request)
    role = body.role.strip().lower()
    if role not in ALLOWED_USER_ROLES:
        raise HTTPException(
            status_code=422, detail=f"Invalid role. Allowed: {', '.join(ALLOWED_USER_ROLES)}."
        )
    email = body.email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(status_code=422, detail="Enter a valid e-mail address.")
    password = generate_temporary_password()
    user_id = f"usr_{uuid.uuid4().hex[:12]}"
    try:
        await accounts.create_user(
            user_id=user_id,
            org_id=org_id,
            email=email,
            name=body.name.strip(),
            role=role,
            password=password,
            must_change=True,
        )
    except ValueError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    await AuditService(getattr(request.app.state, "db_manager", None)).record(
        "credential_change",
        org_id=org_id,
        actor_user_id=admin_id,
        result_summary=f"created local account {email}",
        metadata={"target_user_id": user_id, "role": role},
    )
    return TemporaryCredential(
        user=UserSummary(
            id=user_id, email=email, name=body.name.strip(), role=role, created_at=int(time.time())
        ),
        temporary_password=password,
    )


@router.post("/admin/users/{user_id}/reset-password", response_model=TemporaryCredential)
async def reset_local_password(user_id: str, request: Request) -> TemporaryCredential:
    """Issue a new one-time password for a member and unlock their account (admin/owner only)."""
    org_id, admin_id, _ = _require_admin(request)
    accounts = _local_accounts(request)
    password = generate_temporary_password()
    if not await accounts.reset_password(org_id, user_id, password):
        raise HTTPException(status_code=404, detail="User not found in this organization")
    db_mgr = request.app.state.db_manager
    async with db_mgr.session(tenant_org_id=org_id) as session:
        target = await session.get(UserModel, user_id)
        summary = _to_summary(target) if target is not None else None
    if summary is None:
        raise HTTPException(status_code=404, detail="User not found in this organization")
    await AuditService(db_mgr).record(
        "credential_change",
        org_id=org_id,
        actor_user_id=admin_id,
        result_summary=f"reset the password of {summary.email}",
        metadata={"target_user_id": user_id},
    )
    return TemporaryCredential(user=summary, temporary_password=password)
