"""A sandbox's CPU and memory come from its sandbox type, else the organization; an agent sets
none of its own (it picks a type), so it can never raise them."""

from __future__ import annotations

from api.models import AgentPersonaModel
from api.services.sandbox_catalogue import SandboxEntry
from api.turns.provisioning import _resources

ORG = {"cpu_limit": "2", "memory_limit": "4Gi"}


def test_without_a_type_the_organization_sizes_the_sandbox() -> None:
    limits = _resources(None, ORG)
    assert (limits.cpu_cores, limits.memory_limit) == (2.0, "4Gi")


def test_the_sandbox_type_sizes_it_when_one_is_chosen() -> None:
    heavy = SandboxEntry(
        id="t", org_id="o", name="heavy", image="img", cpu_limit="8", memory_limit="16Gi"
    )
    limits = _resources(heavy, ORG)
    assert (limits.cpu_cores, limits.memory_limit) == (8.0, "16Gi")


def test_an_agent_has_no_resources_of_its_own() -> None:
    fields = AgentPersonaModel.model_fields
    assert (
        "cpu_limit" not in fields and "memory_limit" not in fields and "sandbox_image" not in fields
    )
