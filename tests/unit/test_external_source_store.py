"""The in-memory external source store: the behaviour the PostgreSQL one is held to as well
(tests/security/test_external_sources_rls.py)."""

from __future__ import annotations

from dataclasses import replace

from api.services.external_sources import (
    InMemoryExternalSourceStore,
    catalogue_from_json,
    catalogue_to_json,
)

from specifications.interfaces.external_sources import (
    Catalogue,
    CatalogueEntry,
    CataloguePlugin,
    ExternalSource,
    LinkedFile,
    SourceLink,
)


def _source(source_id: str = "src_1", ref: str = "main", next_sync_at: int = 0) -> ExternalSource:
    return ExternalSource(
        id=source_id,
        org_id="org_a",
        name="Skills",
        repository="Acme/Agent-Skills",
        ref=ref,
        next_sync_at=next_sync_at,
    )


def _link(object_id: str, source_id: str = "src_1", org: str = "org_a") -> SourceLink:
    return SourceLink(
        id=f"lnk_{object_id}",
        org_id=org,
        source_id=source_id,
        kind="skill",
        object_id=object_id,
        path=f"teams/data/{object_id}",
        files=[LinkedFile("scripts/run.sh", "echo", executable=True)],
    )


async def test_sources_and_links_stay_in_their_organization() -> None:
    store = InMemoryExternalSourceStore()
    await store.save_source(_source())
    await store.save_link(_link("skill-1"))

    assert await store.get_source("org_b", "src_1") is None
    assert await store.list_sources("org_b") == []
    assert await store.links_of_objects("org_b", "skill", ["skill-1"]) == {}
    assert await store.delete_source("org_b", "src_1") is False
    assert (
        (await store.links_of_objects("org_a", "skill", ["skill-1"]))["skill-1"].files[0].executable
    )


async def test_deleting_a_source_forgets_its_links() -> None:
    store = InMemoryExternalSourceStore()
    await store.save_source(_source())
    await store.save_link(_link("skill-1"))

    assert await store.delete_source("org_a", "src_1") is True
    assert await store.links_of_source("org_a", "src_1") == []


async def test_a_push_finds_the_sources_of_its_repository_whatever_the_case() -> None:
    store = InMemoryExternalSourceStore()
    await store.save_source(_source())
    await store.save_source(_source(source_id="src_2", ref="develop"))

    found = await store.sources_following("org_a", "acme/agent-skills", "main")

    assert [s.id for s in found] == ["src_1"]


async def test_a_due_source_is_claimed_once_and_moved_forward() -> None:
    store = InMemoryExternalSourceStore()
    await store.save_source(_source())

    first = await store.claim_due(1_000, 900)
    second = await store.claim_due(1_000, 900)

    assert [s.next_sync_at for s in first] == [1_900]
    assert second == []


async def test_recording_a_read_keeps_a_schedule_a_push_moved_meanwhile() -> None:
    store = InMemoryExternalSourceStore()
    source = await store.save_source(_source(next_sync_at=5_000))
    await store.schedule("org_a", source.id, 0)

    await store.record_read(replace(source, status="ok", last_commit="abc", next_sync_at=5_000))

    stored = await store.get_source("org_a", source.id)
    assert stored is not None and (stored.status, stored.last_commit) == ("ok", "abc")
    assert stored.next_sync_at == 0


def test_a_catalogue_survives_its_stored_form() -> None:
    catalogue = Catalogue(
        commit="c1",
        has_manifest=True,
        plugins=(CataloguePlugin("data", "Data team", ("teams/data/a",)),),
        entries=(
            CatalogueEntry(
                kind="skill",
                path="teams/data/a",
                name="a",
                plugins=("data",),
                files=("scripts/x",),
                document_id="d",
                digest="g",
            ),
        ),
        notes=("left out",),
    )

    assert catalogue_from_json(catalogue_to_json(catalogue)) == catalogue
    assert catalogue_from_json(None) is None
