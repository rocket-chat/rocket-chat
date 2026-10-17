"""Placement profiles: where and how a sandbox pod runs, defined by the operator.

A profile names a node pool and its sizing (node selector, tolerations, runtime class, priority
class, labels, annotations, storage class, pod requests and limits). Operators define them in
the deployment (``K8S_SANDBOX_PROFILES``, a JSON object of name -> profile); organization
administrators only choose one per sandbox type. Placement is deliberately not editable by
organizations: in a cluster shared by several, a toleration would let one schedule onto nodes
it was never meant to use.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

PROFILES_ENV = "K8S_SANDBOX_PROFILES"
_TOLERATION_KEYS = frozenset({"key", "operator", "value", "effect", "tolerationSeconds"})
_RESOURCE_KEYS = frozenset({"cpu", "memory", "ephemeral-storage"})


class ProfileError(ValueError):
    """The operator's profile definitions are invalid."""


@dataclass(frozen=True)
class PlacementProfile:
    name: str
    description: str = ""
    node_selector: Mapping[str, str] = field(default_factory=dict)
    tolerations: tuple[Mapping[str, Any], ...] = ()
    runtime_class_name: str | None = None
    priority_class_name: str | None = None
    labels: Mapping[str, str] = field(default_factory=dict)
    annotations: Mapping[str, str] = field(default_factory=dict)
    storage_class_name: str | None = None
    # Pod requests and limits; empty keeps the deployment's sizing.
    requests: Mapping[str, str] = field(default_factory=dict)
    limits: Mapping[str, str] = field(default_factory=dict)


def _strings(raw: Any, what: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ProfileError(f"{what} must be an object of strings")
    return {str(k): str(v) for k, v in raw.items()}


def _resources(raw: Any, what: str) -> dict[str, str]:
    values = _strings(raw, what)
    unknown = set(values) - _RESOURCE_KEYS
    if unknown:
        raise ProfileError(f"{what}: unknown resources {sorted(unknown)}")
    return values


def _tolerations(raw: Any, name: str) -> tuple[Mapping[str, Any], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(t, Mapping) for t in raw):
        raise ProfileError(f"profile {name}: tolerations must be a list of objects")
    for toleration in raw:
        unknown = set(toleration) - _TOLERATION_KEYS
        if unknown:
            raise ProfileError(f"profile {name}: unknown toleration fields {sorted(unknown)}")
    return tuple(dict(t) for t in raw)


def _profile(name: str, raw: Any) -> PlacementProfile:
    if not isinstance(raw, Mapping):
        raise ProfileError(f"profile {name} must be an object")
    resources = raw.get("resources") or {}
    if not isinstance(resources, Mapping):
        raise ProfileError(f"profile {name}: resources must be an object")
    return PlacementProfile(
        name=name,
        description=str(raw.get("description") or ""),
        node_selector=_strings(raw.get("nodeSelector"), f"profile {name}: nodeSelector"),
        tolerations=_tolerations(raw.get("tolerations"), name),
        runtime_class_name=raw.get("runtimeClassName") or None,
        priority_class_name=raw.get("priorityClassName") or None,
        labels=_strings(raw.get("labels"), f"profile {name}: labels"),
        annotations=_strings(raw.get("annotations"), f"profile {name}: annotations"),
        storage_class_name=raw.get("storageClassName") or None,
        requests=_resources(resources.get("requests"), f"profile {name}: requests"),
        limits=_resources(resources.get("limits"), f"profile {name}: limits"),
    )


def parse_profiles(text: str) -> dict[str, PlacementProfile]:
    """Profiles from their JSON definition (camelCase, as in a pod spec)."""
    if not text.strip():
        return {}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as err:
        raise ProfileError(f"{PROFILES_ENV} is not valid JSON: {err}") from err
    if not isinstance(raw, Mapping):
        raise ProfileError(f"{PROFILES_ENV} must be an object of name -> profile")
    return {str(name): _profile(str(name), value) for name, value in raw.items()}


def configured_profiles() -> dict[str, PlacementProfile]:
    """The deployment's profiles; an invalid definition is logged and offers none."""
    try:
        return parse_profiles(os.getenv(PROFILES_ENV, ""))
    except ProfileError as err:
        logger.error("Sandbox placement profiles ignored: %s", err)
        return {}


def parse_cpu(val: str | None) -> float:
    if not val:
        return 1.0
    val = val.strip()
    if val.endswith("m"):
        return float(val[:-1]) / 1000.0
    try:
        return float(val)
    except ValueError:
        return 1.0


def parse_memory_mb(val: str | None) -> float:
    if not val:
        return 2048.0
    val = val.strip().lower()
    if val.endswith("gi"):
        return float(val[:-2]) * 1024.0
    if val.endswith("g"):
        return float(val[:-1]) * 1000.0
    if val.endswith("mi"):
        return float(val[:-2])
    if val.endswith("m"):
        return float(val[:-1])
    try:
        return float(val) / (1024.0 * 1024.0)
    except ValueError:
        return 2048.0


def has_parallel_write_capacity(profile: PlacementProfile | None) -> bool:
    """Parallel write subagents require >= 2 CPU and >= 4 GiB RAM to prevent compiler OOMs."""
    if profile is None:
        return False
    cpu = parse_cpu(profile.limits.get("cpu") or profile.requests.get("cpu"))
    mem = parse_memory_mb(profile.limits.get("memory") or profile.requests.get("memory"))
    return cpu >= 2.0 and mem >= 4000.0
