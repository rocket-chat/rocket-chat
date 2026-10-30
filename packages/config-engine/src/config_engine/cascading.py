"""Settings resolution for a whole domain, and model routing, on top of ``config_engine.policy``."""

import fnmatch
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .exceptions import PolicyViolationError
from .policy import Policy, Preset, ResolveContext, Resolved, resolve
from .registry import SettingSpec, domains, instance_values, specs_for, system_defaults

# The three task-aware routing modes. ``frontier`` is today's behaviour:
# one model for everything. ``balanced`` splits cheap auxiliary work from the ReAct loop.
# ``economy`` pins the cheapest model for all work.
ROUTING_MODES = ("frontier", "balanced", "economy")

# Roles whose work is auxiliary (status/summaries/suggestions) and therefore eligible to
# run on the fast model under ``balanced``. Any other role — the ReAct loop and subagents
# — stays on the frontier model.
AUXILIARY_ROLES = frozenset({"summarize", "suggest", "subagent:researcher"})

# Platform defaults come from the typed registry (``config_engine.registry``), the single
# declaration of every setting's policy, type and runtime consumer.
SYSTEM_DOMAIN_DEFAULTS: dict[str, dict[str, Any]] = {
    domain: system_defaults(domain) for domain in domains()
}


@dataclass
class ResolvedSettings:
    """A domain's effective values, each with where it came from (``provenance``)."""

    domain: str
    effective: dict[str, Any]
    provenance: dict[str, Resolved]
    locked_keys: list[str] = field(default_factory=list)
    org_defaults: dict[str, Any] = field(default_factory=dict)
    user_overrides: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "domain": self.domain,
            "effective": self.effective,
            "provenance": {key: value.to_dict() for key, value in self.provenance.items()},
            "locked_keys": self.locked_keys,
            "org_defaults": self.org_defaults,
            "user_overrides": self.user_overrides,
        }


class CascadingSettingsResolver:
    """Resolves every key of a domain by its policy: platform -> instance -> org -> member -> presets."""

    def __init__(self, custom_defaults: dict[str, dict[str, Any]] | None = None) -> None:
        self.defaults = {domain: dict(values) for domain, values in SYSTEM_DOMAIN_DEFAULTS.items()}
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
        presets: Sequence[Preset] = (),
    ) -> ResolvedSettings:
        """Each key of ``domain`` resolved by its policy, lock and ceiling."""
        ctx = ResolveContext(
            org=dict(org_config or {}),
            locked=frozenset(locked_keys or ()),
            member=dict(user_overrides or {}),
            presets=tuple(presets),
            instance=instance_values(domain),
        )
        defaults = self.get_system_defaults(domain)
        provenance = {
            key: resolve(_with_default(spec, defaults), ctx)
            for key, spec in specs_for(domain).items()
        }
        org_defaults = {**defaults, **ctx.instance, **ctx.org}
        return ResolvedSettings(
            domain=domain,
            effective={key: value.value for key, value in provenance.items()},
            provenance=provenance,
            locked_keys=sorted(ctx.locked),
            org_defaults=org_defaults,
            user_overrides=dict(ctx.member),
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


def _with_default(spec: SettingSpec, defaults: Mapping[str, Any]) -> SettingSpec:
    """``spec`` with this resolver's default (tests and deployments may replace a default)."""
    return replace(spec, default=defaults.get(spec.key, spec.default))


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


_MODEL_REASONS = {
    "session": "the model selected for this turn",
    "member": "your saved model preference",
    "org": "the organization model policy",
    "instance": "the deployment default",
    "platform": "the platform default",
}


@dataclass(frozen=True)
class _ModelSpec:
    """The model choice as a setting: an organization default every level may change."""

    default: str
    key: str = "model"
    policy: Policy = "org_default"
    ceiling: bool = False
    member_override: bool = True


def resolve_model(
    *,
    agent_model: str | None,
    request_model: str | None,
    user_preferred_model: str | None,
    org_default_model: str | None,
    global_default_model: str,
    allowed_models: Sequence[str] = (),
    forbidden_models: Sequence[str] = (),
    agent_source: str = "agent",
) -> Resolved:
    """The turn's model through the policy resolver, with the allowlist as its acceptance test.

    Precedence, highest first: the agent bound to the session (a preset) > this turn's
    explicit model (the session's choice) > the member's preference > the organization's
    default > the platform default. A candidate the allowlist refuses is skipped, never used,
    so no preset or preference escapes a locked-down organization.
    """
    ctx = ResolveContext(
        org={"model": (org_default_model or "").strip()},
        member={"model": (user_preferred_model or "").strip()},
        presets=(
            Preset(agent_source, {"model": (agent_model or "").strip()}),
            Preset("session", {"model": (request_model or "").strip()}),
        ),
        accept=lambda model: (
            bool(model)
            and model_is_allowed(
                str(model), allowed_models=allowed_models, forbidden_models=forbidden_models
            )
        ),
    )
    try:
        return resolve(_ModelSpec(default=(global_default_model or "").strip()), ctx)
    except PolicyViolationError as err:
        raise PolicyViolationError(
            "No usable model: every candidate (agent, turn selection, user preference, org "
            f"policy, platform default) is excluded by the organization allowlist "
            f"({list(allowed_models)})."
        ) from err


def resolve_model_precedence(**kwargs: Any) -> tuple[str, str]:
    """``(model, human-readable reason)`` from :func:`resolve_model`."""
    resolved = resolve_model(**kwargs)
    reason = (
        "the agent bound to this session"
        if resolved.source.startswith("agent")
        else _MODEL_REASONS.get(resolved.source, resolved.source)
    )
    return str(resolved.value), reason


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
        """Build a policy from the organization's ``models`` settings."""
        source = config or {}
        mode = str(source.get("routing_mode") or "frontier").strip().lower()
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
