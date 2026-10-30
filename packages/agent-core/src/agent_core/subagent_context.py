"""What a child starts with and what it may reach: its instructions, what it inherits from the
parent (each piece bounded), its ``report`` tool, and the gates it may or may not raise."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from agent_core.context import ExecutionContext
from agent_core.subagent_roles import EDIT_OR_COMMAND_TOOLS
from agent_core.subagent_scope import SubagentFrame
from agent_core.tools.project_instructions import instruction_paths, read_instructions
from specifications.interfaces.agent import (
    FINDING_SEVERITIES,
    ApprovalDetails,
    SkillInstructions,
    SubagentConfig,
    SubagentTaskRequest,
    TurnCallbacks,
)
from specifications.interfaces.llm import ToolDefinition
from specifications.interfaces.sandbox import SandboxDriverProtocol

# What a child inherits rides in its system prompt for every one of its steps.
MEMORY_CHARS = 2_000
CHECKLIST_CHARS = 2_000
SKILL_CHARS = 6_000
MAX_SKILLS = 3
REFUSED_GATE = (
    "Refused: this subagent may not ask the person anything (its role does not allow it). Do "
    "not do what needed the approval; finish what you can and put the decision in the "
    "`open_questions` of your report, for the main agent to take."
)

REPORT_TOOL = ToolDefinition(
    name="report",
    description=(
        "End your work: give the main agent your result. Call it once, last; nothing you do "
        "after it is seen."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["completed", "partial", "blocked"],
                "description": "completed: done; partial: some of it; blocked: could not proceed.",
            },
            "summary": {"type": "string", "description": "What you found or did, to act on."},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "line": {"type": "integer"},
                        "severity": {"type": "string", "enum": list(FINDING_SEVERITIES)},
                        "evidence": {"type": "string"},
                    },
                    "required": ["evidence"],
                },
            },
            "open_questions": {"type": "array", "items": {"type": "string"}},
            "next_steps": {"type": "array", "items": {"type": "string"}},
            "data": {"type": "object", "description": "Only when your instructions ask for it."},
        },
        "required": ["status", "summary"],
    },
)


def report_tool_for(config: SubagentConfig) -> ToolDefinition:
    """The ``report`` tool, with the role's output schema as the shape of ``data``."""
    if not config.output_schema:
        return REPORT_TOOL
    schema = json.loads(json.dumps(REPORT_TOOL.parameters_json_schema))
    schema["properties"]["data"] = config.output_schema
    schema["required"] = ["status", "summary", "data"]
    return ToolDefinition(REPORT_TOOL.name, REPORT_TOOL.description, schema)


def workspace_brief(permitted: set[str], active_repository: str, isolated: bool) -> str:
    """How the sandbox works, for a delegate that starts without the main turn's context."""
    lines = [
        "Workspace: repositories are cloned into /workspace/<name>.",
        (
            f"The active repository is {active_repository}: relative paths and commands use it."
            if active_repository
            else "No repository is active: use absolute /workspace/... paths."
        ),
        "Each bash_exec runs in a fresh shell in the folder given as `cwd` (never start a "
        "command with `cd`; exports do not carry over).",
        "Tool output is clamped: search (grep_search, glob_files, code_search) before reading, "
        "and read large files by line range.",
    ]
    if isolated:
        lines.append(
            "You work in a tree of your own on your own branch: your changes do not touch the "
            "main agent's tree until it merges them. Do not commit, push or switch branches."
        )
    if not permitted & EDIT_OR_COMMAND_TOOLS:
        lines.append(
            "Your tools are read-only: do not try to change anything. Report what you found; "
            "the main agent makes the changes."
        )
    return "\n".join(lines)


def _clamp(text: str, limit: int) -> str:
    text = text.strip()
    return (
        text if len(text) <= limit else f"{text[:limit]}\n[... {len(text) - limit} characters cut]"
    )


def _checklist_section(checklist: list[dict[str, Any]] | None) -> str | None:
    if not checklist:
        return None
    lines = [f"- [{t.get('status', 'pending')}] {t.get('title', '')}" for t in checklist]
    return (
        "The main agent's checklist (read-only: you work on your task, not on this list):\n"
        + _clamp("\n".join(lines), CHECKLIST_CHARS)
    )


async def _skill_sections(callbacks: TurnCallbacks, names: list[str]) -> list[str]:
    if callbacks.execute_skill is None:
        return []
    sections: list[str] = []
    for name in names[:MAX_SKILLS]:
        try:
            loaded = await callbacks.execute_skill(name, {})
        except Exception:
            continue
        if isinstance(loaded, SkillInstructions):
            sections.append(
                f"Skill `{loaded.name}` (follow it):\n{_clamp(loaded.body, SKILL_CHARS)}"
            )
    return sections


async def inherited_sections(
    config: SubagentConfig,
    request: SubagentTaskRequest,
    parent: ExecutionContext | None,
    driver: SandboxDriverProtocol | None,
    session_id: str,
) -> list[str]:
    """What the child inherits from the parent, each part clamped: the repository's AGENTS.md,
    memories relevant to the task, the parent's checklist, and the skills the parent named."""
    if parent is None:
        return []
    sections: list[str] = []
    inherit = set(config.inherit)
    if "agents_md" in inherit and driver is not None and parent.active_repository:
        if text := await read_instructions(
            driver, session_id, instruction_paths(parent.active_repository)
        ):
            sections.append(text.strip())
    if "memory" in inherit and parent.callbacks.subagent_memories is not None:
        try:
            memories = await parent.callbacks.subagent_memories(request.task)
        except Exception:
            memories = ""
        if memories.strip():
            sections.append(
                f"What the organization remembers that may matter:\n{_clamp(memories, MEMORY_CHARS)}"
            )
    if "checklist" in inherit and (checklist := _checklist_section(parent.checklist)):
        sections.append(checklist)
    if request.skills:
        sections += await _skill_sections(parent.callbacks, request.skills)
    return sections


def _ignore_diff(_: Any) -> None:
    return None


def child_callbacks(
    callbacks: TurnCallbacks, frame: SubagentFrame, *, isolated: bool
) -> TurnCallbacks:
    """The parent's collaborators, as a child may use them.

    A child never records anything on the session for later turns (its loaded tools, guidance,
    active repository or variables would replace the parent's), and a role that may not ask the
    person has its approvals and questions refused rather than raised. An isolated child's
    edits are in its own tree: they reach the session's changes only when merged.
    """

    async def refuse_question(*_: Any) -> str:
        return REFUSED_GATE

    async def refuse_approval(
        action_type: str, description: str, risk_level: str, details: ApprovalDetails | None = None
    ) -> str:
        return REFUSED_GATE

    child = replace(
        callbacks,
        rename=None,
        active_repository_chosen=None,
        tools_loaded=None,
        guidance_read=None,
        skills_read=None,
        agent_env_changed=None,
        plan=None,
        subagent=None,
        diff=_ignore_diff if isolated else callbacks.diff,
    )
    if not frame.can_ask_user:
        child = replace(child, question=refuse_question, approval=refuse_approval)
    return child
