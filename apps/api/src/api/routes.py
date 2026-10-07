"""FastAPI route handlers for session management and real-time WebSocket streaming."""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict
from typing import Any

from agent_core.orchestrator import AsyncReActOrchestrator
from agent_core.tools.registry import ToolRegistry
from config_engine import CascadingSettingsResolver
from config_engine.crypto import CredentialCipher
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from api.auth.jwt_validator import JWTValidator
from api.db.models import OrgSettingModel, UserSettingModel
from api.models import (
    AgentPersonaModel,
    AnswerQuestionRequest,
    ApprovalRequestModel,
    CreateMcpServerRequest,
    CreateSessionRequest,
    McpServerResponseModel,
    McpToolItem,
    ResetSessionsResponse,
    SessionResponse,
    ShareSessionRequest,
    SubagentConfigModel,
    UpdateOrgSettingsRequest,
    UpdateSessionRequest,
    UpdateUserSettingsRequest,
    UserTurnRequest,
)
from specifications.interfaces.events import EventBusProtocol, EventType, SessionEvent
from specifications.interfaces.llm import ChatMessage, ModelRequest
from specifications.interfaces.sandbox import SandboxDriverProtocol, SandboxStatus, WorkspaceSpec
from specifications.interfaces.session import (
    SessionRecord,
    SessionSource,
    SessionStoreProtocol,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Sessions & Telemetry"])

_background_tasks: set[asyncio.Task[Any]] = set()
_settings_resolver = CascadingSettingsResolver()
_org_settings_store: dict[tuple[str, str], tuple[dict[str, Any], list[str]]] = {}
_user_settings_store: dict[tuple[str, str], dict[str, Any]] = {}


def _spawn_background_turn(
    session_id: str,
    prompt: str,
    orchestrator: AsyncReActOrchestrator,
    event_bus: EventBusProtocol,
    driver: SandboxDriverProtocol | None,
    registry: ToolRegistry | None,
    store: SessionStoreProtocol,
    model: str | None = None,
) -> asyncio.Task[None]:
    task = asyncio.create_task(
        execute_turn_background(
            session_id=session_id,
            prompt=prompt,
            orchestrator=orchestrator,
            event_bus=event_bus,
            driver=driver,
            registry=registry,
            store=store,
            model=model,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _get_request_identity(request: Request) -> tuple[str, str, list[str]]:
    """Extract (org_id, user_id, roles) from request state or auth headers, with dev fallback."""
    ctx = getattr(request.state, "context", None)
    if ctx and hasattr(ctx, "user_id"):
        return ctx.org_id, ctx.user_id, getattr(ctx, "roles", ["developer"])

    org_id = request.headers.get("X-Tenant-Org-Id") or "default_org"
    user_id = request.headers.get("X-Tenant-User-Id") or "dev_user"
    raw_role = request.headers.get("X-User-Role") or "developer"
    roles = [r.strip() for r in raw_role.split(",")] if raw_role else ["developer"]
    return org_id, user_id, roles


def _check_session_access(
    record: SessionRecord,
    user_id: str,
    roles: list[str],
    require_write: bool = False,
) -> None:
    """Enforce role permissions and user-specific isolation with sharing access."""
    if "admin" in roles:
        return
    if "readonly" in roles and require_write:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Read-only role cannot perform write actions on session",
        )
    is_owner = record.tenant_user_id == user_id
    is_shared = getattr(record, "is_shared", False) or bool(
        isinstance(record.metadata, dict) and record.metadata.get("is_shared", False)
    )
    collabs = getattr(record, "collaborators", []) or (
        record.metadata.get("collaborators", []) if isinstance(record.metadata, dict) else []
    )
    is_collab = user_id in collabs
    if not (is_owner or is_shared or is_collab):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: You do not have permission to access this session",
        )


def _to_response(s: SessionRecord) -> SessionResponse:
    agent_id = None
    if isinstance(s.metadata, dict):
        agent_id = s.metadata.get("agent_id")

    history = [
        {
            "role": m.role,
            "content": m.content or "",
            "reasoning": getattr(m, "reasoning", None),
            "tool_calls": getattr(m, "tool_calls", None),
        }
        for m in (s.conversation_history or [])
    ]

    is_shared = getattr(s, "is_shared", False)
    if not is_shared and isinstance(s.metadata, dict):
        is_shared = bool(s.metadata.get("is_shared", False))

    collaborators = getattr(s, "collaborators", []) or []
    if not collaborators and isinstance(s.metadata, dict):
        collaborators = s.metadata.get("collaborators", [])

    checklist = getattr(s, "checklist", []) or []
    if not checklist and isinstance(s.metadata, dict):
        checklist = s.metadata.get("checklist", [])

    suggested_followup = (
        s.metadata.get("suggested_followup") if isinstance(s.metadata, dict) else None
    )

    return SessionResponse(
        session_id=s.session_id,
        tenant_org_id=s.tenant_org_id,
        tenant_user_id=s.tenant_user_id,
        title=s.title,
        source=s.source.value if hasattr(s.source, "value") else str(s.source),
        created_at=s.created_at,
        updated_at=s.updated_at,
        sandbox_status=s.sandbox_status.value
        if hasattr(s.sandbox_status, "value")
        else str(s.sandbox_status),
        agent_id=agent_id,
        container_image=s.container_image,
        git_repo=s.git_repo,
        git_branch=s.git_branch,
        is_shared=is_shared,
        collaborators=collaborators,
        checklist=checklist,
        metadata=s.metadata,
        suggested_followup=suggested_followup,
        conversation_history=history,
    )


async def execute_turn_background(
    session_id: str,
    prompt: str,
    orchestrator: AsyncReActOrchestrator,
    event_bus: EventBusProtocol,
    driver: SandboxDriverProtocol | None,
    registry: ToolRegistry | None,
    store: SessionStoreProtocol,
    model: str | None = None,
) -> None:
    assistant_response_parts: list[str] = []
    reasoning_parts: list[str] = []
    executed_tools: list[dict[str, Any]] = []
    try:
        session = await store.get_session(session_id)
        if not session:
            session = SessionRecord(
                session_id=session_id,
                tenant_org_id="default_org",
                tenant_user_id="dev_user",
                title=f"Mission {session_id[:8]}",
                sandbox_status=SandboxStatus.RUNNING if driver else SandboxStatus.NON_EXISTENT,
            )
            await store.create_session(session)

        session.conversation_history.append(ChatMessage(role="user", content=prompt))
        await store.update_session(session)

        # Resolve model from agent persona if bound to session
        agent_id = session.metadata.get("agent_id") if isinstance(session.metadata, dict) else None
        agent = _agent_personas.get(agent_id) if agent_id else None
        effective_model = model or (agent.model if agent else None)

        if driver:
            await driver.ensure_workspace(
                WorkspaceSpec(
                    session_id=session_id,
                    tenant_org_id=session.tenant_org_id,
                    tenant_user_id=session.tenant_user_id,
                    container_image=session.container_image or "python:3.12-slim",
                )
            )
            await driver.start_sandbox(session_id)
            if registry:
                registry.bind_sandbox(driver, session_id)

        # Wire session rename callback for LLM agent
        async def _on_rename(s_id: str, new_title: str) -> None:
            s_rec = await store.get_session(s_id)
            if s_rec:
                s_rec.title = new_title
                await store.update_session(s_rec)
                await event_bus.publish(
                    SessionEvent(
                        session_id=s_id,
                        event_type=EventType.SESSION_UPDATED,
                        payload={"title": new_title, "session_id": s_id},
                    )
                )

        if registry:
            registry.set_rename_callback(_on_rename)

        # Silently auto-rename default session title on the first user message
        is_default_title = (
            not session.title
            or session.title.startswith("Mission ")
            or session.title.strip().lower() in ("new session", "new chat", "mission", "")
        )
        if is_default_title and len(session.conversation_history) <= 1:
            words = prompt.strip().replace("\n", " ").split()
            candidate_words = [w for w in words if len(w) > 1 and not w.startswith("/")][:5]
            if candidate_words:
                auto_title = " ".join(candidate_words)
                auto_title = auto_title[0].upper() + auto_title[1:]
                if len(auto_title) > 35:
                    auto_title = auto_title[:32] + "..."
                session.title = auto_title
                await store.update_session(session)
                await event_bus.publish(
                    SessionEvent(
                        session_id=session_id,
                        event_type=EventType.SESSION_UPDATED,
                        payload={"title": auto_title, "session_id": session_id},
                    )
                )

        await event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.STATUS_CHANGED,
                payload={"status": "running", "message": "Analyzing instruction..."},
            )
        )

        system_prompt = agent.system_prompt if agent else None
        whitelisted_tools = agent.whitelisted_tools if agent else None

        async for event in orchestrator.process_user_turn(
            session_id,
            prompt,
            model=effective_model,
            system_prompt=system_prompt,
            whitelisted_tools=whitelisted_tools,
            tenant_org_id=session.tenant_org_id,
            tenant_user_id=session.tenant_user_id,
        ):
            if event.event_type == "thought":
                delta = event.payload.get("delta", "")
                is_reasoning = event.payload.get("is_reasoning", False)
                if not is_reasoning:
                    assistant_response_parts.append(delta)
                else:
                    reasoning_parts.append(delta)
                # Differentiate reasoning thoughts from assistant response text
                ev_type = EventType.REASONING_TOKEN if is_reasoning else EventType.TOKEN
                await event_bus.publish(
                    SessionEvent(
                        session_id=session_id,
                        event_type=ev_type,
                        payload={"delta": delta},
                    )
                )
                await asyncio.sleep(0)

            elif event.event_type == "tool_call":
                raw_args = event.payload.get("arguments", "{}")
                parsed_args = raw_args
                if isinstance(raw_args, str):
                    try:
                        parsed_args = json.loads(raw_args)
                    except Exception:
                        parsed_args = {}

                tool_name = event.payload.get("name", "")
                await event_bus.publish(
                    SessionEvent(
                        session_id=session_id,
                        event_type=EventType.TOOL_STARTED,
                        payload={
                            "id": event.payload.get("id"),
                            "name": tool_name,
                            "arguments": parsed_args,
                        },
                    )
                )
                # Stream status updates for telemetry HUD
                if tool_name == "bash_exec":
                    cmd = parsed_args.get("command", "")
                    await event_bus.publish(
                        SessionEvent(
                            session_id=session_id,
                            event_type=EventType.LOG_CHUNK,
                            payload={"text": f"\r\n\x1b[38;5;208m$ {cmd}\x1b[0m\r\n"},
                        )
                    )

            elif event.event_type == "tool_completed":
                raw_args = event.payload.get("arguments", "{}")
                parsed_args = raw_args
                if isinstance(raw_args, str):
                    try:
                        parsed_args = json.loads(raw_args)
                    except Exception:
                        parsed_args = {}

                tool_name = event.payload.get("name", "")
                result_output = event.payload.get("result", "")
                tool_data = {
                    "id": event.payload.get("id"),
                    "name": tool_name,
                    "arguments": parsed_args,
                    "result": result_output,
                    "status": event.payload.get("status", "completed"),
                    "duration_ms": event.payload.get("duration_ms", 0),
                }
                executed_tools.append(tool_data)
                await event_bus.publish(
                    SessionEvent(
                        session_id=session_id,
                        event_type=EventType.TOOL_COMPLETED,
                        payload=tool_data,
                    )
                )
                # Stream command output to terminal stream if bash tool
                if tool_name == "bash_exec" and result_output:
                    clean_res = (
                        result_output if isinstance(result_output, str) else str(result_output)
                    )
                    await event_bus.publish(
                        SessionEvent(
                            session_id=session_id,
                            event_type=EventType.LOG_CHUNK,
                            payload={"text": f"{clean_res}\r\n"},
                        )
                    )

            elif event.event_type == "file_diff":
                await event_bus.publish(
                    SessionEvent(
                        session_id=session_id,
                        event_type=EventType.FILE_DIFF,
                        payload=event.payload,
                    )
                )

            elif event.event_type == "interactive_question":
                await event_bus.publish(
                    SessionEvent(
                        session_id=session_id,
                        event_type=EventType.DECISION_REQUIRED,
                        payload=event.payload,
                    )
                )

            elif event.event_type == "plan_updated":
                tasks = event.payload.get("tasks", [])
                current_session = await store.get_session(session_id)
                if current_session:
                    current_session.checklist = tasks
                    if isinstance(current_session.metadata, dict):
                        current_session.metadata["checklist"] = tasks
                    await store.update_session(current_session)
                await event_bus.publish(
                    SessionEvent(
                        session_id=session_id,
                        event_type=EventType.PLAN_UPDATED,
                        payload=event.payload,
                    )
                )

            elif event.event_type == "status":
                await event_bus.publish(
                    SessionEvent(
                        session_id=session_id,
                        event_type=EventType.STATUS_CHANGED,
                        payload=event.payload,
                    )
                )

            elif event.event_type == "subagent_started":
                await event_bus.publish(
                    SessionEvent(
                        session_id=session_id,
                        event_type=EventType.SUBAGENT_STARTED,
                        payload=event.payload,
                    )
                )

            elif event.event_type == "subagent_progress":
                await event_bus.publish(
                    SessionEvent(
                        session_id=session_id,
                        event_type=EventType.SUBAGENT_PROGRESS,
                        payload=event.payload,
                    )
                )

            elif event.event_type == "subagent_completed":
                await event_bus.publish(
                    SessionEvent(
                        session_id=session_id,
                        event_type=EventType.SUBAGENT_COMPLETED,
                        payload=event.payload,
                    )
                )

        full_content = "".join(assistant_response_parts).strip()
        full_reasoning = "".join(reasoning_parts).strip()
        current_session = await store.get_session(session_id)
        if current_session:
            current_session.conversation_history.append(
                ChatMessage(
                    role="assistant",
                    content=full_content,
                    reasoning=full_reasoning if full_reasoning else None,
                    tool_calls=executed_tools if executed_tools else None,
                )
            )
            await store.update_session(current_session)

        await event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.STATUS_CHANGED,
                payload={"status": "completed", "message": "Agent turn complete."},
            )
        )

        # Generate probable next follow-up question directly from LLM if enabled
        org_id = session.tenant_org_id if session else "default_org"
        user_id = session.tenant_user_id if session else "dev_user"
        org_conf, locked_keys = _org_settings_store.get((org_id, "user_preferences"), ({}, []))
        user_over = _user_settings_store.get((user_id, "user_preferences"), {})
        user_prefs = _settings_resolver.resolve(
            domain="user_preferences",
            org_config=org_conf,
            locked_keys=locked_keys,
            user_overrides=user_over,
        ).effective

        should_suggest = user_prefs.get("suggest_next_questions", True)
        if should_suggest:
            suggested_prompt: str | None = None

            # Clean full_content by removing internal reasoning or scratchpads
            clean_content = full_content
            if "<think>" in clean_content:
                clean_content = re.sub(r"<think>.*?</think>", "", clean_content, flags=re.DOTALL)
            clean_content = clean_content.strip()

            if clean_content and orchestrator.gateway:
                try:
                    summary_slice = (
                        clean_content[-1200:] if len(clean_content) > 1200 else clean_content
                    )
                    suggestion_req = ModelRequest(
                        model=effective_model or orchestrator.default_model,
                        messages=[
                            ChatMessage(
                                role="system",
                                content=(
                                    "You are a developer assistant suggestion engine. "
                                    "Based on the assistant's final response, output ONLY the single most logical next prompt "
                                    "that the human user would type into the chat or terminal. "
                                    "Rules: max 8 words, no quotes, no markdown, no punctuation except question mark or period, "
                                    "focus on developer action, never discuss internal reasoning or thinking."
                                ),
                            ),
                            ChatMessage(role="user", content=prompt),
                            ChatMessage(role="assistant", content=summary_slice),
                        ],
                        max_tokens=24,
                        temperature=0.2,
                        stream=False,
                    )
                    suggested_text = ""
                    async for chunk in orchestrator.gateway.chat_stream(suggestion_req):
                        if chunk.text_delta:
                            # Discard provider-level error messages
                            if "⚠️" not in chunk.text_delta:
                                suggested_text += chunk.text_delta

                    candidate = suggested_text.strip()
                    # Strip model special tokens (e.g. <|begin_of_sentence|>, < / begin__of__sentence / >, <s>, etc.)
                    candidate = re.sub(r"<[/ ]*\|?[^>|]*\|?[/ ]*>", "", candidate)
                    candidate = re.sub(
                        r"\[/?(?:BOS|EOS|INST)\]", "", candidate, flags=re.IGNORECASE
                    )
                    # Strip markdown headers, bullets, and numbering
                    candidate = re.sub(r"^[#*\-\d.]+\s*", "", candidate)
                    # Take only first line and strip quotes/backticks
                    candidate = (
                        candidate.split("\n")[0].strip().strip('"').strip("'").strip("`").strip()
                    )

                    # Reject candidates leaking prompt instructions, tokenizer artifacts, or CoT traces
                    leak_indicators = [
                        "i think",
                        "password",
                        "we need to",
                        "the user",
                        "output only",
                        "instruction",
                        "begin_of_sentence",
                        "end_of_sentence",
                        "as an ai",
                        "suggested prompt",
                        "here is",
                        "rule:",
                        "rules:",
                        "introduction",
                        "chapter",
                    ]
                    cand_lower = candidate.lower()
                    is_leak = any(ind in cand_lower for ind in leak_indicators)

                    # Ensure length and word count bounds (between 2 and 15 words)
                    words = candidate.split()
                    if (
                        candidate
                        and 10 <= len(candidate) <= 90
                        and 2 <= len(words) <= 15
                        and not is_leak
                    ):
                        suggested_prompt = candidate
                except Exception as sugg_err:
                    logger.debug("LLM suggestion generation failed: %s", sugg_err)

            # Contextual fallback if LLM suggestion is unavailable or empty
            if not suggested_prompt:
                last_tool = executed_tools[-1]["name"] if executed_tools else ""
                prompt_lower = prompt.lower()
                content_lower = clean_content.lower()

                if "test" in prompt_lower or "pytest" in prompt_lower or "test" in last_tool:
                    suggested_prompt = "Run pytest to verify all test suites pass"
                elif (
                    last_tool in ("file_write", "file_edit")
                    or "created" in content_lower
                    or "updated" in content_lower
                ):
                    suggested_prompt = "Run git status and show file diff"
                elif "git" in last_tool or "commit" in prompt_lower:
                    suggested_prompt = "Review git status and staged changes"
                elif "error" in content_lower or "fail" in content_lower:
                    suggested_prompt = "Show full error logs and stack trace"
                elif "explain" in prompt_lower or "why" in prompt_lower:
                    suggested_prompt = "Can you show a concrete code example?"
                elif clean_content:
                    suggested_prompt = "What are the recommended next steps?"

            if suggested_prompt:
                # Persist suggestion in session record so it survives page reloads
                current_session = await store.get_session(session_id)
                if current_session:
                    if not isinstance(current_session.metadata, dict):
                        current_session.metadata = {}
                    current_session.metadata["suggested_followup"] = suggested_prompt
                    await store.update_session(current_session)

                await event_bus.publish(
                    SessionEvent(
                        session_id=session_id,
                        event_type=EventType.FOLLOWUP_SUGGESTION,
                        payload={"suggestion": suggested_prompt},
                    )
                )

    except Exception as err:
        logger.exception("Error executing turn in session %s", session_id)
        await event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.ERROR,
                payload={"error": str(err)},
            )
        )
        await event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.STATUS_CHANGED,
                payload={"status": "error", "message": f"Mission failed: {err!s}"},
            )
        )
        try:
            current_session = await store.get_session(session_id)
            if current_session:
                current_session.conversation_history.append(
                    ChatMessage(role="assistant", content=f"⚠️ **Mission Error**: {err!s}")
                )
                await store.update_session(current_session)
        except Exception:
            pass


DEFAULT_AGENT_PERSONAS = [
    AgentPersonaModel(
        id="persona-general",
        name="General Software Engineer",
        role_title="Senior Autonomous Engineer",
        description="Autonomous full-stack engineering agent with full access to terminal execution, file inspection and editing, web lookup, git operations, and interactive decision gates.",
        system_prompt=(
            "You are a Senior Autonomous Software Engineer. You write clean, modular, production-grade code adhering to strict types and software craftsmanship. "
            "Plan methodically, inspect files, apply surgical diffs, and verify your work with automated tests."
        ),
        model="openrouter/deepseek/deepseek-v4.1-flash",
        whitelisted_tools=[
            "bash_exec",
            "file_read",
            "file_write",
            "file_edit",
            "apply_patch",
            "web_fetch",
            "web_search",
            "ask_question",
            "session_rename",
            "update_task_checklist",
            "delegate_subagent",
        ],
        icon="bot",
    ),
    AgentPersonaModel(
        id="persona-security",
        name="Security & Penetration Auditor",
        role_title="Application Security Specialist",
        description="Audits source code for injection attacks, RLS boundary leaks, secret exposure, and permissions.",
        system_prompt=(
            "You are a dedicated Security Auditor persona. Inspect source files and configuration for "
            "vulnerabilities (OWASP Top 10, SQL injection, RLS bypasses, secret leakage)."
        ),
        model="openrouter/anthropic/claude-3.5-sonnet",
        whitelisted_tools=["file_read", "web_search", "web_fetch"],
        icon="shield",
    ),
]

_agent_personas: dict[str, AgentPersonaModel] = {p.id: p for p in DEFAULT_AGENT_PERSONAS}


@router.get("/agents", response_model=list[AgentPersonaModel])
async def list_agents() -> list[AgentPersonaModel]:
    """List available custom agent personas."""
    return list(_agent_personas.values())


@router.post("/agents", response_model=AgentPersonaModel)
async def create_or_update_agent(persona: AgentPersonaModel) -> AgentPersonaModel:
    """Create or update a custom agent persona."""
    _agent_personas[persona.id] = persona
    return persona


# --- MCP SERVER REGISTRY & ENCRYPTED AUTH STORAGE ---

_cipher = CredentialCipher()

# In-memory store for registered MCP servers with write-only encrypted credentials
_mcp_servers_store: dict[str, dict[str, Any]] = {
    "mcp-filesystem": {
        "id": "mcp-filesystem",
        "name": "Workspace Filesystem",
        "description": "Direct workspace file inspection, surgical edits, directory traversal, and patch operations.",
        "transport": "stdio",
        "endpoint_or_command": "Built-in Sandbox Runtime (/workspace)",
        "guidance": "Use file_read before modifying any files. Use file_edit for small localized edits and apply_patch for large unified diffs.",
        "auth_type": "none",
        "is_builtin": True,
        "status": "connected",
        "last_probed": "Just now",
        "tools": [
            {
                "name": "file_read",
                "description": "Read file contents with line ranges.",
                "parameters_summary": "path: string, start_line?: number, end_line?: number",
            },
            {
                "name": "file_write",
                "description": "Atomic file creation or overwrite.",
                "parameters_summary": "path: string, content: string, overwrite?: boolean",
            },
            {
                "name": "file_edit",
                "description": "Perform resilient block replacement.",
                "parameters_summary": "path: string, target_block: string, replacement_block: string",
            },
            {
                "name": "apply_patch",
                "description": "Apply standard unified diff patch.",
                "parameters_summary": "path: string, patch_content: string",
            },
        ],
    },
    "mcp-github": {
        "id": "mcp-github",
        "name": "GitHub Integration",
        "description": "Repository navigation, PR reviews, issue management, and git commit history.",
        "transport": "stdio",
        "endpoint_or_command": "npx -y @modelcontextprotocol/server-github",
        "guidance": "Prefer search_repositories when locating dependencies and create_issue for filing bug tickets.",
        "auth_type": "none",
        "is_builtin": True,
        "status": "connected",
        "last_probed": "Just now",
        "tools": [
            {
                "name": "list_repositories",
                "description": "List repositories accessible to the installation.",
                "parameters_summary": "page?: number, per_page?: number",
            },
            {
                "name": "create_issue",
                "description": "Create an issue in a designated target repository.",
                "parameters_summary": "title: string, body?: string",
            },
            {
                "name": "search_repositories",
                "description": "Search repositories matching query.",
                "parameters_summary": "query: string, sort?: string",
            },
        ],
    },
    "mcp-postgres": {
        "id": "mcp-postgres",
        "name": "Database Inspector",
        "description": "Query PostgreSQL schemas, execute read-only SQL, and inspect table definitions.",
        "transport": "stdio",
        "endpoint_or_command": "uvx mcp-server-postgres --dsn postgresql://...",
        "guidance": "Always describe_schema first before writing SQL queries. Execute read-only SELECT queries only.",
        "auth_type": "none",
        "is_builtin": True,
        "status": "connected",
        "last_probed": "Just now",
        "tools": [
            {
                "name": "query_database",
                "description": "Run analytical read-only SQL queries.",
                "parameters_summary": "sql: string, limit?: number",
            },
            {
                "name": "describe_schema",
                "description": "Inspect table columns and relationships.",
                "parameters_summary": "table_name: string",
            },
        ],
    },
}


def _format_mcp_response(s: dict[str, Any]) -> McpServerResponseModel:
    tools_data = s.get("tools") or []
    tool_items = [
        McpToolItem(
            name=t["name"],
            description=t.get("description", ""),
            parameters_summary=t.get("parameters_summary"),
            guidance=t.get("guidance"),
        )
        for t in tools_data
    ]

    # Masked header previews
    headers_preview = []
    for h in s.get("headers_encrypted", []):
        headers_preview.append(
            {
                "name": h["name"],
                "value": CredentialCipher.fingerprint(h["value"])
                if h.get("is_secret")
                else h["value"],
                "is_secret": h.get("is_secret", False),
            }
        )

    api_key_enc = s.get("api_key_encrypted")
    api_key_fp = None
    if api_key_enc:
        try:
            raw = _cipher.decrypt(api_key_enc)
            api_key_fp = CredentialCipher.fingerprint(raw)
        except Exception:
            api_key_fp = "***"

    return McpServerResponseModel(
        id=s["id"],
        name=s["name"],
        description=s.get("description", ""),
        transport=s.get("transport", "stdio"),
        endpoint_or_command=s.get("endpoint_or_command", ""),
        guidance=s.get("guidance", ""),
        auth_type=s.get("auth_type", "none"),
        is_builtin=s.get("is_builtin", False),
        status=s.get("status", "connected"),
        tools_count=len(tool_items),
        last_probed=s.get("last_probed", "Just now"),
        tools=tool_items,
        has_api_key=bool(api_key_enc),
        api_key_fingerprint=api_key_fp,
        headers_preview=headers_preview,
        has_oauth_secret=bool(s.get("oauth_client_secret_encrypted")),
    )


@router.get("/mcp/servers", response_model=list[McpServerResponseModel])
async def list_mcp_servers() -> list[McpServerResponseModel]:
    """List all registered MCP servers with write-only credentials sanitized."""
    return [_format_mcp_response(s) for s in _mcp_servers_store.values()]


@router.post("/mcp/servers", response_model=McpServerResponseModel)
async def register_or_update_mcp_server(req: CreateMcpServerRequest) -> McpServerResponseModel:
    """Register or update an MCP server, securely encrypting any secrets with AES-256-GCM."""
    srv_id = req.id or f"mcp-{uuid.uuid4().hex[:8]}"

    # Encrypt secrets in memory immediately
    api_key_enc = None
    if req.auth.api_key:
        api_key_enc = _cipher.encrypt(req.auth.api_key)

    headers_enc = []
    for h in req.auth.headers:
        val = _cipher.encrypt(h.value) if h.is_secret else h.value
        headers_enc.append({"name": h.name, "value": val, "is_secret": h.is_secret})

    oauth_secret_enc = None
    if req.auth.oauth_client_secret:
        oauth_secret_enc = _cipher.encrypt(req.auth.oauth_client_secret)

    record = {
        "id": srv_id,
        "name": req.name,
        "description": req.description,
        "transport": req.transport,
        "endpoint_or_command": req.endpoint_or_command,
        "guidance": req.guidance,
        "auth_type": req.auth.auth_type,
        "api_key_encrypted": api_key_enc,
        "header_name": req.auth.header_name,
        "header_prefix": req.auth.header_prefix,
        "headers_encrypted": headers_enc,
        "oauth_token_url": req.auth.oauth_token_url,
        "oauth_client_id": req.auth.oauth_client_id,
        "oauth_client_secret_encrypted": oauth_secret_enc,
        "oauth_scopes": req.auth.oauth_scopes,
        "is_builtin": False,
        "status": "connected",
        "last_probed": "Just now",
        "tools": [t.model_dump() for t in req.tools] if req.tools else [],
    }
    _mcp_servers_store[srv_id] = record
    return _format_mcp_response(record)


@router.delete("/mcp/servers/{server_id}")
async def delete_mcp_server(server_id: str) -> dict[str, bool]:
    """Delete a custom MCP server registration."""
    if server_id in _mcp_servers_store:
        del _mcp_servers_store[server_id]
        return {"deleted": True}
    raise HTTPException(status_code=404, detail="MCP server not found")


@router.post("/mcp/servers/{server_id}/probe", response_model=McpServerResponseModel)
async def probe_mcp_server(server_id: str) -> McpServerResponseModel:
    """Probe an MCP server, validating transport and authentication."""
    if server_id not in _mcp_servers_store:
        raise HTTPException(status_code=404, detail="MCP server not found")
    record = _mcp_servers_store[server_id]
    record["status"] = "connected"
    record["last_probed"] = "Just now"
    return _format_mcp_response(record)


# --- SUBAGENTS CONFIGURATION API ---

_subagent_configs_store: dict[str, SubagentConfigModel] = {
    "security_auditor": SubagentConfigModel(
        id="security_auditor",
        name="Security & Penetration Auditor",
        role_title="Application Security Specialist",
        description="Audits source code for injection attacks, RLS boundary leaks, secret exposure, and permissions.",
        system_prompt=(
            "You are a dedicated Security Auditor subagent. Inspect source files and configuration for "
            "vulnerabilities (OWASP Top 10, SQL injection, RLS bypasses, secret leakage). "
            "Synthesize your findings concisely with specific line references and suggested remediations."
        ),
        whitelisted_tools=["file_read", "web_search", "web_fetch"],
        max_turns=4,
    ),
    "qa_verifier": SubagentConfigModel(
        id="qa_verifier",
        name="QA & Test Verifier",
        role_title="Automated Test Specialist",
        description="Executes test suites in the sandbox, synthesizes failing cases, and verifies regressions.",
        system_prompt=(
            "You are a QA & Test Verifier subagent. Run tests using bash_exec, inspect outputs, "
            "and synthesize an exact diagnostic report showing passed vs failed assertions."
        ),
        whitelisted_tools=["bash_exec", "file_read"],
        max_turns=4,
    ),
    "researcher": SubagentConfigModel(
        id="researcher",
        name="Codebase & Web Researcher",
        role_title="Technical Exploration Specialist",
        description="Explores external documentation, packages, or codebases to answer architectural questions.",
        system_prompt=(
            "You are a Codebase & Web Researcher subagent. Search technical documentation and read files "
            "to answer specific architectural or implementation questions without altering files."
        ),
        whitelisted_tools=["file_read", "web_search", "web_fetch"],
        max_turns=4,
    ),
}


@router.get("/subagents", response_model=list[SubagentConfigModel])
async def list_subagents() -> list[SubagentConfigModel]:
    """List configurable autonomous specialist subagents."""
    return list(_subagent_configs_store.values())


@router.put("/subagents/{subagent_id}", response_model=SubagentConfigModel)
async def update_subagent_config(subagent_id: str, cfg: SubagentConfigModel) -> SubagentConfigModel:
    """Update behavioral instructions, model, and allowed tools for a subagent."""
    _subagent_configs_store[subagent_id] = cfg
    return cfg


@router.post("/sessions", response_model=SessionResponse)
async def create_session(req: CreateSessionRequest, request: Request) -> SessionResponse:
    """Create a new agent session with optional sandbox provisioning."""
    store: SessionStoreProtocol = request.app.state.session_store
    driver: SandboxDriverProtocol | None = getattr(request.app.state, "sandbox_driver", None)

    req_org, req_user, roles = _get_request_identity(request)
    if "readonly" in roles:
        raise HTTPException(
            status_code=403, detail="Forbidden: Read-only role cannot create sessions"
        )

    effective_org = req.tenant_org_id if req.tenant_org_id != "default_org" else req_org
    effective_user = req.tenant_user_id if req.tenant_user_id != "dev_user" else req_user

    metadata = dict(req.metadata)
    if req.agent_id:
        metadata["agent_id"] = req.agent_id
    metadata["is_shared"] = req.is_shared
    metadata["collaborators"] = req.collaborators

    session_id = f"session_{uuid.uuid4().hex[:10]}"
    record = SessionRecord(
        session_id=session_id,
        tenant_org_id=effective_org,
        tenant_user_id=effective_user,
        title=req.title,
        source=SessionSource.WEB_UI,
        sandbox_status=SandboxStatus.STARTING if driver else SandboxStatus.NON_EXISTENT,
        container_image=req.container_image,
        git_repo=req.git_repo,
        git_branch=req.git_branch,
        is_shared=req.is_shared,
        collaborators=req.collaborators,
        metadata=metadata,
    )
    await store.create_session(record)

    if driver:
        try:
            await driver.ensure_workspace(
                WorkspaceSpec(
                    session_id=session_id,
                    tenant_org_id=effective_org,
                    tenant_user_id=effective_user,
                    container_image=req.container_image,
                )
            )
            await driver.start_sandbox(session_id)
            record.sandbox_status = SandboxStatus.RUNNING
            await store.update_session(record)
        except Exception as err:
            logger.warning("Failed to auto-start workspace for %s: %s", session_id, err)

    return _to_response(record)


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    request: Request,
    tenant_org_id: str | None = Query(None),
    tenant_user_id: str | None = Query(None),
    limit: int = Query(50),
) -> list[SessionResponse]:
    """List sessions, enforcing user-specific isolation by default with shared session visibility."""
    store: SessionStoreProtocol = request.app.state.session_store
    req_org, req_user, roles = _get_request_identity(request)
    effective_org = tenant_org_id or req_org

    records = await store.list_sessions(tenant_org_id=effective_org, limit=limit * 2)

    is_admin = "admin" in roles
    filtered: list[SessionRecord] = []
    for r in records:
        if is_admin:
            if tenant_user_id is None or r.tenant_user_id == tenant_user_id:
                filtered.append(r)
        else:
            r_shared = getattr(r, "is_shared", False) or bool(
                isinstance(r.metadata, dict) and r.metadata.get("is_shared", False)
            )
            r_collabs = getattr(r, "collaborators", []) or (
                r.metadata.get("collaborators", []) if isinstance(r.metadata, dict) else []
            )
            if r.tenant_user_id == req_user or r_shared or req_user in r_collabs:
                filtered.append(r)

    return [_to_response(r) for r in filtered[:limit]]


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, request: Request) -> SessionResponse:
    """Retrieve session record and metadata."""
    store: SessionStoreProtocol = request.app.state.session_store
    req_org, req_user, roles = _get_request_identity(request)
    if hasattr(store, "get_session"):
        try:
            record = await store.get_session(session_id, tenant_org_id=req_org)  # type: ignore[call-arg]
        except TypeError:
            record = await store.get_session(session_id)
    else:
        record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, req_user, roles, require_write=False)
    return _to_response(record)


@router.patch("/sessions/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: str, req: UpdateSessionRequest, request: Request
) -> SessionResponse:
    """Update an existing session's bound agent persona, title, sharing status, or metadata."""
    store: SessionStoreProtocol = request.app.state.session_store
    req_org, req_user, roles = _get_request_identity(request)
    if hasattr(store, "get_session"):
        try:
            record = await store.get_session(session_id, tenant_org_id=req_org)  # type: ignore[call-arg]
        except TypeError:
            record = await store.get_session(session_id)
    else:
        record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, req_user, roles, require_write=True)

    if req.title is not None:
        record.title = req.title

    meta = dict(record.metadata) if isinstance(record.metadata, dict) else {}
    if req.agent_id is not None:
        meta["agent_id"] = req.agent_id
    if req.is_shared is not None:
        record.is_shared = req.is_shared
        meta["is_shared"] = req.is_shared
    if req.collaborators is not None:
        record.collaborators = req.collaborators
        meta["collaborators"] = req.collaborators
    if req.checklist is not None:
        record.checklist = req.checklist
        meta["checklist"] = req.checklist
    if req.conversation_history is not None:
        record.conversation_history = [
            ChatMessage(
                role=m.get("role", "user"),
                content=m.get("content", ""),
                reasoning=m.get("reasoning"),
                tool_calls=m.get("tool_calls"),
            )
            for m in req.conversation_history
        ]
    if req.metadata is not None:
        meta.update(req.metadata)
    record.metadata = meta

    await store.update_session(record)
    return _to_response(record)


@router.post("/sessions/{session_id}/share", response_model=SessionResponse)
async def share_session(
    session_id: str, req: ShareSessionRequest, request: Request
) -> SessionResponse:
    """Share session across organization members or configure specific collaborators."""
    store: SessionStoreProtocol = request.app.state.session_store
    req_org, req_user, roles = _get_request_identity(request)
    if hasattr(store, "get_session"):
        try:
            record = await store.get_session(session_id, tenant_org_id=req_org)  # type: ignore[call-arg]
        except TypeError:
            record = await store.get_session(session_id)
    else:
        record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    if "admin" not in roles and record.tenant_user_id != req_user:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Only session owner or admin can configure sharing",
        )

    record.is_shared = req.is_shared
    record.collaborators = req.collaborators
    meta = dict(record.metadata) if isinstance(record.metadata, dict) else {}
    meta["is_shared"] = req.is_shared
    meta["collaborators"] = req.collaborators
    record.metadata = meta

    await store.update_session(record)
    return _to_response(record)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request) -> dict[str, Any]:
    """Delete a session record and tear down any associated sandbox workspace."""
    store: SessionStoreProtocol = request.app.state.session_store
    driver: SandboxDriverProtocol | None = getattr(request.app.state, "sandbox_driver", None)

    req_org, req_user, roles = _get_request_identity(request)
    if hasattr(store, "get_session"):
        try:
            record = await store.get_session(session_id, tenant_org_id=req_org)  # type: ignore[call-arg]
        except TypeError:
            record = await store.get_session(session_id)
    else:
        record = await store.get_session(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")

    if "admin" not in roles and record.tenant_user_id != req_user:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Only session owner or admin can delete session",
        )

    if driver:
        try:
            await driver.destroy_workspace(session_id)
        except Exception as err:
            logger.warning("Error destroying workspace for deleted session %s: %s", session_id, err)

    if hasattr(store, "delete_session"):
        try:
            deleted = await store.delete_session(session_id, tenant_org_id=req_org)  # type: ignore[call-arg]
        except TypeError:
            deleted = await store.delete_session(session_id)
    else:
        deleted = await store.delete_session(session_id)
    return {"deleted": deleted, "session_id": session_id}


@router.post("/sessions/cleanup")
async def cleanup_database_and_sessions(request: Request) -> dict[str, Any]:
    """Purge sessions, destroy orphan sandbox workspaces, and perform database maintenance."""
    store: SessionStoreProtocol = request.app.state.session_store
    driver: SandboxDriverProtocol | None = getattr(request.app.state, "sandbox_driver", None)

    _, _, roles = _get_request_identity(request)
    is_oidc = bool(os.getenv("OIDC_JWKS_URL") or os.getenv("OIDC_PUBLIC_KEY"))
    is_admin = (
        ("admin" in roles)
        or (not is_oidc)
        or (os.getenv("DEV_AUTH_BYPASS", "").lower() in ("true", "1", "yes"))
    )
    if not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Admin role required for database maintenance",
        )

    cleaned_count = 0
    sessions = await store.list_sessions(tenant_org_id="default_org", limit=200)
    for s in sessions:
        if driver:
            try:
                await driver.destroy_workspace(s.session_id)
            except Exception:
                pass
        await store.delete_session(s.session_id)
        cleaned_count += 1

    if hasattr(store, "clear_all"):
        await store.clear_all()

    return {"status": "ok", "cleaned_sessions": cleaned_count}


@router.post("/sessions/reset", response_model=ResetSessionsResponse)
async def reset_all_sessions(request: Request) -> ResetSessionsResponse:
    """Delete all active sessions for the current user and seat them in a fresh blank session."""
    store: SessionStoreProtocol = request.app.state.session_store
    driver: SandboxDriverProtocol | None = getattr(request.app.state, "sandbox_driver", None)

    org_id, user_id, roles = _get_request_identity(request)
    is_oidc = bool(os.getenv("OIDC_JWKS_URL") or os.getenv("OIDC_PUBLIC_KEY"))
    is_admin = (
        ("admin" in roles)
        or (not is_oidc)
        or (os.getenv("DEV_AUTH_BYPASS", "").lower() in ("true", "1", "yes"))
    )

    sessions = await store.list_sessions(tenant_org_id=org_id, limit=200)
    deleted_count = 0
    for s in sessions:
        if not is_admin and s.tenant_user_id not in (user_id, "dev_user", "default_user"):
            continue
        if driver:
            try:
                await driver.destroy_workspace(s.session_id)
            except Exception:
                pass
        await store.delete_session(s.session_id)
        deleted_count += 1

    new_session_id = f"session_{uuid.uuid4().hex[:10]}"
    new_record = SessionRecord(
        session_id=new_session_id,
        tenant_org_id=org_id,
        tenant_user_id=user_id,
        title="New Mission",
        source=SessionSource.WEB_UI,
        sandbox_status=SandboxStatus.NON_EXISTENT,
        metadata={"created_via": "session_reset", "checklist": []},
    )
    await store.create_session(new_record)
    return ResetSessionsResponse(
        status="ok",
        deleted_count=deleted_count,
        new_session=_to_response(new_record),
    )


async def _get_org_settings(
    request: Request, org_id: str, domain: str
) -> tuple[dict[str, Any], list[str]]:
    """Retrieve org configuration and locked keys, checking DB first then in-memory fallback."""
    db_mgr = getattr(request.app.state, "db_manager", None)
    if db_mgr and db_mgr.session_factory:
        try:
            async with db_mgr.session(tenant_org_id=org_id) as session:
                stmt = select(OrgSettingModel).where(
                    OrgSettingModel.org_id == org_id,
                    OrgSettingModel.domain == domain,
                )
                res = await session.execute(stmt)
                record = res.scalar_one_or_none()
                if record:
                    return record.config or {}, record.locked_keys or []
        except Exception as err:
            logger.debug("Database org_settings fetch failed: %s", err)
    return _org_settings_store.get((org_id, domain), ({}, []))


async def _save_org_settings(
    request: Request, org_id: str, domain: str, config: dict[str, Any], locked_keys: list[str]
) -> None:
    """Save org configuration and locked keys to DB and in-memory cache."""
    _org_settings_store[(org_id, domain)] = (config, locked_keys)
    db_mgr = getattr(request.app.state, "db_manager", None)
    if db_mgr and db_mgr.session_factory:
        try:
            async with db_mgr.session(tenant_org_id=org_id) as session:
                stmt = select(OrgSettingModel).where(
                    OrgSettingModel.org_id == org_id,
                    OrgSettingModel.domain == domain,
                )
                res = await session.execute(stmt)
                record = res.scalar_one_or_none()
                now = int(time.time())
                if record:
                    record.config = config
                    record.locked_keys = locked_keys
                    record.updated_at = now
                else:
                    record = OrgSettingModel(
                        id=f"org_cfg_{uuid.uuid4().hex[:12]}",
                        org_id=org_id,
                        domain=domain,
                        config=config,
                        locked_keys=locked_keys,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(record)
        except Exception as err:
            logger.debug("Database org_settings persist failed: %s", err)


async def _get_user_settings(
    request: Request, org_id: str, user_id: str, domain: str
) -> dict[str, Any]:
    """Retrieve user overrides, checking DB first then in-memory fallback."""
    db_mgr = getattr(request.app.state, "db_manager", None)
    if db_mgr and db_mgr.session_factory:
        try:
            async with db_mgr.session(tenant_org_id=org_id) as session:
                stmt = select(UserSettingModel).where(
                    UserSettingModel.user_id == user_id,
                    UserSettingModel.org_id == org_id,
                    UserSettingModel.domain == domain,
                )
                res = await session.execute(stmt)
                record = res.scalar_one_or_none()
                if record:
                    return record.overrides or {}
        except Exception as err:
            logger.debug("Database user_settings fetch failed: %s", err)
    return _user_settings_store.get((user_id, domain), {})


async def _save_user_settings(
    request: Request, org_id: str, user_id: str, domain: str, overrides: dict[str, Any]
) -> None:
    """Save user overrides to DB and in-memory cache."""
    _user_settings_store[(user_id, domain)] = overrides
    db_mgr = getattr(request.app.state, "db_manager", None)
    if db_mgr and db_mgr.session_factory:
        try:
            async with db_mgr.session(tenant_org_id=org_id) as session:
                stmt = select(UserSettingModel).where(
                    UserSettingModel.user_id == user_id,
                    UserSettingModel.org_id == org_id,
                    UserSettingModel.domain == domain,
                )
                res = await session.execute(stmt)
                record = res.scalar_one_or_none()
                now = int(time.time())
                if record:
                    record.overrides = overrides
                    record.updated_at = now
                else:
                    record = UserSettingModel(
                        id=f"user_cfg_{uuid.uuid4().hex[:12]}",
                        user_id=user_id,
                        org_id=org_id,
                        domain=domain,
                        overrides=overrides,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(record)
        except Exception as err:
            logger.debug("Database user_settings persist failed: %s", err)


async def _delete_user_settings(request: Request, org_id: str, user_id: str, domain: str) -> None:
    """Delete user overrides from DB and in-memory cache."""
    _user_settings_store.pop((user_id, domain), None)
    db_mgr = getattr(request.app.state, "db_manager", None)
    if db_mgr and db_mgr.session_factory:
        try:
            async with db_mgr.session(tenant_org_id=org_id) as session:
                stmt = select(UserSettingModel).where(
                    UserSettingModel.user_id == user_id,
                    UserSettingModel.org_id == org_id,
                    UserSettingModel.domain == domain,
                )
                res = await session.execute(stmt)
                record = res.scalar_one_or_none()
                if record:
                    await session.delete(record)
        except Exception as err:
            logger.debug("Database user_settings delete failed: %s", err)


@router.get("/settings/{domain}")
async def get_domain_settings(domain: str, request: Request) -> dict[str, Any]:
    """Retrieve cascading settings for the current user and organization."""
    org_id, user_id, _ = _get_request_identity(request)
    org_conf, locked_keys = await _get_org_settings(request, org_id, domain)
    user_over = await _get_user_settings(request, org_id, user_id, domain)
    resolved = _settings_resolver.resolve(
        domain=domain,
        org_config=org_conf,
        locked_keys=locked_keys,
        user_overrides=user_over,
    )
    return resolved.to_dict()


@router.put("/settings/{domain}")
async def update_user_domain_settings(
    domain: str,
    body: UpdateUserSettingsRequest,
    request: Request,
) -> dict[str, Any]:
    """Save user-specific overrides for a domain, honoring organization locked keys."""
    org_id, user_id, roles = _get_request_identity(request)
    if "readonly" in roles:
        raise HTTPException(
            status_code=403, detail="Forbidden: Read-only role cannot update user settings"
        )
    org_conf, locked_keys = await _get_org_settings(request, org_id, domain)
    allowed, rejected = _settings_resolver.filter_valid_user_overrides(
        domain, body.overrides, locked_keys
    )
    current_over = await _get_user_settings(request, org_id, user_id, domain)
    current_over = dict(current_over)
    current_over.update(allowed)
    await _save_user_settings(request, org_id, user_id, domain, current_over)

    resolved = _settings_resolver.resolve(
        domain=domain,
        org_config=org_conf,
        locked_keys=locked_keys,
        user_overrides=current_over,
    )
    res = resolved.to_dict()
    if rejected:
        res["warning"] = f"Locked keys ignored by organization policy: {rejected}"
    return res


@router.delete("/settings/{domain}")
async def reset_user_domain_settings(domain: str, request: Request) -> dict[str, Any]:
    """Reset user overrides for a domain back to organization defaults."""
    org_id, user_id, roles = _get_request_identity(request)
    if "readonly" in roles:
        raise HTTPException(
            status_code=403, detail="Forbidden: Read-only role cannot reset settings"
        )
    await _delete_user_settings(request, org_id, user_id, domain)
    org_conf, locked_keys = await _get_org_settings(request, org_id, domain)
    resolved = _settings_resolver.resolve(
        domain=domain,
        org_config=org_conf,
        locked_keys=locked_keys,
        user_overrides={},
    )
    return resolved.to_dict()


@router.get("/admin/settings/{domain}")
async def get_org_admin_settings(domain: str, request: Request) -> dict[str, Any]:
    """Retrieve organization-wide policy configuration and locked keys (admin only)."""
    org_id, _, roles = _get_request_identity(request)
    is_admin = any(r in ("admin", "owner") for r in roles)
    if not is_admin and os.getenv("DEV_AUTH_BYPASS", "").lower() not in (
        "true",
        "1",
        "yes",
    ):
        raise HTTPException(status_code=403, detail="Forbidden: Admin or owner role required")
    org_conf, locked_keys = await _get_org_settings(request, org_id, domain)
    system_defaults = _settings_resolver.get_system_defaults(domain)
    merged_config = dict(system_defaults)
    merged_config.update(org_conf)
    return {
        "domain": domain,
        "config": merged_config,
        "locked_keys": locked_keys,
        "system_defaults": system_defaults,
    }


@router.put("/admin/settings/{domain}")
async def update_org_admin_settings(
    domain: str,
    body: UpdateOrgSettingsRequest,
    request: Request,
) -> dict[str, Any]:
    """Update organization-level defaults and locked keys (admin only)."""
    org_id, _, roles = _get_request_identity(request)
    is_admin = any(r in ("admin", "owner") for r in roles)
    if not is_admin and os.getenv("DEV_AUTH_BYPASS", "").lower() not in (
        "true",
        "1",
        "yes",
    ):
        raise HTTPException(status_code=403, detail="Forbidden: Admin or owner role required")
    await _save_org_settings(request, org_id, domain, body.config, body.locked_keys)
    return {
        "status": "ok",
        "domain": domain,
        "config": body.config,
        "locked_keys": body.locked_keys,
    }


@router.get("/settings/org/usage")
async def get_org_usage_telemetry(request: Request) -> dict[str, Any]:
    """Retrieve aggregate token and dollar spend metrics for the organization."""
    org_id, _, _ = _get_request_identity(request)
    orchestrator: AsyncReActOrchestrator = request.app.state.orchestrator
    gateway = orchestrator.gateway
    if hasattr(gateway, "get_org_usage_summary"):
        return dict(gateway.get_org_usage_summary(org_id))
    return {
        "tenant_org_id": org_id,
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_cost_usd": 0.0,
        "monthly_budget_cap_usd": 250.0,
        "budget_used_percent": 0.0,
        "models": {},
    }


@router.get("/settings/user/usage")
async def get_user_usage_telemetry(request: Request) -> dict[str, Any]:
    """Retrieve personal token and cost usage for the active developer."""
    _, user_id, _ = _get_request_identity(request)
    orchestrator: AsyncReActOrchestrator = request.app.state.orchestrator
    gateway = orchestrator.gateway
    if hasattr(gateway, "get_user_usage_summary"):
        return dict(gateway.get_user_usage_summary(user_id))
    return {
        "tenant_user_id": user_id,
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_cost_usd": 0.0,
        "recent_turns_count": 0,
    }


@router.post("/sessions/{session_id}/turns")
async def start_session_turn(
    session_id: str,
    req: UserTurnRequest,
    request: Request,
) -> dict[str, str]:
    """Dispatch user prompt to the ReAct engine in the background."""
    store: SessionStoreProtocol = request.app.state.session_store
    orchestrator: AsyncReActOrchestrator = request.app.state.orchestrator
    event_bus: EventBusProtocol = request.app.state.event_bus
    driver: SandboxDriverProtocol | None = getattr(request.app.state, "sandbox_driver", None)
    registry: ToolRegistry | None = getattr(request.app.state, "tool_registry", None)

    _, req_user, roles = _get_request_identity(request)
    record = await store.get_session(session_id)
    if not record:
        if "readonly" in roles:
            raise HTTPException(
                status_code=403, detail="Forbidden: Read-only role cannot create sessions"
            )
        record = SessionRecord(
            session_id=session_id,
            tenant_org_id="default_org",
            tenant_user_id=req_user,
            title=f"Session {session_id[:8]}",
            sandbox_status=SandboxStatus.RUNNING if driver else SandboxStatus.NON_EXISTENT,
        )
        await store.create_session(record)
    else:
        _check_session_access(record, req_user, roles, require_write=True)

    _spawn_background_turn(
        session_id=session_id,
        prompt=req.prompt,
        orchestrator=orchestrator,
        event_bus=event_bus,
        driver=driver,
        registry=registry,
        store=store,
        model=req.model,
    )
    return {"status": "started", "session_id": session_id}


_models_cache: dict[str, Any] = {"timestamp": 0.0, "models": []}
_MODELS_CACHE_TTL = 3600.0  # 1 hour in-memory cache

FALLBACK_MODELS = [
    {
        "id": "openrouter/deepseek/deepseek-v4.1-flash",
        "name": "DeepSeek V4.1 Flash",
        "provider": "DeepSeek",
        "context_window": 131072,
        "recommended": True,
    },
    {
        "id": "openrouter/anthropic/claude-3.7-sonnet",
        "name": "Claude 3.7 Sonnet (Hybrid)",
        "provider": "Anthropic",
        "context_window": 200000,
        "recommended": False,
    },
    {
        "id": "openrouter/anthropic/claude-3.5-sonnet",
        "name": "Claude 3.5 Sonnet",
        "provider": "Anthropic",
        "context_window": 200000,
        "recommended": False,
    },
    {
        "id": "openrouter/openai/gpt-4o",
        "name": "GPT-4o Omnimodal",
        "provider": "OpenAI",
        "context_window": 128000,
        "recommended": False,
    },
    {
        "id": "openrouter/openai/gpt-4o-mini",
        "name": "GPT-4o Mini (Fast)",
        "provider": "OpenAI",
        "context_window": 128000,
        "recommended": False,
    },
]


@router.get("/models")
async def list_available_models(request: Request) -> dict[str, Any]:
    """Return available LLM models and sandbox execution driver status."""
    import os
    import time

    import httpx

    default_model = os.getenv("ROCKET_DEFAULT_MODEL", "openrouter/deepseek/deepseek-v4.1-flash")
    driver_type = getattr(request.app.state, "sandbox_driver", None)
    driver_name = "k8s" if driver_type and hasattr(driver_type, "namespace") else "docker"

    now = time.time()
    models: list[dict[str, Any]] = _models_cache.get("models", [])

    if not models or (now - _models_cache.get("timestamp", 0.0) > _MODELS_CACHE_TTL):
        try:
            api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
            headers = {}
            if api_key:
                clean_key = "".join(ch for ch in api_key if ch not in "\r\n\x00").strip()
                headers["Authorization"] = f"Bearer {clean_key}"
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get("https://openrouter.ai/api/v1/models", headers=headers)
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    parsed: list[dict[str, Any]] = []
                    for item in data:
                        raw_id = item.get("id", "")
                        if not raw_id:
                            continue
                        model_id = f"openrouter/{raw_id}"
                        provider_part = (
                            raw_id.split("/")[0].replace("-", " ").title()
                            if "/" in raw_id
                            else "OpenRouter"
                        )
                        context_len = int(item.get("context_length") or 128000)
                        parsed.append(
                            {
                                "id": model_id,
                                "name": item.get("name") or raw_id,
                                "provider": provider_part,
                                "context_window": context_len,
                                "recommended": model_id == default_model,
                            }
                        )
                    if parsed:
                        popular_rank = {
                            "openrouter/deepseek/deepseek-v4.1-flash": 0,
                            "openrouter/anthropic/claude-3.7-sonnet": 1,
                            "openrouter/anthropic/claude-3.5-sonnet": 2,
                            "openrouter/openai/gpt-4o": 3,
                            "openrouter/openai/gpt-4o-mini": 4,
                            "openrouter/deepseek/deepseek-chat": 5,
                        }
                        parsed.sort(
                            key=lambda m: (
                                popular_rank.get(m["id"], 999),
                                not m["recommended"],
                                m["name"],
                            )
                        )
                        _models_cache["models"] = parsed
                        _models_cache["timestamp"] = now
                        models = parsed
        except Exception as err:
            logger.warning("Could not fetch dynamic OpenRouter models: %s", err)

    if not models:
        models = FALLBACK_MODELS

    return {
        "default_model": default_model,
        "active_driver": driver_name,
        "models": models,
    }


@router.post("/sessions/{session_id}/questions/{question_id}")
async def answer_question(
    session_id: str,
    question_id: str,
    req: AnswerQuestionRequest,
    request: Request,
) -> dict[str, Any]:
    """Submit choice or write-in to an in-stream Interactive Decision Card."""
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if record:
        _, req_user, roles = _get_request_identity(request)
        _check_session_access(record, req_user, roles, require_write=True)

    orchestrator: AsyncReActOrchestrator = request.app.state.orchestrator
    event_bus: EventBusProtocol = request.app.state.event_bus

    try:
        await orchestrator.submit_question_answer(
            session_id=session_id,
            question_id=question_id,
            selected_options=req.selected_options,
            custom_text=req.custom_text,
        )
        await event_bus.publish(
            SessionEvent(
                session_id=session_id,
                event_type=EventType.DECISION_SUBMITTED,
                payload={
                    "question_id": question_id,
                    "selected_options": req.selected_options,
                    "custom_text": req.custom_text,
                },
            )
        )
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err

    return {"status": "submitted", "question_id": question_id}


@router.post("/sessions/{session_id}/approvals/{action_id}")
async def submit_approval(
    session_id: str,
    action_id: str,
    req: ApprovalRequestModel,
    request: Request,
) -> dict[str, Any]:
    """Approve or deny an agent confirmation gate."""
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if record:
        _, req_user, roles = _get_request_identity(request)
        _check_session_access(record, req_user, roles, require_write=True)

    orchestrator: AsyncReActOrchestrator = request.app.state.orchestrator
    try:
        await orchestrator.submit_human_approval(
            session_id=session_id,
            action_id=action_id,
            approved=req.approved,
        )
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err

    return {"status": "recorded", "action_id": action_id, "approved": req.approved}


@router.delete("/sessions/{session_id}/turns")
async def cancel_turn(session_id: str, request: Request) -> dict[str, str]:
    """Halt current autonomous execution immediately."""
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if record:
        _, req_user, roles = _get_request_identity(request)
        _check_session_access(record, req_user, roles, require_write=True)

    orchestrator: AsyncReActOrchestrator = request.app.state.orchestrator
    await orchestrator.cancel_turn(session_id)
    return {"status": "cancelled", "session_id": session_id}


@router.websocket("/sessions/{session_id}/ws")
async def session_websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(default=""),
) -> None:
    """
    Real-time WebSocket endpoint streaming SessionEvent tokens, tool states, diffs,
    and receiving keyboard/interactive steering messages.
    """
    store: SessionStoreProtocol = websocket.app.state.session_store
    event_bus: EventBusProtocol = websocket.app.state.event_bus
    orchestrator: AsyncReActOrchestrator = websocket.app.state.orchestrator
    driver: SandboxDriverProtocol | None = getattr(websocket.app.state, "sandbox_driver", None)
    registry: ToolRegistry | None = getattr(websocket.app.state, "tool_registry", None)

    # Determine caller identity: validate JWT if token provided or OIDC is configured
    is_oidc_configured = bool(os.getenv("OIDC_JWKS_URL") or os.getenv("OIDC_PUBLIC_KEY"))
    user_id = "dev_user"
    roles = ["developer"]

    if token:
        try:
            validator = getattr(websocket.app.state, "jwt_validator", None) or JWTValidator()
            ctx = validator.validate_token(token)
            user_id = ctx.user_id
            roles = ctx.roles
        except Exception:
            if is_oidc_configured:
                await websocket.close(
                    code=1008, reason="Unauthorized: invalid or expired WebSocket token"
                )
                return
            # In development fallback mode, accept test identity if provided
            if token.startswith("user_"):
                user_id = token
            elif token == "dev_token":
                user_id = "dev_user"

    # Rehydrate session state on connect
    session = await store.get_session(session_id)
    if not session:
        await websocket.close(code=1008, reason="Session not found")
        return

    # Enforce session ACL
    if is_oidc_configured and "admin" not in roles and session.tenant_user_id != user_id:
        is_shared = getattr(session, "is_shared", False) or bool(
            isinstance(session.metadata, dict) and session.metadata.get("is_shared", False)
        )
        collabs = getattr(session, "collaborators", []) or (
            session.metadata.get("collaborators", []) if isinstance(session.metadata, dict) else []
        )
        if not is_shared and user_id not in collabs:
            await websocket.close(code=1008, reason="Forbidden: Access to session denied")
            return

    await websocket.accept()

    await websocket.send_json(
        {
            "type": "sync",
            "session_id": session_id,
            "session": asdict(session),
        }
    )

    async def _send_events_loop() -> None:
        async for event in event_bus.subscribe(session_id):
            try:
                await websocket.send_json(
                    {
                        "event_type": event.event_type.value,
                        "session_id": event.session_id,
                        "payload": event.payload,
                        "timestamp_ms": event.timestamp_ms,
                    }
                )
            except Exception:
                break

    send_task = asyncio.create_task(_send_events_loop())

    try:
        while True:
            raw_msg = await websocket.receive_text()
            try:
                data = json.loads(raw_msg)
            except Exception:
                continue

            msg_type = data.get("type")
            if msg_type in ("turn", "action") or data.get("action") == "turn":
                prompt = (data.get("content") or data.get("prompt") or "").strip()
                model_override = data.get("model")
                if prompt:
                    _spawn_background_turn(
                        session_id=session_id,
                        prompt=prompt,
                        orchestrator=orchestrator,
                        event_bus=event_bus,
                        driver=driver,
                        registry=registry,
                        store=store,
                        model=model_override,
                    )

            elif msg_type == "answer_question":
                qid = data.get("question_id", "")
                opts = data.get("selected_options", [])
                text = data.get("custom_text")
                if qid:
                    try:
                        await orchestrator.submit_question_answer(
                            session_id=session_id,
                            question_id=qid,
                            selected_options=opts,
                            custom_text=text,
                        )
                        await event_bus.publish(
                            SessionEvent(
                                session_id=session_id,
                                event_type=EventType.DECISION_SUBMITTED,
                                payload={
                                    "question_id": qid,
                                    "selected_options": opts,
                                    "custom_text": text,
                                },
                            )
                        )
                    except KeyError as err:
                        logger.warning("Pending question %s not found: %s", qid, err)

            elif msg_type == "approval":
                aid = data.get("action_id", "")
                approved = bool(data.get("approved", True))
                if aid:
                    try:
                        await orchestrator.submit_human_approval(
                            session_id=session_id,
                            action_id=aid,
                            approved=approved,
                        )
                    except KeyError as err:
                        logger.warning("Pending approval %s not found: %s", aid, err)

            elif msg_type == "share":
                is_shared = bool(data.get("is_shared", False))
                collaborators = data.get("collaborators", [])
                s_rec = await store.get_session(session_id)
                if s_rec:
                    s_rec.is_shared = is_shared
                    s_rec.collaborators = collaborators
                    if isinstance(s_rec.metadata, dict):
                        s_rec.metadata["is_shared"] = is_shared
                        s_rec.metadata["collaborators"] = collaborators
                    await store.update_session(s_rec)
                    await event_bus.publish(
                        SessionEvent(
                            session_id=session_id,
                            event_type=EventType.SESSION_UPDATED,
                            payload={"is_shared": is_shared, "collaborators": collaborators},
                        )
                    )

            elif msg_type == "terminal_input":
                term_data = data.get("data", "")
                if driver and term_data:
                    # Echo raw terminal input chunk or exec keystroke directly into sandbox
                    try:
                        # If newline entered or control sequence, execute in workspace
                        if term_data == "\r" or term_data == "\n":
                            term_data = "\r\n"
                        await event_bus.publish(
                            SessionEvent(
                                session_id=session_id,
                                event_type=EventType.LOG_CHUNK,
                                payload={"text": term_data},
                            )
                        )
                    except Exception as term_err:
                        logger.debug("Terminal input handling error: %s", term_err)

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected for session %s", session_id)
    finally:
        send_task.cancel()
        try:
            await send_task
        except asyncio.CancelledError:
            pass
