"""What a new organization starts with: instruction skills for everyday engineering work, and
mission templates (created disabled) for recurring maintenance.

Starter skills are visible to every member unless the organization turns them off
(``general.starter_skills``); an organization or member skill with the same name wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StarterSkill:
    name: str
    description: str
    body: str


@dataclass(frozen=True)
class MissionPreset:
    id: str
    name: str
    description: str
    # The mission body (``routers/missions.MissionBody``) the preset fills in.
    mission: dict[str, Any]


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


_LABEL = "ai-fix"
_AUDIT = (
    "Audit the repository's dependencies: list the outdated ones (current, latest, how far "
    "behind) and any with known vulnerabilities (the package manager's audit command). Rank "
    "them by risk and effort, and recommend what to upgrade first. Do not change anything."
)

# Starting points for usual missions: each fills the whole form (triggers, agent, instructions,
# deliveries); the person picks the repository, channel or URL and saves. Kept in code so they
# follow the platform's release, never stored.
MISSION_PRESETS: tuple[MissionPreset, ...] = (
    MissionPreset(
        "pr-review",
        "Pull request review",
        "Reviews every pull request and its new commits, and posts one review with inline "
        "comments. Read-only.",
        {
            "name": "Pull request review",
            "instructions": "Focus on correctness, security and missing tests; skip style nits.",
            "triggers": [{"source": "github_pr"}, {"source": "github_issue_comment"}],
            "deliveries": [{"kind": "github_review"}],
            "mode": "review",
            "permission_mode": "plan",
        },
    ),
    MissionPreset(
        "ci-fixer",
        "CI failure fixer",
        "When a workflow fails on a branch, finds the cause in its logs, pushes a fix and "
        "follows the checks.",
        {
            "name": "CI failure fixer",
            "instructions": "Fix the cause, not the symptom; never disable a test to make it pass.",
            "triggers": [{"source": "github_workflow_failed"}],
            "deliveries": [{"kind": "session"}],
            "auto_fix": True,
        },
    ),
    MissionPreset(
        "issue-to-pr",
        f"Issue labelled {_LABEL} → pull request",
        f"Takes an issue labelled `{_LABEL}`, does the work on a branch and opens a pull "
        "request, then comments on the issue with what it did.",
        {
            "name": "Issues to pull requests",
            "instructions": "Keep the change small and tested; ask in the issue when unsure.",
            "triggers": [{"source": "github_issue_label", "filter": {"labels": [_LABEL]}}],
            "deliveries": [{"kind": "github_comment"}],
            "auto_fix": True,
        },
    ),
    MissionPreset(
        "dependency-audit",
        "Weekly dependency audit → Slack",
        "Every Monday morning, lists outdated and vulnerable dependencies and posts the summary "
        "in a channel. Changes nothing.",
        {
            "name": "Weekly dependency audit",
            "instructions": _AUDIT,
            "triggers": [{"source": "schedule", "schedule": "0 9 * * 1", "timezone": "UTC"}],
            "deliveries": [{"kind": "chat"}],
            "permission_mode": "plan",
        },
    ),
    MissionPreset(
        "standup-digest",
        "Daily engineering digest → Slack",
        "Each weekday, summarises yesterday's merged pull requests, failing checks and open "
        "reviews in a channel.",
        {
            "name": "Daily engineering digest",
            "instructions": (
                "Summarise the last working day of the repository: pull requests merged (one "
                "line each), checks failing on the default branch, and reviews waiting for more "
                "than a day. Under 15 lines; link each item."
            ),
            "triggers": [{"source": "schedule", "schedule": "0 8 * * 1-5", "timezone": "UTC"}],
            "deliveries": [{"kind": "chat"}],
            "permission_mode": "plan",
        },
    ),
    MissionPreset(
        "alert-triage",
        "Alert triage → webhook",
        "Takes an alert posted to the mission's webhook, finds the likely cause in the code and "
        "recent changes, and sends a structured verdict to your incident tool.",
        {
            "name": "Alert triage",
            "instructions": (
                "Triage the alert: find the likely cause in the code and the recent changes, "
                "say how sure you are, and what to do first. Change nothing."
            ),
            "triggers": [{"source": "webhook"}],
            "deliveries": [
                {
                    "kind": "webhook",
                    "output_schema": {
                        "type": "object",
                        "properties": {
                            "severity": {"enum": ["low", "medium", "high", "critical"]},
                            "likely_cause": {"type": "string"},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "next_step": {"type": "string"},
                        },
                        "required": ["severity", "likely_cause", "confidence", "next_step"],
                    },
                }
            ],
            "permission_mode": "plan",
        },
    ),
)
