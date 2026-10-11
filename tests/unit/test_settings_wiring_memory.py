"""Memory settings change what the agent can remember and recall."""

from __future__ import annotations

import hashlib
import time
from typing import Any

import pytest
from api.bootstrap.core import memory_policy_for
from api.services.memory import MemoryService
from api.services.settings import save_org_config


async def _hash_embed(text: str) -> list[float]:
    vector = [0.0] * 64
    vector[int(hashlib.sha256(text.strip().lower().encode()).hexdigest(), 16) % 64] = 1.0
    return vector


async def _service(org: str, embedder: Any = _hash_embed, **settings: Any) -> MemoryService:
    if settings:
        await save_org_config(None, org, "memory", settings, [])
    return MemoryService(None, policy=await memory_policy_for(None, org), embedder=embedder)


async def _remember(service: MemoryService, org: str, content: str, **kwargs: Any) -> Any:
    return await service.remember(org_id=org, user_id="u1", content=content, **kwargs)


@pytest.mark.setting("memory.enabled")
async def test_a_disabled_memory_remembers_and_recalls_nothing() -> None:
    org = "wire_mem_disabled"
    enabled = await _service(org)
    assert (await _remember(enabled, org, "uses tabs")).status == "created"

    off = await _service(org, enabled=False)

    assert (await _remember(off, org, "uses spaces")).status == "blocked"
    assert await off.recall(org_id=org, user_id="u1", query="uses tabs") == []


@pytest.mark.setting("memory.scope_allowlist")
async def test_a_scope_outside_the_allowlist_cannot_be_written() -> None:
    org = "wire_mem_scopes"
    service = await _service(org, scope_allowlist=["user"])

    assert (await _remember(service, org, "mine", scope="user")).status == "created"
    shared = await _remember(service, org, "for everyone", scope="org")
    assert shared.status == "blocked" and "scope" in (shared.reason or "")


@pytest.mark.setting("memory.kind_allowlist")
async def test_a_kind_outside_the_allowlist_cannot_be_written() -> None:
    org = "wire_mem_kinds"
    service = await _service(org, kind_allowlist=["fact"])

    assert (await _remember(service, org, "the db is postgres", kind="fact")).status == "created"
    other = await _remember(service, org, "prefers short answers", kind="preference")
    assert other.status == "blocked" and "kind" in (other.reason or "")


@pytest.mark.setting("memory.top_k")
async def test_recall_returns_at_most_top_k_memories_by_default() -> None:
    org = "wire_mem_top_k"
    service = await _service(org, top_k=2)
    for index in range(5):
        await _remember(service, org, f"fact number {index}")

    assert len(await service.recall(org_id=org, user_id="u1", query="fact")) == 2


def _near_duplicate_embedder() -> Any:
    """Two texts that are 0.9 similar to each other; everything else is unrelated."""
    vectors = {"alpha": [1.0, 0.0], "alpha, reworded": [0.9, 0.4358898943540674]}

    async def embed(text: str) -> list[float]:
        return vectors.get(text.strip().lower(), [0.0, 1.0])

    return embed


@pytest.mark.setting("memory.dedupe_similarity")
async def test_a_near_duplicate_updates_the_old_memory_only_above_the_threshold() -> None:
    strict_org, loose_org = "wire_mem_dedupe_strict", "wire_mem_dedupe_loose"

    strict = await _service(strict_org, _near_duplicate_embedder(), dedupe_similarity=0.99)
    await _remember(strict, strict_org, "alpha")
    assert (await _remember(strict, strict_org, "alpha, reworded")).status == "created"

    loose = await _service(loose_org, _near_duplicate_embedder(), dedupe_similarity=0.8)
    await _remember(loose, loose_org, "alpha")
    assert (await _remember(loose, loose_org, "alpha, reworded")).status == "updated"


@pytest.mark.setting("memory.decay_half_life_days")
async def test_the_half_life_decides_whether_an_old_memory_still_outranks_a_fresh_one() -> None:
    vectors = {"query": [1.0, 0.0], "old but exact": [1.0, 0.0], "fresh but looser": [0.8, 0.6]}

    async def embed(text: str) -> list[float]:
        return vectors.get(text.strip().lower(), [0.0, 1.0])

    async def first_recalled(org: str, half_life: float) -> str:
        service = await _service(org, embed, decay_half_life_days=half_life)
        old = (await _remember(service, org, "old but exact")).record
        await _remember(service, org, "fresh but looser")
        old.created_at = int(time.time()) - 200 * 86400
        recalled = await service.recall(org_id=org, user_id="u1", query="query")
        return recalled[0].content

    assert await first_recalled("wire_mem_decay_short", 10) == "fresh but looser"
    assert await first_recalled("wire_mem_decay_long", 100000) == "old but exact"
