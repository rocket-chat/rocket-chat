"""Generic Cascading Settings Engine supporting Org Defaults, User Overrides, and Admin Lockouts."""

import fnmatch
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from .exceptions import PolicyViolationError
from .registry import domains, system_defaults

FieldStatus = Literal["org_default", "overridden", "locked"]

# The three task-aware routing modes. ``frontier`` is today's behaviour:
# one model for everything. ``balanced`` splits cheap auxiliary work from the ReAct loop.
# ``economy`` pins the cheapest model for all work.
ROUTING_MODES = ("frontier", "balanced", "economy")

# Roles whose work is auxiliary (status/summaries/suggestions) and therefore eligible to
# run on the fast model under ``balanced``. Any other role — the ReAct loop and subagents
# — stays on the frontier model.
AUXILIARY_ROLES = frozenset({"summarize", "suggest", "subagent:researcher"})

# Platform defaults come from the typed registry (``config_engine.registry``), the single
# declaration of every setting's scope, type and runtime consumer.
SYSTEM_DOMAIN_DEFAULTS: dict[str, dict[str, Any]] = {
    domain: system_defaults(domain) for domain in domains()
}


@dataclass
class ResolvedSettings:
    """Resolved cascading configuration with inheritance tracking and locked key enforcement."""

    domain: str
    effective: dict[str, Any]
    field_status: dict[str, FieldStatus]
    locked_keys: list[str] = field(default_factory=list)
    org_defaults: dict[str, Any] = field(default_factory=dict)
    user_overrides: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "domain": self.domain,
            "effective": self.effective,
            "field_status": self.field_status,
            "locked_keys": self.locked_keys,
            "org_defaults": self.org_defaults,
            "user_overrides": self.user_overrides,
        }


class CascadingSettingsResolver:
    """Resolves effective settings following: System Defaults -> Org Policy -> User Override.

    Enforces that keys present in `locked_keys` cannot be overridden by user configurations.
    """

    def __init__(self, custom_defaults: dict[str, dict[str, Any]] | None = None) -> None:
        self.defaults = dict(SYSTEM_DOMAIN_DEFAULTS)
        if custom_defaults:
            for domain, conf in custom_defaults.items():
                self.defaults.setdefault(domain, {}).update(conf)

    def get_system_defaults(self, domain: str) -> dict[str, Any]:
        """Retrieve system default configuration for a given domain."""
        return dict(self.defaults.get(domain, {}))

    def resolve(
        self,
        domain: str,
        org_config: dict[str, Any] | None = None,
        locked_keys: list[str] | None = None,
        user_overrides: dict[str, Any] | None = None,
    ) -> ResolvedSettings:
        """Calculate effective configuration, field status annotations, and enforce locked keys."""
        system_base = self.get_system_defaults(domain)
        org_conf = dict(org_config or {})
        locked = list(locked_keys or [])
        user_over = dict(user_overrides or {})

        # Step 1: Base org defaults = system_base merged with org_conf
        merged_org: dict[str, Any] = dict(system_base)
        merged_org.update(org_conf)

        effective: dict[str, Any] = dict(merged_org)
        field_status: dict[str, FieldStatus] = {}

        # Default all known keys to org_default
        for key in merged_org:
            field_status[key] = "locked" if key in locked else "org_default"

        # Step 2: Apply user overrides where not locked
        for key, value in user_over.items():
            if key in locked:
                # User cannot override locked keys
                field_status[key] = "locked"
                # Keep org value in effective
                effective[key] = merged_org.get(key, value)
            else:
                effective[key] = value
                field_status[key] = "overridden"

        return ResolvedSettings(
            domain=domain,
            effective=effective,
            field_status=field_status,
            locked_keys=locked,
            org_defaults=merged_org,
            user_overrides=user_over,
        )

    def filter_valid_user_overrides(
        self,
        domain: str,
        requested_overrides: dict[str, Any],
        locked_keys: list[str],
    ) -> tuple[dict[str, Any], list[str]]:
        """Separate allowed user overrides from disallowed locked keys.

        Returns:
            (allowed_overrides, rejected_locked_keys)
        """
        allowed: dict[str, Any] = {}
        rejected: list[str] = []

        for key, value in requested_overrides.items():
            if key in locked_keys:
                rejected.append(key)
            else:
                allowed[key] = value

        return allowed, rejected


def _as_model_list(value: Any) -> tuple[str, ...]:
    """Normalize an org setting into a tuple of non-empty model patterns."""
    if value is None:
        return ()
    if isinstance(value, str):
        candidates: Sequence[Any] = [part.strip() for part in value.split(",")]
    elif isinstance(value, Sequence):
        candidates = value
    else:
        return ()
    return tuple(str(item).strip() for item in candidates if str(item).strip())


def model_is_allowed(
    model: str,
    *,
    allowed_models: Sequence[str] = (),
    forbidden_models: Sequence[str] = (),
) -> bool:
    """Whether ``model`` satisfies the organization allow/deny patterns.

    Mirrors ``ConfigEngine._validate_model_policy``: forbidden patterns win, and an
    empty allowlist means "no allowlist" rather than "deny everything".
    """
    candidates = (model or "").strip()
    if not candidates:
        return False
    if any(fnmatch.fnmatchcase(candidates, pattern) for pattern in forbidden_models):
        return False
    if allowed_models and not any(
        fnmatch.fnmatchcase(candidates, pattern) for pattern in allowed_models
    ):
        return False
    return True


def image_is_allowed(image: str, *, default_image: str, allowed_images: Sequence[str] = ()) -> bool:
    """Whether a sandbox image may be used: the org default, or one matching the allowlist."""
    candidate = (image or "").strip()
    if not candidate:
        return False
    if candidate == default_image:
        return True
    return any(fnmatch.fnmatchcase(candidate, pattern) for pattern in allowed_images)


def resolve_model_precedence(
    *,
    agent_model: str | None,
    request_model: str | None,
    user_preferred_model: str | None,
    org_default_model: str | None,
    global_default_model: str,
    allowed_models: Sequence[str] = (),
    forbidden_models: Sequence[str] = (),
) -> tuple[str, str]:
    """Resolve the effective model and validate it against the allowlist.

    Precedence, highest first: ``session.agent.model`` > this turn's explicit model
    (the user's picker at dispatch time) > the saved user preference > organization model
    policy > global default. Every candidate is checked against the org allowlist; a
    disallowed candidate is skipped rather than used, so a user preference can never
    escape a locked-down org. Returns ``(model, human_readable_reason)``.
    """
    candidates: tuple[tuple[str | None, str], ...] = (
        (agent_model, "the agent bound to this session"),
        (request_model, "the model selected for this turn"),
        (user_preferred_model, "your saved model preference"),
        (org_default_model, "the organization model policy"),
        (global_default_model, "the platform default"),
    )
    for candidate, reason in candidates:
        model = (candidate or "").strip()
        if model and model_is_allowed(
            model, allowed_models=allowed_models, forbidden_models=forbidden_models
        ):
            return model, reason

    raise PolicyViolationError(
        "No usable model: every candidate (agent, turn selection, user preference, org "
        f"policy, platform default) is excluded by the organization allowlist "
        f"({list(allowed_models)})."
    )


@dataclass(frozen=True)
class RoutingPolicy:
    """Per-organization model-routing configuration."""

    mode: str = "frontier"
    fast_model: str = ""
    economy_model: str = ""
    allowed_models: tuple[str, ...] = ()
    forbidden_models: tuple[str, ...] = ()

    @classmethod
    def from_settings(cls, config: Mapping[str, Any] | None) -> "RoutingPolicy":
        """Build a policy from a raw org ``routing`` settings mapping."""
        source = config or {}
        mode = str(source.get("mode") or "frontier").strip().lower()
        if mode not in ROUTING_MODES:
            mode = "frontier"
        return cls(
            mode=mode,
            fast_model=str(source.get("fast_model") or "").strip(),
            economy_model=str(source.get("economy_model") or "").strip(),
            allowed_models=_as_model_list(source.get("allowed_models")),
            forbidden_models=_as_model_list(source.get("forbidden_models")),
        )


class ModelRouter:
    """Selects a model per *role of work*, enforcing mode and the org allowlist.

    ``frontier_model`` is the precedence-resolved model for the session: in
    ``frontier`` mode every role uses it (today's behaviour), in ``balanced`` the
    auxiliary roles use ``fast_model`` while the ReAct loop and subagents keep it, and in
    ``economy`` every role uses ``economy_model``. Any mapped model that violates the
    allowlist degrades to the resolved frontier model — never to a disallowed one.
    """

    def __init__(self, *, frontier_model: str, policy: RoutingPolicy) -> None:
        self._frontier = (frontier_model or "").strip()
        self._policy = policy
        self._fast = (policy.fast_model or self._frontier).strip()
        self._economy = (policy.economy_model or self._frontier).strip()

    @property
    def mode(self) -> str:
        return self._policy.mode

    def _allowed(self, model: str) -> bool:
        return model_is_allowed(
            model,
            allowed_models=self._policy.allowed_models,
            forbidden_models=self._policy.forbidden_models,
        )

    def model_for(self, role: str) -> str:
        normalized = (role or "react").strip().lower()
        if self._policy.mode == "economy":
            candidate = self._economy
        elif self._policy.mode == "balanced":
            candidate = self._fast if normalized in AUXILIARY_ROLES else self._frontier
        else:
            candidate = self._frontier

        if self._allowed(candidate):
            return candidate
        if self._allowed(self._frontier):
            return self._frontier
        raise PolicyViolationError(
            f"Routing mode '{self._policy.mode}' has no model permitted by the organization "
            f"allowlist ({list(self._policy.allowed_models)})."
        )

    def describe(self, role: str) -> str:
        normalized = (role or "react").strip().lower()
        model = self.model_for(role)
        if self._policy.mode == "frontier":
            return f"{model} (frontier mode: one model for all work)"
        if self._policy.mode == "economy":
            return f"{model} (economy mode: cheapest model for all work)"
        if normalized in AUXILIARY_ROLES:
            return f"{model} (balanced mode: fast model for {normalized})"
        return f"{model} (balanced mode: frontier model for {normalized})"
