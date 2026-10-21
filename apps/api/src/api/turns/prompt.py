"""The system prompt of a turn: who the agent is and what its environment looks like.

The stable part (identity, runtime facts, repository, committer) is byte-identical for a whole
session so the provider's prompt cache stays warm; the volatile part (which model is active)
goes after the cache boundary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

from api.auth.origin import public_base_url
from api.services.git_credentials import GitCredentialBundle
from api.services.github_watches import GithubWatchStore, pending_events_notice
from api.services.personas import DEFAULT_AGENT_PROMPT
from api.services.process_tracker import (
    REPORTED_METADATA_KEY,
    ProcessTracker,
    describe_for_agent,
    unreported_exits,
)
from api.services.project_instructions import load_project_instructions
from api.services.session_support import active_repository_of
from api.services.settings import effective_settings
from api.services.skills_lookup import list_visible_skills
from api.services.subagents import load_subagent_configs
from api.services.workspace_release import RELEASED_AT, released_notice
from api.turns.context import TurnContext
from specifications.interfaces.agent import GitPolicy, SystemPrompt, TurnPolicy
from specifications.interfaces.sandbox import SandboxStatus
from specifications.interfaces.session import SessionRecord

SANDBOX_TOOLS = (
    "bash_exec",
    "clone_repository",
    "file_read",
    "file_write",
    "file_edit",
    "apply_patch",
    "run_tests",
)
EDIT_TOOLS = ("file_edit", "apply_patch", "file_write")
CHECK_TOOLS = ("run_tests", "lint", "typecheck", "build", "bash_exec")
PUBLISH_TOOLS = ("git_push", "git_open_pr")


@dataclass(frozen=True)
class PromptTools:
    """The tools this turn can call, so the prompt names only those.

    The toolset is fixed per agent and transport (an agent's list, Slack's blocked tools), so
    building the prompt from it keeps the cached prefix stable within a session while no longer
    telling a restricted agent to use tools it does not have.
    """

    allowed: frozenset[str] | None = None
    denied: frozenset[str] = frozenset()

    def can(self, name: str) -> bool:
        if name in self.denied:
            return False
        return self.allowed is None or any(
            name == entry or ("*" in entry and fnmatchcase(name, entry)) for entry in self.allowed
        )

    def has(self, *names: str) -> bool:
        return any(self.can(name) for name in names)

    def named(self, *names: str) -> str:
        """The available ones among ``names``, as code spans for a sentence."""
        return ", ".join(f"`{name}`" for name in names if self.can(name))


def prompt_tools(ctx: TurnContext) -> PromptTools:
    """What the turn's agent and transport allow (mirrors what the runner hands the orchestrator)."""
    overlay = ctx.req.overlay
    listed = ctx.binding.whitelisted_tools
    scope = None if listed is None or "*" in listed else list(listed)
    if overlay is not None and overlay.tool_scope is not None:
        scoped = overlay.tool_scope(scope)
        scope = None if scoped is None else list(scoped)
    denied = frozenset(overlay.denied_tools or ()) if overlay is not None else frozenset()
    return PromptTools(None if scope is None else frozenset(scope), denied)


def _list_in_words(items: list[str]) -> str:
    """``a, b or c``."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} or {items[-1]}"


def sandbox_guidance(tools: PromptTools) -> list[str]:
    """How the sandbox works and how the agent gets code into it."""
    lines = ["\n[Runtime environment]"]
    if tools.has(*SANDBOX_TOOLS):
        lines.append(
            "- The sandbox starts BLANK: no repository is checked out and nothing from earlier "
            "sessions is there. It starts on demand, when a tool needs it."
        )
    sources = [
        label
        for label, tool in (
            ("knowledge bases (`kb_search`)", "kb_search"),
            ("skills", "execute_skill"),
            ("web search", "web_search"),
            ("the GitHub tools", "github_read_file"),
        )
        if tools.can(tool)
    ]
    answer_from = _list_in_words(["reasoning", *sources])
    lines.append(
        f"- Many tasks need no sandbox and no repository. Answer from {answer_from} whenever "
        "that is enough."
    )
    if tools.can("clone_repository"):
        lookups = [
            "the user's wording and the conversation",
            *(
                [
                    "`kb_search` and the skills you can read, which often map services to repositories"
                ]
                if tools.can("kb_search")
                else []
            ),
            *(
                [
                    "`github_find_repositories`, `github_read_file` (a README, AGENTS.md or a "
                    "directory, without cloning) and `github_issues`"
                ]
                if tools.has("github_find_repositories", "github_read_file")
                else []
            ),
        ]
        lines.append(
            "- If code is needed, first work out WHICH repository: "
            + "; ".join(f"({number}) {text}" for number, text in enumerate(lookups, start=1))
            + ". A connected GitHub MCP server adds its own `mcp__github__*` tools."
        )
        lines.append(
            "- Then fetch it with `clone_repository` (`owner/name` -> `/workspace/<name>`; safe to "
            "repeat). Once cloned, read it in the sandbox, never with `github_read_file`. If "
            "access is refused, continue without it where you can and say what you could not reach."
        )
    if tools.can("ask_question"):
        lines.append(
            "- `ask_question` blocks the whole turn until the person answers (it shows a decision "
            "card in the cockpit or Slack). Use it as a last resort, for choices you cannot make "
            "safely and that change the outcome (ambiguous requirements, irreversible forks); "
            "otherwise look it up, or pick the most reasonable option, state your assumption and "
            "continue."
        )
    if tools.can("bash_exec"):
        lines.append(
            "- The default image has git, Python with uv, Node, build tools, ripgrep and fd; "
            "install anything else with the package manager."
        )
    return lines


def workspace_guidance(tools: PromptTools) -> list[str]:
    if not tools.has(*SANDBOX_TOOLS):
        return []
    lines = ["\n[Workspace]"]
    lines.append(
        "- Each repository lives in `/workspace/<name>`. The first one you clone becomes the "
        "active repository: git, test, lint and build tools act on it unless you pass `path`, and "
        "relative file paths are inside it. In multi-repository work pass `path` rather than "
        "switching with `set_active_repository`."
    )
    if tools.can("bash_exec"):
        lines.append(
            "- `bash_exec` runs in the folder you give as `cwd` (e.g. `/workspace/api`); never "
            "start a command with `cd`. Every call is a fresh shell: `export` and shell variables "
            "do not carry over, so chain dependent commands with `&&` in one call."
        )
    lines.append(
        "- Tool output is clamped (head and tail, with the hidden range marked). Search first "
        "(`grep_search`, `glob_files`, `code_search`), then read only what you need: "
        "`file_read` returns 400 numbered lines at a time and takes a line range; `web_fetch` "
        "pages long documents with `start`."
    )
    return lines


def editing_guidance(tools: PromptTools) -> list[str]:
    lines: list[str] = []
    if tools.has(*EDIT_TOOLS):
        lines.append(
            "- Change existing files with `file_edit` (an exact block of the current text, "
            "without the line numbers `file_read` shows) or `apply_patch` (a unified diff); "
            "create new files with `file_write`. Do not rewrite a whole existing file to change "
            "part of it."
        )
    if tools.has(*CHECK_TOOLS):
        lines.append(
            "- Verify what you changed with `run_tests`, `lint`, `typecheck` or `build` (or the "
            "project's own commands) before you say it works. Report failures with their output "
            "and never claim a check you did not run; run only checks that already work, and do "
            "not set up a toolchain to run one."
        )
    return ["\n[Editing and verifying]", *lines] if lines else []


def working_style_guidance(tools: PromptTools) -> str:
    lines = [
        '- Settle what "done" means before the first tool call (the question to answer, the '
        "files to produce, the checks that prove it) and stop once that is met. Offer anything "
        "beyond it in your answer instead of doing it.",
        "- Batch independent calls into ONE step: every step is a round trip with your whole "
        "context. Read all the files you will need, run independent searches and commands, and "
        "make independent edits together, never one per step.",
        "- Decide, then act. Investigate in a few steps, state your decision in one line and do "
        "not reopen it without new evidence. When a detail stays uncertain (how a library "
        "behaves, what a flag does), assume what it documents, implement, and let a build or a "
        "test settle it instead of reading its source.",
        "- Build and test here in the sandbox as soon as there is something to build: it is your "
        "fastest feedback loop. Prepare the repository's tooling first as its instructions say.",
        "- When an edit, write or rebase step succeeds, trust the tool output and move on. Do "
        "not repeat `git status` or re-read a file you just wrote without a change in between.",
    ]
    if tools.can("bash_exec"):
        lines.append(
            "- Shell: `bash_exec` runs commands in bash (sh only in an image without bash)."
        )
    if tools.can("env_set"):
        lines.append(
            "- Environment variables every command needs (e.g. `GOPRIVATE`, `NODE_OPTIONS`): "
            "set them once with `env_set`; they apply to every later command, in this turn and "
            "later ones. Do not re-export them in each command."
        )
    lines.append(
        "- Do not repeat a search that already failed with a similar query: change approach or "
        "say what you could not find."
    )
    if tools.can("update_task_checklist"):
        lines.append(
            "- For work of three or more steps keep a task checklist with "
            "`update_task_checklist`: update it when a task starts or finishes (not on every "
            "step, and never re-send an unchanged list) and settle it before you answer. Say you "
            "are nearly done only when the next step is the final answer."
        )
    if tools.can("create_artifact"):
        lines.append(
            "- Publish architecture designs, migration plans, investigation reports and diffs "
            "meant for review with `create_artifact`. Artifacts are kept with the session, appear "
            "in the cockpit's Artifacts drawer and in Slack, and keep long text out of your "
            "context (the content you write is still part of this request)."
        )
    lines.append(
        "- Do not open with an acknowledgement ('Understood', 'Noted') of a reminder or notice; "
        "go straight to the next tool call or to your answer."
    )
    return "\n[Working style]\n" + "\n".join(lines)


def long_task_guidance(tools: PromptTools) -> str:
    lines: list[str] = []
    if tools.has("file_write", "file_edit", "bash_exec"):
        lines.append(
            "- Working plan: for work that will span several turns, maintain `.rocket/PLAN.md` "
            "(and optionally `PROGRESS.md`) with the goal, decisions and what remains; the "
            "checklist tracks the steps. The file is given back to you at the start of every turn "
            "and survives sandbox restarts, so update it when a decision or milestone changes, "
            "not on every step."
        )
    if tools.can("bash_exec"):
        lines.append(
            "- Never leave a rebase, merge conflict, or detached HEAD open at the end of a turn. "
            "Resolve or abort (`git rebase --abort`, `git merge --abort`) before you answer."
        )
    if tools.can("git_checkpoint"):
        lines.append(
            "- The platform snapshots the workspace before your first change of a turn. Before a "
            "sweeping edit, refactor or migration, add a named snapshot with `git_checkpoint` so "
            "it can be rewound."
        )
    if tools.has("clone_repository", "workspace_status"):
        lines.append(
            "- Across several repositories, work on one at a time in dependency order and verify "
            "each before moving to the next."
        )
    return "\n[Long-running tasks and git hygiene]\n" + "\n".join(lines) if lines else ""


def showing_work_guidance(tools: PromptTools) -> str:
    lines: list[str] = []
    if tools.can("process_start"):
        lines.append(
            "- Long-running commands: `bash_exec` waits for its command and kills it on timeout, "
            "so start servers, watchers and slow builds with `process_start` (it returns at "
            "once; read them with `process_output`, wait with `process_wait`, end them with "
            "`process_stop`)."
        )
        check = (
            " Check the result yourself with `browser_inspect` on `http://localhost:<port>/` "
            "(needs the browser sandbox image)."
            if tools.can("browser_inspect")
            else ""
        )
        lines.append(
            "- Showing your work: to let the user see an app, start it with `process_start` and "
            "`port` set (for example 3000) and tell them to open the Preview tab for that port; "
            "use relative asset paths (or a relative base path) so it works under the preview "
            "prefix." + check
        )
    return "\n".join(lines)


logger = logging.getLogger(__name__)

SIGNING_GUIDANCE = {
    "github_app": "GitHub signs the commits when `git_push` publishes them (Verified, made "
    "by the organization's GitHub App); their hashes change, and the local branch follows.",
    "gpg_key": "The member's key signs the commits when `git_push` publishes them; their "
    "hashes change, and the local branch follows.",
    "none": "Commits are published as they are, unsigned.",
}


def git_guidance(git_bundle: GitCredentialBundle | None, policy: GitPolicy) -> list[str]:
    """How the agent works with git and GitHub here, and any problem with its access."""
    reach = {
        "app": "repositories the organization's GitHub App can read",
        "member": "repositories the member's linked GitHub account can read",
    }.get(git_bundle.mode if git_bundle else "", "public repositories only")
    signing = SIGNING_GUIDANCE.get(policy.signing_mode, SIGNING_GUIDANCE["none"])
    lines = [
        "\n[Git and GitHub]",
        "- The sandbox holds no GitHub credential and needs none: `git clone` and `git fetch` "
        f"of GitHub URLs work through the platform ({reach}). `git push` and the `gh` CLI do "
        "not work here. Do not search the environment for tokens.",
        f"- Never commit to `{policy.default_branch}` or the repository's own default branch: "
        "create a branch (`git_branch`), commit "
        "(`git_commit`), then publish with `git_push`, or `git_open_pr` to also open the pull "
        f"request. {signing}",
        "- Everything else on GitHub goes through the platform's tools: `github_pull_request` "
        "(diff, checks, review threads, replies, reviews, merge), `github_actions` (runs, a "
        "failed job's log, re-runs), `github_issues`, `github_releases`, `github_search`, and "
        "`github_api` for anything they do not cover. Answer a review with `reply_to_thread`; "
        "diagnose CI from `job_logs`.",
        f"- Committer identity: '{git_bundle.author_name}' <{git_bundle.author_email}>."
        if git_bundle
        else "- Committer identity: the organization's bot.",
    ]
    if git_bundle and git_bundle.mode == "public":
        lines.append(
            "- Private repositories need the member to link GitHub in Settings > Git & version "
            "control, or an administrator to map the organization's GitHub App."
        )
    if git_bundle and git_bundle.warning:
        lines.append(
            "- Git authentication problem: the member's linked GitHub token is no longer "
            "valid and was withheld. Do not search the environment for credentials or "
            "retry authentication; tell the user to reconnect GitHub in Settings > Git & "
            "version control (or ask an administrator to map the GitHub App installation)."
        )
    return lines


async def _organization_guidance(ctx: TurnContext, member_prefs: dict[str, Any]) -> list[str]:
    session = ctx.session
    lines: list[str] = []
    general = await effective_settings(ctx.req.db_mgr, session.tenant_org_id, "general")
    if company := str(general["company_name"]):
        lines.append(f"- Organization: {company}.")
    if branch_prefix := str(member_prefs.get("git_branch_prefix") or ""):
        lines.append(
            f"- Branch naming: create new branches with the prefix `{branch_prefix}`. Pull "
            "requests go to each repository's own default branch "
            f"(`{ctx.binding.policy.git.default_branch}` when it cannot be read)."
        )
    if session.git_repo:
        branch = f" (branch: `{session.git_branch}`)" if session.git_branch else ""
        lines.append(f"- Repository for this session: `{session.git_repo}`{branch}.")
    return lines


MAX_SKILLS_LISTED = 30
SKILL_SUMMARY_CHARS = 220


async def _skills_guidance(ctx: TurnContext) -> list[str]:
    """Names and summaries of the skills this member's agent may load.

    Without this the model cannot know a skill exists: ``execute_skill`` takes a name and nothing
    else tells it which names there are.
    """
    skills = await list_visible_skills(
        ctx.req.db_mgr,
        ctx.session.tenant_org_id,
        ctx.session.tenant_user_id,
        ctx.binding.skill_ids,
    )
    if not skills:
        return []
    lines = [
        "\n[Skills]",
        "- The organization keeps skills: expert instructions for recurring work. When a task "
        "matches one, call `execute_skill` with its name BEFORE you start; it returns the "
        "instructions to follow (skills that are code run instead).",
    ]
    for name, description in skills[:MAX_SKILLS_LISTED]:
        summary = " ".join(description.split())[:SKILL_SUMMARY_CHARS]
        lines.append(f"  - `{name}`: {summary}")
    if len(skills) > MAX_SKILLS_LISTED:
        lines.append(f"  - ... and {len(skills) - MAX_SKILLS_LISTED} more.")
    return lines


async def _subagents_guidance(ctx: TurnContext, tools: PromptTools) -> list[str]:
    """How to delegate, and which specialist roles the organization has."""
    if not tools.has("delegate_subagent", "delegate_subagents_parallel"):
        return []
    lines = [
        "\n[Delegation]",
        "- Hand focused investigations, security reviews, test runs or isolated tasks to "
        "subagents (`delegate_subagent` for one task, `delegate_subagents_parallel` for several "
        "at once) when it saves you steps. They run in isolated worktrees and report back.",
    ]
    try:
        configs = await load_subagent_configs(ctx.req.db_mgr, ctx.session.tenant_org_id)
    except Exception:
        logger.warning("Could not load the subagent roles for the prompt", exc_info=True)
        return lines
    for config in sorted((c for c in configs.values() if c.enabled), key=lambda c: c.id):
        description = " ".join((config.description or config.role_title).split())[:120]
        lines.append(f"  - `{config.id}` ({config.role_title}): {description}")
    return lines


async def build_system_prompt(
    ctx: TurnContext, git_bundle: GitCredentialBundle, member_prefs: dict[str, Any]
) -> SystemPrompt:
    """Compose the cacheable prefix and the per-turn suffix."""
    overlay = ctx.req.overlay
    base = ctx.binding.system_prompt or DEFAULT_AGENT_PROMPT

    tools = prompt_tools(ctx)
    env_details = [
        *sandbox_guidance(tools),
        *workspace_guidance(tools),
        *editing_guidance(tools),
        working_style_guidance(tools),
        long_task_guidance(tools),
        showing_work_guidance(tools),
    ]
    if tools.has(*SANDBOX_TOOLS):
        env_details += git_guidance(git_bundle, ctx.binding.policy.git)
        if not tools.has(*PUBLISH_TOOLS):
            env_details.append(
                "- Publishing is not available here (`git_push` and `git_open_pr` are disabled): "
                "commit locally if it helps, and tell the person what they can publish themselves."
            )
    env_details = [line for line in env_details if line]
    env_details += await _organization_guidance(ctx, member_prefs)
    env_details += await _skills_guidance(ctx)
    env_details += await _subagents_guidance(ctx, tools)
    # The active repository's instructions change whenever the agent switches repository (in
    # multi-repository work, many times a session): in the cached prefix every switch made the
    # next turn re-send the whole history uncached, so they ride with the per-turn context.
    instructions = await _project_instructions(ctx)

    # Runtime facts (workspace, committer, repository) are session-stable, so they belong in
    # the cacheable prefix rather than the volatile suffix. The "why this model" notice is
    # per-turn, so it goes in the dynamic suffix only - putting it in the prefix would
    # invalidate the provider cache every turn.
    routing_notice = f"Active model: {ctx.effective_model} ({ctx.routing_reason})."
    processes_note = await _processes_note(ctx)
    github_note = await _github_activity_note(ctx)
    sign_in_note = _mcp_sign_in_note(ctx.state.mcp_needs_sign_in)
    env_note = _session_env_note(ctx.binding.policy)
    released_note = await _released_workspace_note(ctx)
    return SystemPrompt(
        stable=base
        + "\n"
        + "\n".join(env_details)
        + (f"\n{overlay.stable_suffix}" if overlay and overlay.stable_suffix else ""),
        dynamic=routing_notice
        + (f"\n{instructions}" if instructions else "")
        + (f"\n{released_note}" if released_note else "")
        + (f"\n{processes_note}" if processes_note else "")
        + (f"\n{github_note}" if github_note else "")
        + (f"\n{sign_in_note}" if sign_in_note else "")
        + (f"\n{env_note}" if env_note else "")
        + (f"\n{overlay.dynamic}" if overlay and overlay.dynamic else ""),
    )


def _session_env_note(policy: TurnPolicy) -> str:
    """Which variables every command already has (names only: values may be secrets)."""
    parts = []
    if policy.sandbox_env_names:
        parts.append(f"from the sandbox type: {', '.join(policy.sandbox_env_names)}")
    if policy.agent_env:
        parts.append(f"set by you (env_set): {', '.join(sorted(policy.agent_env))}")
    if not parts:
        return ""
    return f"Environment variables every command already has, {'; '.join(parts)}."


def _mcp_sign_in_note(servers: list[str]) -> str:
    """Tool servers the member has not signed in to: their tools are missing from this turn."""
    if not servers:
        return ""
    settings = f"{public_base_url()}/settings/mcp" if public_base_url() else "Settings > MCP"
    names = ", ".join(sorted(set(servers)))
    return (
        f"Not signed in (their tools are unavailable this turn): {names}. If the task needs one, "
        f"tell the person to sign in at {settings} and say what you could not do; do not try to "
        "reach that service another way."
    )


async def _released_workspace_note(ctx: TurnContext) -> str:
    """Tell the agent once that the session's workspace was released while it was idle."""
    metadata = ctx.session.metadata if isinstance(ctx.session.metadata, dict) else {}
    note = released_notice(metadata)
    if not note:
        return ""

    def delivered(record: SessionRecord) -> None:
        known = record.metadata if isinstance(record.metadata, dict) else {}
        record.metadata = {k: v for k, v in known.items() if k not in (RELEASED_AT,)}

    await ctx.req.store.mutate_session(ctx.session.session_id, ctx.session.tenant_org_id, delivered)
    return note


PROJECT_INSTRUCTIONS_KEY = "project_instructions"


async def _project_instructions(ctx: TurnContext) -> str | None:
    """The active repository's own instructions (AGENTS.md and the like).

    Read from the sandbox when it runs and remembered on the session, so a turn that finds
    the sandbox asleep sends the same stable prompt instead of breaking the prompt cache.
    """
    req, session = ctx.req, ctx.session
    repository = active_repository_of(session)
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    remembered = metadata.get(PROJECT_INSTRUCTIONS_KEY) or {}
    if req.driver is None or session.sandbox_status != SandboxStatus.RUNNING:
        same = remembered.get("repository") == repository
        return (str(remembered.get("text") or "") or None) if same else None
    text = await load_project_instructions(req.driver, ctx.session_id, repository) or ""
    if remembered != {"repository": repository, "text": text}:

        def remember(record: SessionRecord) -> None:
            known = record.metadata if isinstance(record.metadata, dict) else {}
            record.metadata = {
                **known,
                PROJECT_INSTRUCTIONS_KEY: {"repository": repository, "text": text},
            }

        await req.store.mutate_session(session.session_id, session.tenant_org_id, remember)
    return text or None


async def _github_activity_note(ctx: TurnContext) -> str | None:
    """Pull request activity kept while the agent was busy (shown once, then cleared)."""
    events = await GithubWatchStore(ctx.req.db_mgr).take_pending(
        ctx.session.tenant_org_id, ctx.session_id
    )
    return pending_events_notice(events) or None


async def _processes_note(ctx: TurnContext) -> str | None:
    """What the session's background processes did while the agent was away (once per exit)."""
    req, session = ctx.req, ctx.session
    if req.driver is None or session.sandbox_status != SandboxStatus.RUNNING:
        return None
    tracker = ProcessTracker.for_app(req.db_mgr, req.event_bus)
    processes = await tracker.refresh(session.session_id, session.tenant_org_id, req.driver)
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    reported = set(metadata.get(REPORTED_METADATA_KEY) or [])
    note = describe_for_agent(processes, reported)
    if fresh := unreported_exits(processes, reported):

        def remember_reported(record: SessionRecord) -> None:
            known = record.metadata if isinstance(record.metadata, dict) else {}
            seen = set(known.get(REPORTED_METADATA_KEY) or [])
            record.metadata = {**known, REPORTED_METADATA_KEY: sorted(seen | fresh)}

        await req.store.mutate_session(session.session_id, session.tenant_org_id, remember_reported)
    return note
