"""What kind of failure ended a turn, so a client can offer the way out without reading text."""

from __future__ import annotations

from sandbox_driver.exceptions import SandboxError

from api.services.agent_access import AgentAccessDenied
from specifications.interfaces.event_payloads import TurnErrorKind
from specifications.interfaces.llm import (
    BudgetExceededError,
    CallRefusal,
    CredentialRefusedError,
    ModelExecutionError,
)

# A provider refusing the key or the account, as opposed to the model or the request.
# How a failed turn's stored answer starts (the cockpit's error card and the mission runner read it).
FAILED_ANSWER_PREFIX = "**The turn failed:**"

_CREDENTIAL_SIGNS = (
    "authentication",
    "unauthorized",
    "invalid api key",
    "api key",
    "permission denied",
)
_MODEL_SIGNS = ("model not found", "not_found", "no such model", "does not exist", "not allowed")


def refusal_of(err: BaseException) -> CallRefusal | None:
    """The platform's own refusal behind a failed model call, if that is what failed."""
    if isinstance(err, ModelExecutionError) and err.refusal is not None:
        return err.refusal
    return CallRefusal.from_error(err)


def classify_turn_error(err: BaseException) -> TurnErrorKind:
    """``budget`` (a spend limit), ``access`` (the agent is not the person's to use),
    ``provider`` (keys, quota or an outage), ``model`` (the model is unknown or not allowed),
    ``sandbox`` (the workspace) or ``internal``."""
    if isinstance(err, AgentAccessDenied):
        return "access"
    refusal = refusal_of(err)
    if isinstance(err, BudgetExceededError) or (refusal is not None and refusal.kind == "budget"):
        return "budget"
    if isinstance(err, CredentialRefusedError) or refusal is not None:
        return "provider"
    if isinstance(err, SandboxError):
        return "sandbox"
    message = str(err).lower()
    if isinstance(err, ModelExecutionError) or any(s in message for s in _CREDENTIAL_SIGNS):
        if any(sign in message for sign in _MODEL_SIGNS):
            return "model"
        return "provider"
    return "internal"
