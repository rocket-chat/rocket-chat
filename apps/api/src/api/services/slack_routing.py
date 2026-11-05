"""Classification of Slack messages to agents via structured output, and decision logging."""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from llm_gateway.structured import generate_structured
from pydantic import BaseModel, Field
from slack_assistant.bot import (
    DISMISS,
    IGNORE,
    RESPOND,
    ClassifyRequest,
    FollowUpRequest,
    RouteDecision,
    SlackBot,
)
from sqlalchemy import delete, desc, select

from api.db.models import SlackRouteDecisionModel
from api.db.session import DatabaseSessionManager
from api.services.personas import get_persona
from api.services.systemone import call_systemone_choice, is_systemone_model
from api.turns.routing import _resolve_turn_routing
from specifications.interfaces.llm import (
    ChatMessage,
    CredentialBinding,
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

    route: str = Field(description="The chosen option's name")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0")
    reason: str = Field(description="Concise rationale for this classification")


@dataclass(frozen=True)
class _Question:
    """What a classifier is asked: pick one of ``choices`` (name -> when to pick it)."""

    key: str
    instructions: str
    choices: dict[str, str]


def _route_question(bot: SlackBot) -> _Question:
    router = bot.router
    assert router is not None
    choices: dict[str, str] = {}
    for r in router.active_routes():
        desc = r.description
        if r.examples:
            desc += f" (Examples: {', '.join(r.examples)})"
        choices[r.name] = desc
    if router.allow_dismiss:
        choices[DISMISS] = (
            router.dismiss_description or "Irrelevant message, small talk, or out of scope."
        )
    return _Question("route", "Select the appropriate route for this user request.", choices)


def _follow_up_question(bot: SlackBot) -> _Question:
    name = bot.name or "the bot"
    return _Question(
        "respond",
        f"Someone replied in a Slack thread where the assistant {name} works, without "
        f"mentioning it. Decide whether the reply asks {name} for something or is meant for "
        "the other people of the thread.",
        {
            RESPOND: f"The reply asks {name} to do, answer or change something: a new request, "
            f"a correction, an answer to a question {name} asked, or more work on its answer.",
            IGNORE: f"The reply is for the other people: it summarises or relays {name}'s "
            "answer, discusses among people, thanks or acknowledges, or comments without "
            f"asking {name} for anything.",
        },
    )


def _build_classification_messages(
    question: _Question, text: str, excerpt: str
) -> list[ChatMessage]:
    lines = [
        "You are an incoming message classifier for a Slack bot assistant.",
        question.instructions,
        "\nOptions:",
        *(f"- '{name}': {when}" for name, when in question.choices.items()),
        f"\nYou must respond with JSON selecting one of these options: [{', '.join(question.choices)}].",
    ]
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
    preferred: str = "",
) -> str:
    """``preferred``, else the router's model, else its classifier agent's, else the
    organization's default; always checked against the organization's allowed and forbidden
    models."""
    router = bot.router
    chosen = preferred or (router.classifier_model if router else "")
    if router and not chosen and router.classifier_agent_id:
        persona = await get_persona(
            db_mgr, bot.org_id, "", router.classifier_agent_id, enforce_access=False
        )
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
async def _billed_to(
    db_mgr: DatabaseSessionManager | None, bot: SlackBot, org_id: str, call_role: str
) -> AsyncIterator[ModelScope]:
    """Bill the classification to the bot's organization, with its keys and limits, and with
    its classifier agent's credential bindings: it runs before any session or turn exists,
    so no turn has bound the scope."""
    agent_id = bot.router.classifier_agent_id if bot.router else ""
    persona = (
        await get_persona(db_mgr, org_id, "", agent_id, enforce_access=False) if agent_id else None
    )
    scope = ModelScope(
        org_id=org_id,
        user_id=f"slack-bot:{bot.id}",
        agent_id=persona.id if persona else None,
        credential_bindings=tuple(
            CredentialBinding(b.provider, b.credential_id) for b in persona.credential_bindings
        )
        if persona
        else (),
        strict_binding=bool(persona.strict_binding) if persona else False,
        call_role=call_role,
    )
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
    question: _Question,
    text: str,
    thread_excerpt: str,
) -> tuple[RouteDecision, int]:
    res = await call_systemone_choice(
        model=model_name,
        api_key=api_key,
        message=text,
        thread_excerpt=thread_excerpt,
        question_key=question.key,
        instructions=question.instructions,
        criteria=question.choices,
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
    question: _Question,
    text: str,
    thread_excerpt: str,
) -> RouteDecision:
    messages = _build_classification_messages(question, text, thread_excerpt)
    model_req = ModelRequest(messages=messages, model=model_name, temperature=0.0)
    structured = await generate_structured(gateway, model_req, RouterClassification)
    return RouteDecision(
        route=structured.route.strip(),
        confidence=float(structured.confidence),
        reason=structured.reason.strip(),
    )


async def _dispatch_classification(
    gateway: LLMGatewayProtocol,
    db_mgr: DatabaseSessionManager | None,
    bot: SlackBot,
    org_id: str,
    model_name: str,
    question: _Question,
    text: str,
    thread_excerpt: str,
) -> RouteDecision:
    role = "slack_router" if question.key == "route" else "slack_follow_up"
    async with _billed_to(db_mgr, bot, org_id, role) as scope:
        if is_systemone_model(model_name):
            decision = await _try_systemone(
                gateway, scope, question, model_name, text, thread_excerpt
            )
            if decision is not None:
                return decision
        return await _classify_via_structured(gateway, model_name, question, text, thread_excerpt)


async def _try_systemone(
    gateway: LLMGatewayProtocol,
    scope: ModelScope,
    question: _Question,
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
        # The call costs input tokens only and has no LiteLLM price: nothing to set aside,
        # but a limit that is already full refuses it.
        reservation = await guard.reserve(scope, estimate_usd=0.0, key_source=key_source)
        if reservation is not None:
            await guard.settle(reservation, actual_usd=0.0)
    try:
        decision, input_tokens = await _classify_via_systemone(
            api_key, model_name, question, text, thread_excerpt
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
                    agent_id=scope.agent_id,
                    call_role=scope.call_role,
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
            gateway,
            db_mgr,
            bot,
            request.org_id,
            model_name,
            _route_question(bot),
            request.text,
            request.thread_excerpt,
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
        gateway, db_mgr, bot, org_id, model_name, _route_question(bot), text, thread_excerpt
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


async def classify_follow_up(
    gateway: LLMGatewayProtocol,
    request: FollowUpRequest,
    db_mgr: DatabaseSessionManager | None,
    default_model: str = "",
) -> RouteDecision | None:
    """Whether a thread reply without a mention asks the bot for something (``RESPOND``) or
    is for the others (``IGNORE``); ``None`` when the classifier could not decide. Each
    decision joins the bot's routing log, where its prompts are tuned."""
    bot = request.bot
    model_name = await _resolve_classifier_model(db_mgr, bot, default_model, request.model)
    t0 = time.perf_counter()
    try:
        decision = await _dispatch_classification(
            gateway,
            db_mgr,
            bot,
            request.org_id,
            model_name,
            _follow_up_question(bot),
            request.text,
            request.thread_excerpt,
        )
    except Exception as err:
        logger.warning("Follow-up classifier failed for bot %s: %s", bot.id, err)
        return None
    if decision.route not in (RESPOND, IGNORE):
        logger.info("Follow-up classifier of bot %s answered %r", bot.id, decision.route)
        return None
    await record_route_decision(
        db_mgr,
        org_id=request.org_id,
        bot_id=bot.id,
        channel_id=request.channel_id,
        thread_ts=request.thread_ts,
        slack_user_id=request.slack_user_id,
        # The reply first: the log keeps an excerpt's first 2,000 characters.
        excerpt=f"Reply: {request.text}\n\n{request.thread_excerpt}".strip(),
        route=decision.route,
        applied=decision.route,
        confidence=decision.confidence,
        reason=decision.reason,
        model=model_name,
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
    return decision
