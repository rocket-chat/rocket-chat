"""The system prompt of a turn: who the agent is and what its environment looks like.

The stable part (identity, runtime facts, repository, committer) is byte-identical for a whole
session so the provider's prompt cache stays warm; the volatile part (which model is active)
goes after the cache boundary.
"""

from __future__ import annotations

from typing import Any

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
from specifications.interfaces.agent import GitPolicy, SystemPrompt
from specifications.interfaces.sandbox import SandboxStatus
from specifications.interfaces.session import SessionRecord

# How the sandbox works and how the agent gets code into it.
SANDBOX_GUIDANCE: tuple[str, ...] = (
    "\n[Runtime environment]",
    "- The sandbox starts BLANK: no repository is checked out and nothing from earlier "
    "sessions is there. It starts on demand, when a tool needs it.",
    "- Many tasks need no sandbox and no repository. Answer from reasoning, knowledge "
    "bases (`kb_search`), skills, web search or the GitHub tools whenever that is enough.",
    "- If code is needed, first work out WHICH repository: (1) the user's wording and the "
    "conversation; (2) `kb_search` and the skills you can read, which often map services to "
    "repositories; (3) `github_find_repositories`, `github_read_file` (a README, AGENTS.md "
    "or a directory, without cloning) and `github_issues`. A connected GitHub MCP server "
    "adds its own `mcp__github__*` tools.",
    "- Then fetch it with `clone_repository` (`owner/name` -> `/workspace/<name>`; safe to "
    "repeat). Once cloned, read it in the sandbox, never with `github_read_file`. If access "
    "is refused, continue without it where you can and say what you could not reach.",
    "- `ask_question` BLOCKS the whole turn until the person answers, possibly for hours. "
    "Treat it as a last resort, for a choice you cannot make safely and that changes the "
    "outcome; "
    "otherwise look it up, or pick the most reasonable option, state your assumption and "
    "continue.",
    "- The default image has git, Python with uv, Node, build tools, ripgrep and fd; "
    "install anything else with the package manager.",
)

WORKSPACE_GUIDANCE: tuple[str, ...] = (
    "\n[Workspace]",
    "- Each repository lives in `/workspace/<name>`. The first one you clone becomes the "
    "active repository; `set_active_repository` switches. Git, test, lint and build tools "
    "act on it unless you pass `path`, `bash_exec` runs in it unless you pass `cwd`, and "
    "relative file paths are inside it.",
    "- Every `bash_exec` is a fresh shell: `cd`, `export` and shell variables do not carry "
    "over. Pass `cwd`, or chain commands with `&&` in one call.",
    "- Tool output is clamped (head and tail, with the hidden range marked). Search first "
    "(`grep_search`, `glob_files`, `code_search`), then read only what you need: "
    "`file_read` returns 400 numbered lines at a time and takes a line range; `web_fetch` "
    "pages long documents with `start`.",
)

EDITING_GUIDANCE: tuple[str, ...] = (
    "\n[Editing and verifying]",
    "- Change existing files with `file_edit` (an exact block of the current text, without "
    "the line numbers `file_read` shows) or `apply_patch` (a unified diff); create new files "
    "with `file_write`. Do not rewrite a whole existing file to change part of it.",
    "- Verify in the repository with `run_tests`, `lint`, `typecheck` or `build` (or the "
    "project's own commands). Report failures with their output; never claim a check you "
    "did not run.",
)

WORKING_STYLE_GUIDANCE = (
    "\n[Working style]\n"
    '- Settle what "done" means before the first tool call (the question to answer, the '
    "files to produce) and stop once that is met. Extra verification or coverage beyond it "
    "is optional: offer it in the answer instead of doing it.\n"
    "- Batch independent calls into ONE step: emit all independent file edits, conflict "
    "resolutions, reads or searches in parallel within the same turn. Resolving one file per step "
    "multiplies round-trips and delays execution.\n"
    "- Avoid redundant verification loops: when a file edit, write, or rebase continue succeeds, "
    "trust the tool output and move directly to the next step. Do not execute consecutive identical "
    "`git status` calls or re-read files you just wrote without an intervening state change.\n"
    "- Shell dialect: commands in `bash_exec` execute in `/bin/sh` by default. Write portable "
    "POSIX shell scripts (`[ ... ]` instead of `[[ ... ]]`), or invoke `/bin/bash -c '...'` "
    "explicitly if using bashisms.\n"
    "- Do not repeat a search that already failed with a similar query: change approach or "
    "say what you could not find.\n"
    "- For work of three or more steps keep `update_task_checklist` current as you go and "
    "settled before you answer. Say you are nearly done only when the next step is the final "
    "answer.\n"
    "- Hand focused tasks or investigations to specialist subagents "
    "(`delegate_subagent` or `delegate_subagents_parallel` for concurrent tasks) when it saves you steps; "
    "they report back and isolate work on their own branch if they edit files.\n"
    "- Do not emit reflexive conversational acknowledgments ('Creator, noted', 'Understood', "
    "'Acknowledged') in response to system reminders or turn notices. Never speak filler "
    "acknowledgments; proceed directly to the required action with a tool call or deliver your "
    "substantive response."
)

LONG_TASK_GUIDANCE = (
    "\n[Long-running tasks and git hygiene]\n"
    "- Working plan: maintain `.rocket/PLAN.md` (and optional `PROGRESS.md`) for tasks of 3 or more "
    "steps. Write the goal, planned steps, decisions, and current progress. This file is preserved "
    "across turns and sandbox cycles.\n"
    "- Safe git operations: never leave a rebase, merge conflict, or detached HEAD open at the "
    "end of a turn. Always resolve or abort (`git rebase --abort`, `git merge --abort`) before "
    "delivering your answer.\n"
    "- Checkpoint before risky edits: create a git commit or branch before running sweeping edits, "
    "refactors, or migrations so work can be cleanly reverted if needed.\n"
    "- Dependency order: when working across multiple repositories or services, work on one repository "
    "at a time in dependency order. Verify each component before moving to the next."
)

SHOWING_WORK_GUIDANCE = (
    "- Long-running commands: `bash_exec` waits for its command and kills it on timeout, so "
    "start servers, watchers and slow builds with `process_start` (it returns at once; read "
    "them with `process_output`, wait with `process_wait`, end them with `process_stop`).\n"
    "- Showing your work: to let the user see an app, start it with `process_start` and "
    "`port` set (for example 3000) and tell them to open the Preview tab for that port; use "
    "relative asset paths (or a relative base path) so it works under the preview prefix. "
    "Check the result yourself with `browser_inspect` on `http://localhost:<port>/` (needs "
    "the browser sandbox image)."
)

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
        f"- Never commit to `{policy.default_branch}`: create a branch (`git_branch`), commit "
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
            f"- Branch naming: create new branches with the prefix `{branch_prefix}` "
            f"(base branch for pull requests: `{ctx.binding.policy.git.default_branch}`)."
        )
    if session.git_repo:
        lines.append(
            f"- Repository for this session: `{session.git_repo}` (branch: "
            f"`{session.git_branch or 'main'}`)."
        )
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


async def _subagents_guidance(ctx: TurnContext) -> list[str]:
    """Inform the lead agent of the organization's available specialist subagents."""
    try:
        configs = await load_subagent_configs(ctx.req.db_mgr, ctx.session.tenant_org_id)
    except Exception:
        return []
    active = [c for c in configs.values() if c.enabled]
    if not active:
        return []
    lines = [
        "\n[Specialist Subagents & Delegation]",
        "- Hand focused investigations, security reviews, test runs, or isolated tasks to specialist "
        "subagents (`delegate_subagent` for one task, or `delegate_subagents_parallel` for multiple "
        "tasks concurrently). Subagents run in isolated worktrees and report findings back:",
    ]
    for c in sorted(active, key=lambda x: x.id):
        desc = " ".join((c.description or c.role_title).split())[:120]
        lines.append(f"  - `{c.id}` ({c.role_title}): {desc}")
    return lines


async def build_system_prompt(
    ctx: TurnContext, git_bundle: GitCredentialBundle, member_prefs: dict[str, Any]
) -> SystemPrompt:
    """Compose the cacheable prefix and the per-turn suffix."""
    overlay = ctx.req.overlay
    base = ctx.binding.system_prompt or DEFAULT_AGENT_PROMPT

    env_details = [
        *SANDBOX_GUIDANCE,
        *WORKSPACE_GUIDANCE,
        *EDITING_GUIDANCE,
        WORKING_STYLE_GUIDANCE,
        LONG_TASK_GUIDANCE,
        SHOWING_WORK_GUIDANCE,
        *git_guidance(git_bundle, ctx.binding.policy.git),
    ]
    env_details += await _organization_guidance(ctx, member_prefs)
    env_details += await _skills_guidance(ctx)
    env_details += await _subagents_guidance(ctx)
    if instructions := await _project_instructions(ctx):
        env_details.append(instructions)

    # Runtime facts (workspace, committer, repository) are session-stable, so they belong in
    # the cacheable prefix rather than the volatile suffix. The "why this model" notice is
    # per-turn, so it goes in the dynamic suffix only - putting it in the prefix would
    # invalidate the provider cache every turn.
    router = ctx.state.router
    routing_notice = (
        f"# --- Model Routing (volatile; not cacheable) ---\n"
        f"Active model: {ctx.effective_model} ({ctx.routing_reason}); "
        f"routing mode: {router.mode if router is not None else 'frontier'}."
    )
    processes_note = await _processes_note(ctx)
    github_note = await _github_activity_note(ctx)
    released_note = await _released_workspace_note(ctx)
    return SystemPrompt(
        stable=base
        + "\n"
        + "\n".join(env_details)
        + (f"\n{overlay.stable_suffix}" if overlay and overlay.stable_suffix else ""),
        dynamic=routing_notice
        + (f"\n{released_note}" if released_note else "")
        + (f"\n{processes_note}" if processes_note else "")
        + (f"\n{github_note}" if github_note else "")
        + (f"\n{overlay.dynamic}" if overlay and overlay.dynamic else ""),
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
