"""An agent's CPU and memory can lower the sandbox's resources, never raise them."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from api.services.sandbox_catalogue import SandboxEntry
from api.turns.provisioning import _resources

ORG = {"cpu_limit": "2", "memory_limit": "4Gi"}


def _agent(cpu: str = "", memory: str = "") -> SimpleNamespace:
    return SimpleNamespace(cpu_limit=cpu, memory_limit=memory)


@pytest.mark.parametrize(
    "agent, expected",
    [
        (_agent(), (2.0, "4Gi")),
        (_agent("8", "64Gi"), (2.0, "4Gi")),  # above the organization: clamped
        (_agent("1", "1Gi"), (1.0, "1Gi")),  # below: the agent's request stands
        (_agent("1", "8192Mi"), (1.0, "4Gi")),
    ],
)
def test_the_organization_value_is_the_ceiling(
    agent: SimpleNamespace, expected: tuple[float, str]
) -> None:
    limits = _resources(agent, None, ORG)
    assert (limits.cpu_cores, limits.memory_limit) == expected


def test_the_sandbox_type_is_the_ceiling_when_one_is_chosen() -> None:
    heavy = SandboxEntry(
        id="t", org_id="o", name="heavy", image="img", cpu_limit="8", memory_limit="16Gi"
    )
    limits = _resources(_agent("16", "32Gi"), heavy, ORG)
    assert (limits.cpu_cores, limits.memory_limit) == (8.0, "16Gi")
