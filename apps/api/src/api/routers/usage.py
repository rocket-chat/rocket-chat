"""Token usage, spend and the organization budget cap."""

import logging
import math
from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)

from api.auth.access import _check_session_access, _get_request_identity, _require_admin
from api.db.store import TurnTraceStore
from api.services.audit import (
    AuditService,
)
from api.services.budgets import BudgetService
from api.services.session_usage import SessionUsage, TurnWindow, summarize_session_usage
from api.services.settings import (
    effective_settings,
    load_org_config,
    save_org_config,
)
from api.services.usage import UsageLedger, month_start

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Sessions & Telemetry"])


def _usage_ledger(request: Request) -> UsageLedger:
    ledger: UsageLedger | None = getattr(request.app.state, "usage_ledger", None)
    return (
        ledger
        if ledger is not None
        else UsageLedger(getattr(request.app.state, "db_manager", None))
    )


@router.get("/settings/org/usage")
async def get_org_usage_telemetry(request: Request) -> dict[str, Any]:
    """This month's token and dollar spend for the organization, from the persisted ledger."""
    org_id, _, _ = _get_request_identity(request)
    db_mgr = getattr(request.app.state, "db_manager", None)
    since = month_start()
    summary = await _usage_ledger(request).summary(org_id, since=since)
    cap = float(
        (await effective_settings(db_mgr, org_id, "budgets")).get("monthly_budget_usd") or 0
    )
    funded = await _usage_ledger(request).spend_usd(org_id, since=since)
    return {
        "tenant_org_id": org_id,
        "period_start": since,
        **summary,
        "monthly_budget_cap_usd": cap,
        "budget_used_percent": round(funded / cap * 100, 2) if cap > 0 else 0.0,
    }


@router.put("/admin/budget")
async def update_org_budget_cap(
    body: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """Set the organization's monthly spend cap (shorthand for the ``budgets`` settings domain)."""
    org_id, admin_user, _ = _require_admin(request)
    try:
        raw_cap = body.get("monthly_budget_cap_usd", body.get("budget_cap_usd"))
        cap_usd = float(raw_cap if raw_cap is not None else "nan")
    except (TypeError, ValueError) as err:
        raise HTTPException(
            status_code=422, detail="monthly_budget_cap_usd must be a number"
        ) from err
    if not math.isfinite(cap_usd) or cap_usd < 0:
        raise HTTPException(
            status_code=422, detail="The budget cap must be a non-negative number (0 = none)"
        )

    db_mgr = getattr(request.app.state, "db_manager", None)
    current, locked = await load_org_config(db_mgr, org_id, "budgets")
    await save_org_config(
        db_mgr, org_id, "budgets", {**current, "monthly_budget_usd": cap_usd}, list(locked)
    )
    budget_service: BudgetService | None = getattr(request.app.state, "budget_service", None)
    if budget_service is not None:
        budget_service.forget(org_id)

    await AuditService(db_mgr).record(
        "settings_change",
        org_id=org_id,
        actor_user_id=admin_user,
        result_summary=f"set monthly budget cap to {cap_usd} USD",
        metadata={"monthly_budget_cap_usd": cap_usd},
    )
    return {"status": "ok", "tenant_org_id": org_id, "monthly_budget_cap_usd": cap_usd}


@router.get("/settings/user/usage")
async def get_user_usage_telemetry(request: Request) -> dict[str, Any]:
    """This month's personal token and cost usage, from the persisted ledger."""
    org_id, user_id, _ = _get_request_identity(request)
    since = month_start()
    summary = await _usage_ledger(request).summary(org_id, since=since, user_id=user_id)
    return {
        "tenant_user_id": user_id,
        "period_start": since,
        **summary,
        "recent_turns_count": summary["calls"],
    }


@router.get("/sessions/{session_id}/usage", response_model=SessionUsage)
async def get_session_usage(session_id: str, request: Request) -> SessionUsage:
    """Tokens and cost of one session: in total, per turn and per model.

    From the usage ledger, which records each model call as it happens, so a turn that is still
    running is included. Per-turn figures need the PostgreSQL store (they come from the turns'
    start times); without it only the totals are returned.
    """
    org_id, user_id, roles = _get_request_identity(request)
    store = request.app.state.session_store
    record = await store.get_session(session_id)
    # Row-level security already hides another organization's session from the real store; checked
    # here too so the answer never depends on that alone, and reads as "no such session".
    if record is None or record.tenant_org_id != org_id:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, user_id, roles)

    windows: list[TurnWindow] = []
    db_mgr = getattr(request.app.state, "db_manager", None)
    if db_mgr is not None:
        traces = await TurnTraceStore(db_mgr).list_for_session(session_id, org_id, limit=1000)
        windows = [
            TurnWindow(
                t.turn_index,
                t.started_at,
                t.ended_at,
                t.status,
                (t.tokens or {}).get("metrics") if isinstance(t.tokens, dict) else None,
            )
            for t in traces
        ]
    events = await _usage_ledger(request).session_events(org_id, session_id)
    return summarize_session_usage(session_id, events, windows)
