"""What a new organization starts with: instruction skills for everyday engineering work, and
mission templates (created disabled) for recurring maintenance.

Starter skills are visible to every member unless the organization turns them off
(``general.starter_skills``); an organization or member skill with the same name wins.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StarterSkill:
    name: str
    description: str
    body: str


@dataclass(frozen=True)
class MissionTemplate:
    id: str
    name: str
    description: str
    prompt: str
    cron: str
    permission_mode: str


STARTER_SKILLS: tuple[StarterSkill, ...] = (
    StarterSkill(
        "review-diff",
        "Review the current change before it is published.",
        "Review the working diff (git_diff) as a reviewer would, before anything is pushed.\n"
        "1. Read the whole diff, then the code around each hunk and the tests that cover it.\n"
        "2. List problems ranked: blocking (bugs, data loss, security, broken contracts), "
        "should-fix (missing tests, unhandled cases, unclear names), nits.\n"
        "3. Give each the file and line, why it is wrong and the fix.\n"
        "4. Fix the blocking and should-fix items, run the tests again, and say what you changed.",
    ),
    StarterSkill(
        "write-tests",
        "Add tests for a change or an untested module.",
        "Write tests for $ARGUMENTS (or the current change when nothing is named).\n"
        "1. Find the project's test framework, layout and helpers; follow them exactly.\n"
        "2. Cover the behaviour, not the implementation: the main path, the edge cases, the "
        "errors.\n"
        "3. Make each test fail first when you can (break the code briefly) so it tests something.\n"
        "4. Run the whole affected suite and report what you added and what it covers.",
    ),
    StarterSkill(
        "fix-ci-failure",
        "Find and fix why the pull request's CI fails.",
        "Fix the failing CI of $ARGUMENTS (or of the current branch's pull request).\n"
        "1. Read the failing job's log (github_actions) and find the first real error.\n"
        "2. Reproduce it in the sandbox with the same command before changing anything.\n"
        "3. Fix the cause, not the symptom; never skip or weaken a test to make it pass.\n"
        "4. Run the command again, push, and wait_for ci_status on the new commit.",
    ),
    StarterSkill(
        "write-pr-description",
        "Write the pull request description from the change itself.",
        "Write a pull request description for the current branch.\n"
        "- What changed and why, in two or three sentences a reviewer reads first.\n"
        "- How it was tested (the commands you ran and their result).\n"
        "- Risks, follow-ups and anything the reviewer should look at closely.\n"
        "Base every line on the diff and the commits (git_diff, git_log), not on memory.",
    ),
    StarterSkill(
        "investigate-bug",
        "Find the cause of a bug before fixing it.",
        "Investigate $ARGUMENTS.\n"
        "1. Reproduce it: a failing test or a command that shows it.\n"
        "2. Narrow it down: read the code path, add logging or bisect (git log) until you can "
        "name the line and the reason.\n"
        "3. Explain the cause in one paragraph, then fix it and keep the reproduction as a "
        "regression test.",
    ),
    StarterSkill(
        "upgrade-dependency",
        "Upgrade a dependency safely.",
        "Upgrade $ARGUMENTS.\n"
        "1. Read its changelog between the current and the target version; list the breaking "
        "changes that touch this code.\n"
        "2. Upgrade with the project's package manager (lockfile included), adapt the code.\n"
        "3. Run the build and the whole test suite; report what changed and anything left to "
        "check by hand.",
    ),
)
STARTER_SKILL_NAMES: tuple[str, ...] = tuple(skill.name for skill in STARTER_SKILLS)
_BY_NAME = {skill.name: skill for skill in STARTER_SKILLS}


def starter_skill(name: str, enabled: list[str] | tuple[str, ...]) -> StarterSkill | None:
    return _BY_NAME.get(name) if name in enabled else None


MISSION_TEMPLATES: tuple[MissionTemplate, ...] = (
    MissionTemplate(
        "weekly-dependency-audit",
        "Weekly dependency audit",
        "Lists outdated and vulnerable dependencies; changes nothing.",
        "Audit the repository's dependencies: list the outdated ones (current, latest, how far "
        "behind) and any with known vulnerabilities (the package manager's audit command). Rank "
        "them by risk and effort, and recommend what to upgrade first. Do not change anything.",
        "0 9 * * 1",
        "plan",
    ),
    MissionTemplate(
        "flaky-test-triage",
        "Flaky-test triage",
        "Finds tests that failed and then passed on the same commit in CI this week.",
        "Look at this week's CI runs of the default branch (github_actions): find tests that "
        "failed and then passed on the same commit. For each, give how often it flaked, the "
        "failure message, and the likely cause from reading the test. Do not change code.",
        "0 8 * * 5",
        "plan",
    ),
    MissionTemplate(
        "stale-branch-report",
        "Stale-branch cleanup report",
        "Reports branches with no commit for 30 days and whether they were merged.",
        "List the repository's branches with no commit in the last 30 days: for each, the last "
        "commit date and author, whether it was merged, and its open pull request if any. "
        "Recommend which to delete. Delete nothing.",
        "0 9 1 * *",
        "plan",
    ),
)
