"""The system prompt of a turn: who the agent is and what its environment looks like.

The stable part (identity, runtime facts, repository, committer) is byte-identical for a whole
session so the provider's prompt cache stays warm; the volatile part (which model is active)
goes after the cache boundary.
"""

from __future__ import annotations

from typing import Any

from api.services.git_credentials import GitCredentialBundle
from api.services.process_tracker import (
    REPORTED_METADATA_KEY,
    ProcessTracker,
    describe_for_agent,
    unreported_exits,
)
from api.services.project_instructions import load_project_instructions
from api.services.settings import effective_settings
from api.turns.context import TurnContext
from specifications.interfaces.agent import SystemPrompt
from specifications.interfaces.sandbox import SandboxStatus
from specifications.interfaces.session import SessionRecord

DEFAULT_AGENT_PROMPT = (
    "You are a Senior Autonomous Software Engineer running inside Rocket Chat. "
    "You write clean, modular, production-grade code adhering to strict types and software craftsmanship. "
    "Plan methodically, inspect files, apply surgical diffs, and verify your work with automated tests."
)

# How the sandbox works and how the agent gets code into it.
SANDBOX_GUIDANCE: tuple[str, ...] = (
    "\n[Runtime Environment & Lazy Sandbox Architecture]",
    "- Working Directory: `/workspace` (isolated container sandbox). It starts BLANK: no "
    "repository is checked out and nothing from earlier sessions is there.",
    "- Many tasks need no sandbox and no repository. Answer from reasoning, knowledge "
    "bases (`kb_search`), skills, web search or the GitHub read tools whenever that is "
    "enough. The sandbox starts on demand, only when a tool needs it (`bash_exec`, "
    "`file_read`, `file_edit`, `apply_patch`, `file_write`, `clone_repository`, "
    "`execute_skill`).",
    "- If code is needed, first work out WHICH repository, before touching the sandbox: "
    "(1) the user's wording and the conversation; (2) `kb_search` and the skills you can "
    "read - they often hold the service-to-repository map; (3) the GitHub read tools: "
    "`github_find_repositories` (by owner or search text), `github_read_file` (a README, "
    "AGENTS.md or a directory listing, without cloning) and `github_issues` (an issue or "
    "pull request and its discussion). If a GitHub MCP connector is connected, its "
    "`mcp__github__*` tools are available too.",
    "- Then fetch it with `clone_repository` (`owner/name` -> `/workspace/<name>`; safe "
    "to repeat; it authenticates with the member's linked GitHub account or the "
    "organization's GitHub App by itself). Do not search the environment for tokens and "
    "do not run `git clone` with credentials yourself. If access is refused, continue "
    "without that repository where you can and say what you could not reach.",
    "- `ask_question` BLOCKS the whole turn until the person answers, possibly for hours. "
    "Treat it as a last resort for a choice you cannot make safely and that changes the "
    "outcome. Prefer to look it up with the tools above, or pick the most reasonable "
    "option, state your assumption in the answer and continue.",
    "- The default image already has git, Python with uv, Node, build tools, ripgrep and "
    "fd; install anything else with the package manager.",
    "- Shell & Tools: bash (`bash_exec`), file operations (`file_read`, `file_edit`, "
    "`apply_patch`, `file_write`), `clone_repository`, web search, GitHub lookups and "
    "subagents.",
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


def git_guidance(git_bundle: GitCredentialBundle | None) -> list[str]:
    """What the agent may assume about git authentication, and any problem with it."""
    if git_bundle and git_bundle.token:
        lines = [
            f"- Git Authentication: Pre-authenticated for GitHub (mode={git_bundle.mode}). "
            f"Committer identity: '{git_bundle.author_name}' <{git_bundle.author_email}>. "
            "GitHub HTTPS URLs and `gh` CLI commands have valid tokens pre-configured. "
            "You can run `git clone`, `git push`, `git fetch`, or GitHub API calls directly without asking the user for credentials."
        ]
    else:
        lines = [
            "- Git Authentication: No GitHub token configured for this session. Public repositories can be cloned, but private access requires user authentication in Settings."
        ]
    if git_bundle and git_bundle.warning:
        lines.append(
            "- Git Authentication problem: the member's linked GitHub token is no longer "
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
            f"- Active Repository: `{session.git_repo}` (branch: `{session.git_branch or 'main'}`)."
        )
    return lines


async def build_system_prompt(
    ctx: TurnContext, git_bundle: GitCredentialBundle, member_prefs: dict[str, Any]
) -> SystemPrompt:
    """Compose the cacheable prefix and the per-turn suffix."""
    req, overlay = ctx.req, ctx.req.overlay
    base = ctx.binding.system_prompt or DEFAULT_AGENT_PROMPT

    env_details = [*SANDBOX_GUIDANCE, SHOWING_WORK_GUIDANCE, *git_guidance(git_bundle)]
    env_details += await _organization_guidance(ctx, member_prefs)
    if req.driver is not None and ctx.session.sandbox_status == SandboxStatus.RUNNING:
        instructions = await load_project_instructions(
            req.driver, ctx.session_id, ctx.session.git_repo
        )
        if instructions:
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
    return SystemPrompt(
        stable=base
        + "\n"
        + "\n".join(env_details)
        + (f"\n{overlay.stable_suffix}" if overlay and overlay.stable_suffix else ""),
        dynamic=routing_notice
        + (f"\n{processes_note}" if processes_note else "")
        + (f"\n{overlay.dynamic}" if overlay and overlay.dynamic else ""),
    )


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
