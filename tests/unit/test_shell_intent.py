"""Shell commands that do what a dedicated tool does are told so; the shell's own work is not."""

from __future__ import annotations

from itertools import pairwise

import pytest
from agent_core.tools.shell_intent import (
    TIP_AFTER,
    TIP_EVERY,
    ShellIntent,
    shell_intents,
    should_tip,
    tip_for,
)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("grep -rn 'func Foo' ticket/", {ShellIntent.SEARCH}),
        ("rg 'TODO' src", {ShellIntent.SEARCH}),
        ("grep -n pattern service/core/ticket.go", {ShellIntent.SEARCH}),
        ("cat service/core/ticket.go", {ShellIntent.READ}),
        ("head -50 README.md", {ShellIntent.READ}),
        ("sed -n '10,40p' service/core/ticket.go", {ShellIntent.READ}),
        ("find . -name '*.go' -path './ticket/*'", {ShellIntent.LIST}),
        ("ls -R service", {ShellIntent.LIST}),
        ("sed -i 's/a/b/' ticket.go", {ShellIntent.EDIT}),
        ("echo 'x' > notes.txt", {ShellIntent.EDIT}),
        ("cat > service/new.go <<'EOF'\npackage core\nEOF", {ShellIntent.EDIT}),
        (
            "echo === A ===; grep -n foo a.go; sed -n '1,20p' a.go",
            {ShellIntent.SEARCH, ShellIntent.READ},
        ),
    ],
)
def test_a_command_that_a_dedicated_tool_covers_is_recognised(
    command: str, expected: set[ShellIntent]
) -> None:
    assert shell_intents(command) == expected


@pytest.mark.parametrize(
    "command",
    [
        "go test ./... 2>&1 | tail -40",
        "git log --oneline | head -20",
        "make build && make test",
        "kubectl get pods | grep Running",
        "cat",  # reads stdin
        "grep pattern",  # reads stdin
        "echo done",
        "echo 'a; grep x y' ",
        "ls",
        "ls -la service",
        "find . -type d",
        "go build ./... > /dev/null",
        "docker logs app 2>&1 | head",
    ],
)
def test_the_shells_own_work_is_never_flagged(command: str) -> None:
    assert shell_intents(command) == frozenset()


def test_a_tip_names_the_most_useful_replacement() -> None:
    both = frozenset({ShellIntent.READ, ShellIntent.SEARCH})

    assert tip_for(both) is not None and "grep_search" in tip_for(both)  # type: ignore[operator]
    assert "file_read" in (tip_for(frozenset({ShellIntent.READ})) or "")
    assert tip_for(frozenset()) is None


def test_tips_start_after_a_couple_of_misuses_and_then_come_now_and_then() -> None:
    tipped = [n for n in range(1, 20) if should_tip(n)]

    assert tipped[0] == TIP_AFTER
    assert all(b - a == TIP_EVERY for a, b in pairwise(tipped))
    assert not should_tip(1)


async def test_the_second_shell_search_of_a_turn_gets_a_tip_in_its_result() -> None:
    from tests.turn_harness import run_turn

    outcome = await run_turn(
        "org_shell_tip",
        steps=[
            (
                "think_and_call",
                "",
                "bash_exec",
                {"command": "grep -rn alpha src", "cwd": "/workspace"},
            ),
            (
                "think_and_call",
                "",
                "bash_exec",
                {"command": "grep -rn beta src", "cwd": "/workspace"},
            ),
            ("say", "Done."),
        ],
    )

    last_step = [r for r in outcome.model.requests if r.tools][-1]
    results = [m.content or "" for m in last_step.messages if m.role == "tool"]
    assert len(results) == 2
    assert "[Tip:" not in results[0], "the first one is free"
    assert "[Tip: `grep_search`" in results[1]
