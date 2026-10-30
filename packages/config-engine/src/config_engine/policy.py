"""Who decides a setting, and the one rule that resolves its value.

Every setting declares a ``policy``:

* ``instance`` - the operator sets it in the environment; nobody changes it in the product.
* ``org`` - administrators decide; members, sessions and agents do not.
* ``org_default`` - administrators set the default; members, sessions and presets may choose
  another value unless an administrator locks the key.
* ``member`` - each member's own choice.

Agents, Slack bots and channel profiles are *presets*: they offer candidate values at session
level, which this resolver accepts or refuses by policy, lock and ceiling. No preset is a
layer of its own, so no preset can get around a lock (an agent written by a member is no
more trusted than the member).

A numeric spec marked ``ceiling`` lets the levels below its decider lower the value, never
raise it: an agent may run fewer steps than the organization allows, not more.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .exceptions import PolicyViolationError

Policy = Literal["instance", "org", "org_default", "member"]
LockedBy = Literal["org", "instance"]

# Where a resolved value came from: "platform", "instance", "org", "member", "session",
# "agent:<id>", "slack_bot:<id>", "channel:<id>".
Source = str

# Values that mean "no limit" for a ceiling: they never cap the levels below.
_UNLIMITED = (None, 0, 0.0)


@dataclass(frozen=True)
class Preset:
    """Candidate values offered at session level by ``source`` (a session, agent or bot)."""

    source: Source
    values: Mapping[str, Any]


@dataclass(frozen=True)
class Resolved:
    """A setting's value, where it came from, and who (if anyone) holds it there."""

    value: Any
    source: Source
    locked_by: LockedBy | None = None
    # Candidates the policy refused (a preset above a ceiling, a member value under a lock),
    # so a caller or the UI can say why a choice did not apply.
    refused: tuple[Source, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "locked_by": self.locked_by, "refused": list(self.refused)}


@dataclass(frozen=True)
class ResolveContext:
    """Everything the resolver needs about one organization, member and session."""

    org: Mapping[str, Any] = field(default_factory=dict)
    locked: frozenset[str] = frozenset()
    member: Mapping[str, Any] = field(default_factory=dict)
    # Highest priority first; the session's own choice is a preset with source "session".
    presets: tuple[Preset, ...] = ()
    # Operator values from the environment, already coerced, by key.
    instance: Mapping[str, Any] = field(default_factory=dict)
    # An extra test every candidate must pass (the model allowlist); a refused candidate is
    # skipped, never used.
    accept: Callable[[Any], bool] | None = None


class _Spec(Protocol):
    """The part of a ``SettingSpec`` the resolver reads (structural, so no import cycle)."""

    @property
    def key(self) -> str: ...
    @property
    def default(self) -> Any: ...
    @property
    def policy(self) -> Policy: ...
    @property
    def ceiling(self) -> bool: ...
    @property
    def member_override(self) -> bool: ...


def _present(values: Mapping[str, Any], key: str) -> bool:
    value = values.get(key)
    return key in values and value is not None and value != ""


_QUANTITY_SUFFIXES = {
    "m": 1e-3,
    "k": 1e3,
    "Ki": 2.0**10,
    "M": 1e6,
    "Mi": 2.0**20,
    "G": 1e9,
    "Gi": 2.0**30,
    "T": 1e12,
    "Ti": 2.0**40,
}


def quantity(value: Any) -> float | None:
    """A number or a Kubernetes quantity (``500m``, ``4Gi``) as a float; ``None`` if neither."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for suffix in sorted(_QUANTITY_SUFFIXES, key=len, reverse=True):
        if text.endswith(suffix):
            text, factor = text[: -len(suffix)], _QUANTITY_SUFFIXES[suffix]
            break
    else:
        factor = 1.0
    try:
        return float(text) * factor
    except ValueError:
        return None


def _lowers(candidate: Any, ceiling: Any) -> bool:
    """Whether ``candidate`` stays at or under ``ceiling`` (``0``/``None``/empty: no limit)."""
    limit = quantity(ceiling)
    if ceiling in _UNLIMITED or ceiling == "" or limit is None:
        return True
    amount = quantity(candidate)
    return amount is not None and 0 < amount <= limit


def _upper(spec: _Spec, ctx: ResolveContext) -> Resolved:
    """The decider's value: organization, else instance, else platform default."""
    if spec.policy != "instance" and spec.policy != "member" and _present(ctx.org, spec.key):
        return Resolved(ctx.org[spec.key], "org")
    if _present(ctx.instance, spec.key):
        return Resolved(ctx.instance[spec.key], "instance")
    return Resolved(spec.default, "platform")


def _lower_candidates(spec: _Spec, ctx: ResolveContext) -> list[tuple[Source, Any]]:
    candidates = [
        (preset.source, preset.values[spec.key])
        for preset in ctx.presets
        if _present(preset.values, spec.key)
    ]
    if spec.member_override and _present(ctx.member, spec.key):
        candidates.append(("member", ctx.member[spec.key]))
    return candidates


def resolve(spec: _Spec, ctx: ResolveContext) -> Resolved:
    """The effective value of ``spec`` for this context, with its provenance."""
    upper = _upper(spec, ctx)
    if spec.policy == "instance":
        return Resolved(upper.value, upper.source, "instance")
    lower = _lower_candidates(spec, ctx)
    if spec.policy == "org" and not spec.ceiling:
        return upper
    if spec.policy == "org_default" and spec.key in ctx.locked:
        return Resolved(upper.value, upper.source, "org", tuple(source for source, _ in lower))
    refused: list[Source] = []
    for source, value in lower:
        if spec.ceiling and not _lowers(value, upper.value):
            refused.append(source)
            continue
        if ctx.accept is not None and not ctx.accept(value):
            refused.append(source)
            continue
        return Resolved(value, source, None, tuple(refused))
    fallbacks = [(upper.source, upper.value)]
    if upper.source != "platform":
        fallbacks.append(("platform", spec.default))
    for source, value in fallbacks:
        if ctx.accept is None or ctx.accept(value):
            return Resolved(value, source, None, tuple(refused))
        refused.append(source)
    raise PolicyViolationError(
        f"No usable value for {spec.key}: every candidate ({', '.join(refused)}) is refused "
        "by the organization's policy."
    )
