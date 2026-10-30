"""The subagent roles a turn may delegate to: the built-in ones, the index the agent reads, and
the roles a repository defines in ``.rocket/agents/*.md``.

Every piece of this the model reads is bounded: the index rides in the cached system prompt of
every step, and a repository's role files are text from the repository, like its AGENTS.md.
"""

from __future__ import annotations

import copy
import posixpath
import re
import shlex
from collections.abc import Iterable, Mapping
from typing import Any

from agent_core.tools.project_instructions import clamp_instructions
from specifications.interfaces.agent import (
    DEFAULT_SUBAGENT_COST_USD,
    DEFAULT_SUBAGENT_STEPS,
    SUBAGENT_ISOLATIONS,
    SUBAGENT_TIERS,
    SubagentConfig,
)
from specifications.interfaces.llm import ToolDefinition
from specifications.interfaces.sandbox import SandboxDriverProtocol

READ_ONLY_TOOLS = ("file_read", "grep_search", "glob_files", "list_dir", "code_search")
WEB_TOOLS = ("web_search", "web_fetch")
GIT_READ_TOOLS = ("git_diff", "git_status", "git_log")
EDIT_TOOLS = ("file_write", "file_edit", "apply_patch", "apply_diff_file")
COMMAND_TOOLS = ("bash_exec", "run_tests")
EDIT_OR_COMMAND_TOOLS = frozenset((*EDIT_TOOLS, *COMMAND_TOOLS))

_REPORT_FORMAT = (
    "End by calling `report` once: `status` (completed, partial or blocked), a `summary` the main "
    "agent can act on, `findings` (file, line, severity, evidence) for anything you found, and "
    "`open_questions` / `next_steps` for what you could not settle."
)
_SEVERITY_SCALE = (
    "Severity: critical (exploitable now, data or control at stake), high (exploitable with "
    "little effort), medium (needs unusual conditions), low (hardening), info (worth knowing)."
)


def _role(
    role_id: str,
    name: str,
    description: str,
    prompt: str,
    tools: Iterable[str],
    *,
    tier: str = "default",
    isolation: str = "none",
) -> SubagentConfig:
    return SubagentConfig(
        id=role_id,
        name=name,
        role_title=name,
        description=description,
        system_prompt=f"{prompt}\n\n{_REPORT_FORMAT}",
        tier=tier,
        isolation=isolation,
        whitelisted_tools=list(tools),
        max_turns=DEFAULT_SUBAGENT_STEPS,
        max_cost_usd=DEFAULT_SUBAGENT_COST_USD,
    )


DEFAULT_SUBAGENT_CONFIGS: dict[str, SubagentConfig] = {
    "explorer": _role(
        "explorer",
        "Explorer",
        "Finds code fast: where something is defined, handled or used. Not for changes or "
        "judgements.",
        "You are an explorer subagent. Find where things are in the code: search before you "
        "read, read only the lines that matter, and answer with file paths and line numbers.",
        READ_ONLY_TOOLS,
        tier="fast",
    ),
    "researcher": _role(
        "researcher",
        "Researcher",
        "Answers questions from documentation, the web and the code (an API, a library, an "
        "error). Not for changes.",
        "You are a researcher subagent. Answer the question from primary sources (official "
        "documentation, the library's code, the repository) and cite where each fact comes "
        "from. Never change files.",
        (*READ_ONLY_TOOLS, *WEB_TOOLS),
        tier="fast",
    ),
    "reviewer": _role(
        "reviewer",
        "Reviewer",
        "Reviews the current diff: correctness, edge cases, tests, readability. Reports "
        "findings by severity; changes nothing.",
        "You are a code reviewer subagent. Read the current diff (git_diff) and the code around "
        "it. Look for bugs, unhandled cases, missing tests and unclear code; report each as a "
        f"finding with the line and the evidence. {_SEVERITY_SCALE}",
        (*READ_ONLY_TOOLS, *GIT_READ_TOOLS),
    ),
    "qa_verifier": _role(
        "qa_verifier",
        "QA verifier",
        "Runs the tests in its own worktree and reports pass or fail with the evidence. For "
        "checking a change, not for fixing it.",
        "You are a QA verifier subagent working in your own worktree of the repository (with "
        "its uncommitted changes). Run the relevant tests (run_tests, or bash_exec for what it "
        "does not cover), read failures in the code, and report which passed and which failed, "
        "each failure with its assertion, output and likely cause. Do not fix the code.",
        ("run_tests", "bash_exec", *GIT_READ_TOOLS, *READ_ONLY_TOOLS),
        isolation="worktree",
    ),
    "security_auditor": _role(
        "security_auditor",
        "Security auditor",
        "Audits code for vulnerabilities: entry points, untrusted input, secrets, access "
        "control. Reports findings by severity; changes nothing.",
        "You are a security auditor subagent. Work in this order: (1) a short threat model: "
        "what the code protects and from whom; (2) the entry points: requests, messages, "
        "files, commands, anything an outsider controls; (3) the data flow from each entry "
        "point to where it is used (queries, shell, file paths, templates, network calls, "
        "authorization checks); (4) what can go wrong on each flow (injection, broken access "
        "control, secrets in code or logs, unsafe deserialization, server-side requests to "
        f"attacker-chosen hosts). {_SEVERITY_SCALE} Give each finding the file, line and the "
        "evidence, and a fix.",
        (*READ_ONLY_TOOLS, *GIT_READ_TOOLS),
    ),
    "implementer": _role(
        "implementer",
        "Implementer",
        "Writes code in its own worktree for one well-defined piece of a larger change (one "
        "module or set of files). The main agent merges its branch.",
        "You are an implementer subagent working in your own git worktree on your own branch. "
        "Make the change you were given and nothing else, run the relevant tests, and leave "
        "the tree as you want it merged: the main agent reviews and merges your branch.",
        (*READ_ONLY_TOOLS, *EDIT_TOOLS, *COMMAND_TOOLS, *GIT_READ_TOOLS),
        isolation="worktree",
    ),
}

# The roles index in the delegation tools' descriptions: a few lines per role, bounded however
# many roles an organization or a repository defines and however long their descriptions are.
MAX_ROLES_LISTED = 24
ROLE_LINE_CHARS = 180
ROLES_INDEX_MAX_CHARS = 3_500
# Above this many roles the ``role`` parameter is a plain string: an enum of hundreds of ids
# would cost every request more than the index it replaces.
MAX_ROLE_ENUM = 48


def enabled_roles(configs: Mapping[str, SubagentConfig]) -> list[SubagentConfig]:
    return sorted((c for c in configs.values() if c.enabled), key=lambda c: c.id)


def _role_line(config: SubagentConfig) -> str:
    traits = [config.tier if config.tier in SUBAGENT_TIERS else "default"]
    if config.isolation != "none":
        traits.append(config.isolation)
    if config.source == "repository":
        traits.append("from the repository")
    description = " ".join((config.description or config.role_title).split())
    line = f"  - `{config.id}` ({', '.join(traits)}): {description}"
    return line if len(line) <= ROLE_LINE_CHARS else line[: ROLE_LINE_CHARS - 1] + "…"


def render_roles_index(configs: Mapping[str, SubagentConfig]) -> list[str]:
    """One line per enabled role (id, tier, isolation, when to use it), within the caps."""
    roles = enabled_roles(configs)
    lines: list[str] = []
    used = 0
    for config in roles[:MAX_ROLES_LISTED]:
        line = _role_line(config)
        if used + len(line) + 1 > ROLES_INDEX_MAX_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    hidden = len(roles) - len(lines)
    if hidden > 0:
        lines.append(f"  - ... and {hidden} more roles (their ids are in `delegate_subagent`).")
    return lines


def _with_enum(schema: dict[str, Any], path: tuple[str, ...], ids: list[str]) -> None:
    node: Any = schema
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
        if node is None:
            return
    if isinstance(node, dict):
        node["enum"] = ids


def with_role_enum(
    definition: ToolDefinition, configs: Mapping[str, SubagentConfig]
) -> ToolDefinition:
    """``definition`` with the roles index in its description and its ``role`` parameter
    limited to the turn's enabled roles (the index rides with the tool, so a deferred
    delegation tool costs the fixed prompt nothing)."""
    roles = enabled_roles(configs)
    if not roles:
        return definition
    description = "\n".join([definition.description, "Roles:", *render_roles_index(configs)])
    schema = copy.deepcopy(definition.parameters_json_schema)
    ids = [c.id for c in roles]
    if len(ids) <= MAX_ROLE_ENUM:
        _with_enum(schema, ("properties", "role"), ids)
        _with_enum(schema, ("properties", "delegations", "items", "properties", "role"), ids)
        _with_enum(schema, ("$defs", "Delegation", "properties", "role"), ids)
    return ToolDefinition(definition.name, description, schema)


# --- roles from the repository (``.rocket/agents/*.md``) ----------------------------------------

REPOSITORY_ROLES_DIR = ".rocket/agents"
MAX_REPOSITORY_ROLES = 12
MAX_ROLE_DESCRIPTION_CHARS = 300
_ROLE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)(.*)\Z", re.DOTALL)


def _front_matter(text: str) -> tuple[dict[str, Any], str] | None:
    """The ``key: value`` front matter (inline ``[a, b]`` and ``- a`` lists) and the body."""
    match = _FRONT_MATTER.match(text.lstrip("\ufeff"))
    if match is None:
        return None
    fields: dict[str, Any] = {}
    current: str | None = None
    for raw in match.group(1).splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("- ") and current is not None:
            items = fields.setdefault(current, [])
            if isinstance(items, list):
                items.append(line.lstrip()[2:].strip().strip("'\""))
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        current = key.strip().lower()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            fields[current] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        else:
            fields[current] = value.strip("'\"") if value else []
    return fields, match.group(2).strip()


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


def _as_int(value: Any, default: int, ceiling: int) -> int:
    text = str(value or "").strip()
    return min(int(text), ceiling) if text.isdigit() else default


def parse_role_file(path: str, text: str) -> SubagentConfig | None:
    """A role from one ``.rocket/agents/<id>.md`` file, or ``None`` when it is not one.

    A repository's role is never more than an organization's could be: no sandbox of its own,
    no budget above the default, no right to ask the person or to delegate.
    """
    parsed = _front_matter(text)
    if parsed is None:
        return None
    fields, body = parsed
    if str(fields.get("kind") or "subagent").strip().lower() == "agent":
        return None  # an agent exported from the cockpit, not a subagent role
    stem = posixpath.splitext(posixpath.basename(path))[0].lower()
    # The cockpit's export names the role ``id`` and its display ``name``; a hand-written file
    # may name the role ``name`` and give a ``title``.
    role_id = str(fields.get("id") or fields.get("name") or stem).strip().lower()
    if not _ROLE_ID.match(role_id) or not body:
        return None
    shown = (
        fields.get("title")
        or fields.get("role_title")
        or (fields.get("name") if "id" in fields else "")
    )
    title = str(shown or role_id.replace("_", " ").title())
    description = " ".join(str(fields.get("description") or "").split())
    tier = str(fields.get("tier") or "default").strip().lower()
    isolation = str(fields.get("isolation") or "none").strip().lower()
    tools = _as_list(fields.get("tools") or fields.get("whitelisted_tools")) or list(
        READ_ONLY_TOOLS
    )
    steps = fields.get("max_steps") or fields.get("max_turns")
    return SubagentConfig(
        id=role_id,
        name=title,
        role_title=title,
        description=description[:MAX_ROLE_DESCRIPTION_CHARS],
        system_prompt=f"{clamp_instructions(body, path)}\n\n{_REPORT_FORMAT}",
        tier=tier if tier in SUBAGENT_TIERS else "default",
        isolation=isolation if isolation in SUBAGENT_ISOLATIONS[:2] else "none",
        whitelisted_tools=[t for t in tools if t != "*"],
        disallowed_tools=_as_list(fields.get("disallowed_tools")),
        max_turns=_as_int(steps, DEFAULT_SUBAGENT_STEPS, 200),
        max_cost_usd=DEFAULT_SUBAGENT_COST_USD,
        source="repository",
    )


async def load_repository_roles(
    driver: SandboxDriverProtocol, session_id: str, folder: str
) -> dict[str, SubagentConfig]:
    """The roles ``folder`` defines in ``.rocket/agents/*.md`` (best effort, at most a dozen)."""
    directory = posixpath.join(folder, REPOSITORY_ROLES_DIR)
    listing = await driver.exec_command(
        session_id,
        f"ls -1 {shlex.quote(directory)}/*.md 2>/dev/null | head -n {MAX_REPOSITORY_ROLES}",
        timeout_seconds=15,
    )
    roles: dict[str, SubagentConfig] = {}
    for path in (listing.stdout or "").splitlines():
        try:
            text = await driver.read_file(session_id, path.strip())
        except Exception:
            continue
        role = parse_role_file(path.strip(), text)
        if role is not None:
            roles.setdefault(role.id, role)
    return roles


def merge_repository_roles(
    configured: Mapping[str, SubagentConfig], repository: Mapping[str, SubagentConfig]
) -> dict[str, SubagentConfig]:
    """The organization's roles, plus the repository's whose id no organization role takes."""
    merged = dict(configured)
    for role_id, role in repository.items():
        merged.setdefault(role_id, role)
    return merged
