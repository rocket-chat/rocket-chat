"""A skill from a repository brings its files: loading it puts them in the sandbox, its
instructions say where, and an unchanged skill is not reported as edited at the next turn."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from api.services.compaction import InMemoryCompactionStore
from api.services.external_sources import external_source_store
from api.services.skill_files import MAX_LISTED_FILES, files_note, put_in_sandbox
from api.services.source_sync import import_entries, synchronise
from api.services.transcript import InMemoryTranscriptStore
from api.store import InMemorySessionStore
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.external_sources import ExternalSource, LinkedFile
from specifications.interfaces.ownership import Owner
from tests.support.fake_source import FakeRepository, FakeSourceAccess, skill_md
from tests.turn_harness import run_turn

FOLDER = "/workspace/.rocket/skills/bq-envs"


async def _imported(org: str) -> None:
    repo = FakeRepository()
    repo.commit(
        {
            "bq-envs/SKILL.md": skill_md(
                "bq-envs", "BigQuery environments", "Run scripts/bq-envs."
            ),
            "bq-envs/scripts/bq-envs": "#!/bin/sh\necho bq",
            "bq-envs/reference.md": "# Datasets",
        },
        executable=("bq-envs/scripts/bq-envs",),
    )
    state = SimpleNamespace(db_manager=None, source_access=FakeSourceAccess(repo))
    source = ExternalSource(
        id=f"src_{org}", org_id=org, name="Skills", repository=repo.repository, ref="main"
    )
    await external_source_store(None).save_source(source)
    await synchronise(state, source, force=True)
    stored = await external_source_store(None).get_source(org, source.id)
    assert stored is not None
    await import_entries(state, stored, ["bq-envs"], Owner("org"), merge=[], actor_user_id="u1")


def _tool_results(request: Any) -> list[str]:
    return [m.content or "" for m in request.messages if m.role == "tool"]


async def test_loading_the_skill_puts_its_files_in_the_sandbox_and_says_where() -> None:
    org = f"files_{uuid.uuid4().hex[:8]}"
    await _imported(org)
    driver = FakeSandboxDriver()

    outcome = await run_turn(
        org,
        driver=driver,
        script=[("execute_skill", {"skill_name": "bq-envs", "arguments": {}})],
    )

    written = driver.file_snapshot(f"session_{org}")
    assert any(path.endswith(".rocket/skills/bq-envs/scripts/bq-envs") for path in written)
    assert any(path.endswith(".rocket/skills/bq-envs/reference.md") for path in written)
    assert any(
        "chmod +x" in command and "scripts/bq-envs" in command for _, command in driver.exec_calls
    )
    results = [r for request in outcome.model.requests for r in _tool_results(request)]
    result = next(r for r in results if "Run scripts/bq-envs." in r)
    assert "Run scripts/bq-envs." in result
    assert f"`{FOLDER}/`" in result and "scripts/bq-envs (executable)" in result


async def test_an_unchanged_skill_with_files_stays_in_effect_at_the_next_turn() -> None:
    org = f"files_{uuid.uuid4().hex[:8]}"
    await _imported(org)
    shared: dict[str, Any] = {
        "store": InMemorySessionStore(),
        "transcript_store": InMemoryTranscriptStore(),
        "compaction_store": InMemoryCompactionStore(),
    }
    await run_turn(
        org, script=[("execute_skill", {"skill_name": "bq-envs", "arguments": {}})], **shared
    )

    again = await run_turn(org, prompt="Carry on.", **shared)

    note = "\n".join(m.content or "" for m in again.model.requests[0].messages)
    assert "changed since" not in note and "out of date" not in note


def test_the_note_names_a_bounded_number_of_files() -> None:
    many = [LinkedFile(f"refs/{i}.md", "x") for i in range(MAX_LISTED_FILES + 5)]

    note = files_note("big", many)

    assert note.count("refs/") == MAX_LISTED_FILES
    assert "and 5 more" in note
    assert files_note("none", []) == ""


async def test_a_path_leaving_the_skill_folder_is_not_written() -> None:
    driver = FakeSandboxDriver()

    await put_in_sandbox(
        driver, "s1", "evil", [LinkedFile("../../etc/profile", "x"), LinkedFile("ok.md", "y")]
    )

    written = driver.file_snapshot("s1")
    assert not any("etc/profile" in path for path in written)
    assert any(path.endswith(".rocket/skills/evil/ok.md") for path in written)
