"""Classification of Slack messages to agents via structured output, and decision logging."""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from llm_gateway.structured import generate_structured
from pydantic import BaseModel, Field
from slack_assistant.bot import DISMISS, ClassifyRequest, RouteDecision, SlackBot
from sqlalchemy import delete, desc, select

from api.db.models import SlackRouteDecisionModel
from api.db.session import DatabaseSessionManager
from api.services.personas import get_persona
from api.services.systemone import call_systemone_choice, is_systemone_model
from api.turns.routing import _resolve_turn_routing
from specifications.interfaces.llm import (
    ChatMessage,
    LLMGatewayProtocol,
    ModelRequest,
    ModelScope,
    UsageEvent,
    current_model_scope,
)

logger = logging.getLogger(__name__)

# Without PostgreSQL (development, tests) decisions stay in this process, newest kept.
_memory_decisions: dict[str, list[dict[str, Any]]] = {}
MAX_MEMORY_DECISIONS = 500
DECISION_RETENTION_SECONDS = 30 * 24 * 3600


class RouterClassification(BaseModel):
    """The structured decision returned by the model classifier."""

    route: str = Field(description="The chosen route name, or 'dismiss' if allowed")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0")
    reason: str = Field(description="Concise rationale for this classification")


def _build_criteria(bot: SlackBot) -> dict[str, str]:
    router = bot.router
    assert router is not None
    criteria: dict[str, str] = {}
    for r in router.active_routes():
        desc = r.description
        if r.examples:
            desc += f" (Examples: {', '.join(r.examples)})"
        criteria[r.name] = desc
    if router.allow_dismiss:
        criteria[DISMISS] = (
            router.dismiss_description or "Irrelevant message, small talk, or out of scope."
        )
    return criteria


def _build_classification_messages(bot: SlackBot, text: str, excerpt: str) -> list[ChatMessage]:
    router = bot.router
    assert router is not None
    lines = [
        "You are an incoming message classifier for a Slack bot assistant.",
        "Your task is to classify an incoming user message to exactly one route from the list below.",
        "\nAvailable routes:",
    ]
    for r in router.active_routes():
        desc_line = f"- Route '{r.name}': {r.description}"
        if r.examples:
            desc_line += f" (Examples: {', '.join(r.examples)})"
        lines.append(desc_line)
    if router.allow_dismiss:
        dismiss_desc = (
            router.dismiss_description or "Irrelevant message, small talk, or out of scope."
        )
        lines.append(f"- Route '{DISMISS}': {dismiss_desc}")
    allowed = ", ".join(router.choices())
    lines.append(f"\nYou must respond with JSON selecting one of these routes: [{allowed}].")

    user_content = ""
    if excerpt.strip():
        user_content += f"Recent thread context:\n{excerpt.strip()}\n\n"
    user_content += f"Incoming message to classify:\n{text}"

    return [
        ChatMessage(role="system", content="\n".join(lines)),
        ChatMessage(role="user", content=user_content),
    ]


async def _resolve_classifier_model(
    db_mgr: DatabaseSessionManager | None,
    bot: SlackBot,
    default_model: str,
) -> str:
    """The router's model, else its classifier agent's, else the organization's default;
    always checked against the organization's allowed and forbidden models."""
    router = bot.router
    chosen = router.classifier_model if router else ""
    if router and not chosen and router.classifier_agent_id:
        persona = await get_persona(db_mgr, bot.org_id, "", router.classifier_agent_id)
        chosen = persona.model if persona else ""
    _, model, _ = await _resolve_turn_routing(
        db_mgr=db_mgr,
        org_id=bot.org_id,
        user_id="",
        agent_model=chosen or None,
        request_model=None,
        global_default_model=default_model,
    )
    return model


@asynccontextmanager
async def _billed_to(bot: SlackBot, org_id: str) -> AsyncIterator[ModelScope]:
    """Bill the classification to the bot's organization, with its keys and caps: it runs
    before any session or turn exists, so no turn has bound the scope."""
    scope = ModelScope(org_id=org_id, user_id=f"slack-bot:{bot.id}")
    token = current_model_scope.set(scope)
    try:
        yield scope
    finally:
        current_model_scope.reset(token)


async def _resolve_openrouter_key(
    gateway: LLMGatewayProtocol, scope: ModelScope, model_name: str
) -> tuple[str, str]:
    """(key, whose key) for a direct System One call: the organization's or member's own
    OpenRouter key, else the platform's."""
    resolver = getattr(gateway, "credential_resolver", None)
    if resolver is not None:
        resolved = await resolver.resolve(scope, model_name)
        if resolved and resolved.credentials and resolved.credentials.api_key:
            return str(resolved.credentials.api_key), resolved.key_source
    return os.environ.get("OPENROUTER_API_KEY", "").strip(), "platform"


async def _classify_via_systemone(
    api_key: str,
    model_name: str,
    bot: SlackBot,
    text: str,
    thread_excerpt: str,
) -> tuple[RouteDecision, int]:
    criteria = _build_criteria(bot)
    res = await call_systemone_choice(
        model=model_name,
        api_key=api_key,
        message=text,
        thread_excerpt=thread_excerpt,
        question_key="route",
        instructions="Select the appropriate route for this user request.",
        criteria=criteria,
    )
    probs_summary = ", ".join(f"{k}: {v:.0%}" for k, v in res.probabilities.items())
    reason = f"System One decision ({res.confidence:.0%}). Probabilities: [{probs_summary}]"
    decision = RouteDecision(
        route=res.choice,
        confidence=res.confidence,
        reason=reason,
        probabilities=res.probabilities,
    )
    return decision, res.input_tokens


async def _classify_via_structured(
    gateway: LLMGatewayProtocol,
    model_name: str,
    bot: SlackBot,
    text: str,
    thread_excerpt: str,
) -> RouteDecision:
    messages = _build_classification_messages(bot, text, thread_excerpt)
    model_req = ModelRequest(messages=messages, model=model_name, temperature=0.0)
    structured = await generate_structured(gateway, model_req, RouterClassification)
    return RouteDecision(
        route=structured.route.strip(),
        confidence=float(structured.confidence),
        reason=structured.reason.strip(),
    )


async def _dispatch_classification(
    gateway: LLMGatewayProtocol,
    bot: SlackBot,
    org_id: str,
    model_name: str,
    text: str,
    thread_excerpt: str,
) -> RouteDecision:
    async with _billed_to(bot, org_id) as scope:
        if is_systemone_model(model_name):
            decision = await _try_systemone(gateway, scope, bot, model_name, text, thread_excerpt)
            if decision is not None:
                return decision
        return await _classify_via_structured(gateway, model_name, bot, text, thread_excerpt)


async def _try_systemone(
    gateway: LLMGatewayProtocol,
    scope: ModelScope,
    bot: SlackBot,
    model_name: str,
    text: str,
    thread_excerpt: str,
) -> RouteDecision | None:
    """A System One decision, or ``None`` to use structured output instead. The call bypasses
    the gateway, so the spending caps are checked and the usage booked here."""
    api_key, key_source = await _resolve_openrouter_key(gateway, scope, model_name)
    if not api_key:
        return None
    guard = getattr(gateway, "budget_guard", None)
    if guard is not None:
        await guard.check(scope)
    try:
        decision, input_tokens = await _classify_via_systemone(
            api_key, model_name, bot, text, thread_excerpt
        )
    except Exception as err:
        logger.warning("System One failed for %s, using structured output: %s", model_name, err)
        return None
    sink = getattr(gateway, "usage_sink", None)
    if sink is not None:
        try:
            # System One bills input tokens only; LiteLLM has no price for it, so the
            # ledger records the tokens and leaves the cost to the provider's invoice.
            await sink.record(
                UsageEvent(
                    org_id=scope.org_id,
                    user_id=scope.user_id,
                    model=model_name,
                    prompt_tokens=input_tokens,
                    completion_tokens=0,
                    cost_usd=0.0,
                    key_source=key_source,
                )
            )
        except Exception:
            logger.exception("Could not record System One usage for %s", model_name)
    return decision


async def record_route_decision(
    db_mgr: DatabaseSessionManager | None,
    *,
    org_id: str,
    bot_id: str,
    channel_id: str,
    thread_ts: str,
    slack_user_id: str,
    excerpt: str,
    route: str,
    applied: str,
    confidence: float,
    reason: str,
    model: str,
    latency_ms: int,
) -> None:
    now = int(time.time())
    record = {
        "id": uuid.uuid4().hex[:12],
        "org_id": org_id,
        "bot_id": bot_id,
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "slack_user_id": slack_user_id,
        "excerpt": excerpt[:2000],
        "route": route,
        "applied": applied,
        "confidence": confidence,
        "reason": reason[:1000],
        "model": model,
        "latency_ms": latency_ms,
        "created_at": now,
    }
    if db_mgr is None:
        kept = _memory_decisions.setdefault(bot_id, [])
        kept.append(record)
        del kept[:-MAX_MEMORY_DECISIONS]
        return
    async with db_mgr.session(tenant_org_id=org_id) as session:
        session.add(SlackRouteDecisionModel(**record))
        # The log is for tuning prompts, not an archive: a bot keeps its last 30 days.
        await session.execute(
            delete(SlackRouteDecisionModel).where(
                SlackRouteDecisionModel.bot_id == bot_id,
                SlackRouteDecisionModel.created_at < now - DECISION_RETENTION_SECONDS,
            )
        )


async def list_route_decisions(
    db_mgr: DatabaseSessionManager | None,
    org_id: str,
    bot_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if db_mgr is None:
        items = _memory_decisions.get(bot_id, [])
        return sorted(items, key=lambda d: d["created_at"], reverse=True)[:limit]
    async with db_mgr.session(tenant_org_id=org_id) as session:
        stmt = (
            select(SlackRouteDecisionModel)
            .where(
                SlackRouteDecisionModel.org_id == org_id,
                SlackRouteDecisionModel.bot_id == bot_id,
            )
            .order_by(desc(SlackRouteDecisionModel.created_at))
            .limit(limit)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [
            {
                "id": r.id,
                "org_id": r.org_id,
                "bot_id": r.bot_id,
                "channel_id": r.channel_id,
                "thread_ts": r.thread_ts,
                "slack_user_id": r.slack_user_id,
                "excerpt": r.excerpt,
                "route": r.route,
                "applied": r.applied,
                "confidence": r.confidence,
                "reason": r.reason,
                "model": r.model,
                "latency_ms": r.latency_ms,
                "created_at": r.created_at,
            }
            for r in rows
        ]


async def classify_slack_message(
    gateway: LLMGatewayProtocol,
    request: ClassifyRequest,
    db_mgr: DatabaseSessionManager | None,
    default_model: str = "",
) -> RouteDecision | None:
    bot = request.bot
    router = bot.router
    if router is None or not router.active_routes():
        return None
    model_name = await _resolve_classifier_model(db_mgr, bot, default_model)

    t0 = time.perf_counter()
    try:
        decision = await _dispatch_classification(
            gateway, bot, request.org_id, model_name, request.text, request.thread_excerpt
        )
    except Exception as err:
        logger.warning("Classifier failed for bot %s: %s", bot.id, err)
        return None
    latency_ms = int((time.perf_counter() - t0) * 1000)

    applied = router.resolve(decision)
    await record_route_decision(
        db_mgr,
        org_id=request.org_id,
        bot_id=bot.id,
        channel_id=request.channel_id,
        thread_ts=request.thread_ts,
        slack_user_id=request.slack_user_id,
        excerpt=request.thread_excerpt,
        route=decision.route,
        applied=applied,
        confidence=decision.confidence,
        reason=decision.reason,
        model=model_name,
        latency_ms=latency_ms,
    )
    return decision


async def try_classify(
    gateway: LLMGatewayProtocol,
    bot: SlackBot,
    org_id: str,
    text: str,
    thread_excerpt: str = "",
    db_mgr: DatabaseSessionManager | None = None,
    default_model: str = "",
) -> dict[str, Any]:
    router = bot.router
    if router is None or not router.active_routes():
        raise ValueError("This bot has no active routes configured.")
    model_name = await _resolve_classifier_model(db_mgr, bot, default_model)

    t0 = time.perf_counter()
    decision = await _dispatch_classification(
        gateway, bot, org_id, model_name, text, thread_excerpt
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)
    applied = router.resolve(decision)
    return {
        "route": decision.route,
        "applied": applied,
        "confidence": decision.confidence,
        "reason": decision.reason,
        "probabilities": decision.probabilities,
        "model": model_name,
        "latency_ms": latency_ms,
    }
