"""Token usage and spend, from the usage ledger."""

import logging
from collections.abc import Sequence
from typing import Any

from agent_core.context import ContextManager, threshold
from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)

from api.auth.access import _check_session_access, _get_request_identity
from api.bootstrap.runtime import default_model
from api.db.store import TurnTraceStore
from api.services.session_usage import (
    ActiveContextModel,
    SessionUsage,
    TurnWindow,
    summarize_session_usage,
)
from api.services.settings import load_org_config
from api.services.usage import UsageLedger, month_start
from specifications.interfaces.session import SessionRecord

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
    """This month's token and dollar spend for the organization, from the persisted ledger,
    in total and grouped by agent, credential and member."""
    org_id, _, _ = _get_request_identity(request)
    since = month_start()
    ledger = _usage_ledger(request)
    summary = await ledger.summary(org_id, since=since)
    return {
        "tenant_org_id": org_id,
        "period_start": since,
        **summary,
        "by_agent": await ledger.grouped(org_id, since=since, by="agent"),
        "by_credential": await ledger.grouped(org_id, since=since, by="credential"),
        "by_member": await ledger.grouped(org_id, since=since, by="member"),
    }


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


def _extract_tokens_from_traces_or_events(
    traces: Sequence[Any] | None,
    events: Sequence[Any] | None,
) -> int:
    if traces:
        for trace in reversed(traces):
            calls = (trace.tokens or {}).get("calls", []) if isinstance(trace.tokens, dict) else []
            for call in reversed(calls):
                if call.get("role") == "react":
                    prompt_toks = call.get("prompt_tokens")
                    if isinstance(prompt_toks, int) and prompt_toks > 0:
                        return prompt_toks
            if calls:
                prompt_toks = calls[-1].get("prompt_tokens")
                if isinstance(prompt_toks, int) and prompt_toks > 0:
                    return prompt_toks
    if events:
        for ev in reversed(events):
            prompt_toks = ev.get("prompt_tokens")
            if isinstance(prompt_toks, int) and prompt_toks > 0:
                return prompt_toks
    return 0


async def _resolve_session_active_context(
    record: SessionRecord,
    org_id: str,
    db_mgr: Any,
    request: Request,
    traces: Sequence[Any] | None = None,
    events: Sequence[Any] | None = None,
) -> ActiveContextModel:
    model_id = getattr(record, "selected_model", None) or default_model()
    ctx_mgr = getattr(request.app.state, "context_manager", None) or ContextManager()
    models_cfg: dict[str, Any] = {}
    if db_mgr is not None:
        models_cfg = (await load_org_config(db_mgr, org_id, "models"))[0]
    raw_overrides = models_cfg.get("context_windows")
    overrides = tuple(raw_overrides.items()) if isinstance(raw_overrides, dict) else ()
    limit_info = ctx_mgr.resolve_context_limit(model_id, overrides)
    target_tokens = int(models_cfg.get("context_target_tokens") or 0)
    effective_limit = (
        min(limit_info.tokens, target_tokens) if target_tokens > 0 else limit_info.tokens
    )
    reserve_tokens = min(16_384, effective_limit // 4)
    prune = int(models_cfg.get("context_prune_percent") or 0) / 100 or ctx_mgr.tier2_threshold_ratio
    compact = (
        int(models_cfg.get("context_compact_percent") or 0) / 100 or ctx_mgr.tier3_threshold_ratio
    )

    if isinstance(record.metadata, dict) and "active_context" in record.metadata:
        try:
            saved = ActiveContextModel.model_validate(record.metadata["active_context"])
            if saved.tokens <= effective_limit or not (traces or events):
                return saved
        except Exception:
            pass

    counted = _extract_tokens_from_traces_or_events(traces, events)
    return ActiveContextModel(
        tokens=counted,
        limit=effective_limit,
        model_limit=limit_info.tokens,
        target_tokens=target_tokens,
        source=limit_info.source,
        tier2_tokens=threshold(effective_limit, prune, reserve_tokens),
        tier3_tokens=threshold(effective_limit, compact, reserve_tokens),
    )


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
    traces: list[Any] = []
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
    active_ctx = await _resolve_session_active_context(
        record, org_id, db_mgr, request, traces=traces, events=events
    )
    return summarize_session_usage(session_id, events, windows, active_context=active_ctx)
