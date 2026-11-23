"""What a repository offers: every skill without a manifest, grouped by plugin with one."""

from __future__ import annotations

from api.services.source_catalogue import build_catalogue, parse_manifest

from tests.support.fake_source import MARKETPLACE, FakeReader, FakeRepository, skill_md


def _repo(files: dict[str, str | bytes]) -> FakeRepository:
    repo = FakeRepository()
    repo.commit(files, executable=("teams/data/bq-envs/scripts/bq-envs",))
    return repo


async def test_without_a_manifest_every_skill_is_offered_on_its_own() -> None:
    repo = _repo(
        {
            "README.md": "# Skills",
            "review/SKILL.md": skill_md("review", "Review a diff"),
            "deep/nested/deploy/SKILL.md": skill_md("deploy", "Deploy the app"),
            "deep/nested/deploy/scripts/run.sh": "echo deploy",
        }
    )

    catalogue, folders = await build_catalogue(FakeReader(repo), repo.head)

    assert not catalogue.has_manifest and catalogue.plugins == ()
    assert [(e.path, e.name, e.plugins) for e in catalogue.entries] == [
        ("deep/nested/deploy", "deploy", ()),
        ("review", "review", ()),
    ]
    assert catalogue.entry("deep/nested/deploy").files == ("scripts/run.sh",)  # type: ignore[union-attr]
    assert set(folders) == {"deep/nested/deploy", "review"}


async def test_a_marketplace_groups_skills_by_plugin_and_keeps_the_rest() -> None:
    repo = _repo(
        {
            ".claude-plugin/marketplace.json": MARKETPLACE,
            "teams/data/bq-envs/SKILL.md": skill_md("bq-envs", "BigQuery environments"),
            "teams/data/bq-envs/scripts/bq-envs": "#!/bin/sh\necho bq",
            "teams/backend/signoz/SKILL.md": skill_md("signoz", "Query SigNoz"),
            "teams/backend/signoz/queries.md": "# Queries",
            "misc/loose/SKILL.md": skill_md("loose", "Not in any plugin"),
        }
    )

    catalogue, _ = await build_catalogue(FakeReader(repo), repo.head)

    assert catalogue.has_manifest
    assert {p.name: p.entries for p in catalogue.plugins} == {
        "data": ("teams/data/bq-envs",),
        "backend": ("teams/backend/signoz",),
    }
    assert catalogue.entry("misc/loose").plugins == ()  # type: ignore[union-attr]
    assert catalogue.entry("teams/data/bq-envs").plugins == ("data",)  # type: ignore[union-attr]
    assert any("lives in another repository" in note for note in catalogue.notes)


async def test_a_skill_folder_holds_what_is_nested_in_it() -> None:
    repo = _repo(
        {
            "outer/SKILL.md": skill_md("outer", "Outer"),
            "outer/examples/inner/SKILL.md": skill_md("inner", "An example inside outer"),
        }
    )

    catalogue, _ = await build_catalogue(FakeReader(repo), repo.head)

    assert [e.path for e in catalogue.entries] == ["outer"]
    assert catalogue.entries[0].files == ("examples/inner/SKILL.md",)


async def test_an_invalid_skill_is_listed_with_its_reason() -> None:
    repo = _repo({"broken/SKILL.md": "no front matter"})

    catalogue, _ = await build_catalogue(FakeReader(repo), repo.head)

    assert "front matter" in catalogue.entries[0].problem


async def test_an_unchanged_skill_md_is_not_read_again_and_a_changed_file_moves_the_digest() -> (
    None
):
    repo = _repo(
        {
            "review/SKILL.md": skill_md("review", "Review a diff"),
            "review/checklist.md": "- tests",
        }
    )
    first, _ = await build_catalogue(FakeReader(repo), repo.head)
    repo.reads.clear()
    repo.change("review/checklist.md", "- tests\n- docs")

    second, _ = await build_catalogue(FakeReader(repo), repo.head, first)

    assert repo.reads == []
    assert second.entries[0].name == "review"
    assert second.entries[0].digest != first.entries[0].digest


def test_an_unreadable_manifest_offers_skills_on_their_own() -> None:
    specs, notes = parse_manifest("{not json")

    assert specs == [] and "not valid JSON" in notes[0]


def test_a_plugin_pointing_outside_the_repository_is_left_out() -> None:
    specs, notes = parse_manifest('{"plugins": [{"name": "x", "source": "../elsewhere"}]}')

    assert specs == [] and "outside the repository" in notes[0]


def test_a_plugin_without_a_skills_list_uses_its_skills_folder() -> None:
    specs, _ = parse_manifest('{"plugins": [{"name": "tools", "source": "./plugins/tools"}]}')

    assert specs[0].folders == ("plugins/tools/skills",)
