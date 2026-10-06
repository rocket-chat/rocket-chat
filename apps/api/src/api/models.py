"""Pydantic request and response models for FastAPI endpoints."""

from typing import Any

from pydantic import BaseModel, Field


class AgentPersonaModel(BaseModel):
    """Custom Agent Persona configuration."""

    id: str = Field(..., description="Unique persona identifier")
    name: str = Field(..., description="Display name of persona")
    role_title: str = Field(..., description="Role title")
    description: str = Field(default="", description="Detailed persona description")
    system_prompt: str = Field(..., description="Agent system prompt / instructions")
    model: str = Field(
        default="openrouter/deepseek/deepseek-v4.1-flash", description="Default model ID"
    )
    whitelisted_tools: list[str] = Field(
        default_factory=list, description="List of allowed tool names"
    )
    icon: str = Field(default="bot", description="Avatar/icon key")
    sandbox_image: str = Field(
        default="python:3.12-slim", description="Custom container sandbox image"
    )
    cpu_limit: str = Field(default="2.0", description="Container CPU limit")
    memory_limit: str = Field(default="4Gi", description="Container RAM limit")
    temperature: float | None = Field(
        default=None, ge=0.0, le=2.0, description="Sampling temperature override"
    )
    thinking_budget_tokens: int | None = Field(
        default=None, description="Extended thinking budget tokens override"
    )
    reasoning_effort: str | None = Field(
        default=None, description="Reasoning effort tier override ('low', 'medium', 'high')"
    )


class CreateSessionRequest(BaseModel):
    """Payload for creating a new mission session."""

    title: str = Field(default="Mission Control Session", description="Session title or task name")
    tenant_org_id: str = Field(default="default_org", description="Organization ID")
    tenant_user_id: str = Field(default="dev_user", description="User ID")
    agent_id: str | None = Field(default=None, description="Active custom agent persona ID")
    container_image: str = Field(
        default="python:3.12-slim", description="Base docker container image"
    )
    git_repo: str | None = Field(default=None, description="Target repository URL")
    git_branch: str | None = Field(default="main", description="Target git branch")
    is_shared: bool = Field(
        default=False, description="Whether session is shared with organization"
    )
    collaborators: list[str] = Field(
        default_factory=list, description="List of user IDs with shared access"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Custom metadata")


class UpdateSessionRequest(BaseModel):
    """Payload for updating an existing session."""

    title: str | None = Field(default=None, description="Updated session title")
    agent_id: str | None = Field(default=None, description="Updated bound agent persona ID")
    is_shared: bool | None = Field(default=None, description="Updated session sharing status")
    collaborators: list[str] | None = Field(default=None, description="Updated collaborator IDs")
    checklist: list[dict[str, Any]] | None = Field(
        default=None, description="Updated task checklist"
    )
    conversation_history: list[dict[str, Any]] | None = Field(
        default=None, description="Updated conversation messages"
    )
    metadata: dict[str, Any] | None = Field(default=None, description="Metadata updates")


class ShareSessionRequest(BaseModel):
    """Payload for sharing a session across users."""

    is_shared: bool = Field(
        ..., description="Whether session is shared across organization members"
    )
    collaborators: list[str] = Field(
        default_factory=list, description="List of authorized user IDs"
    )


class SessionResponse(BaseModel):
    """Serialized representation of a session record."""

    session_id: str
    tenant_org_id: str
    tenant_user_id: str
    title: str
    source: str
    created_at: int
    updated_at: int
    sandbox_status: str
    agent_id: str | None = None
    container_image: str | None = None
    git_repo: str | None = None
    git_branch: str | None = None
    is_shared: bool = False
    collaborators: list[str] = Field(default_factory=list)
    checklist: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    suggested_followup: str | None = Field(
        default=None, description="Predictive next question suggested for the user"
    )
    conversation_history: list[dict[str, Any]] = Field(
        default_factory=list, description="Ordered conversation messages"
    )


class UserTurnRequest(BaseModel):
    """Payload to trigger an autonomous agent ReAct turn."""

    prompt: str = Field(..., min_length=1, description="Instruction or steer message for the agent")
    model: str | None = Field(default=None, description="Override LLM model name")


class AnswerQuestionRequest(BaseModel):
    """Payload answering an in-stream interactive decision card."""

    selected_options: list[str] = Field(..., description="Selected options from choices")
    custom_text: str | None = Field(
        default=None, description="Optional custom user clarification text"
    )


class ApprovalRequestModel(BaseModel):
    """Payload approving or denying a decision gate."""

    approved: bool = Field(..., description="True if approved, False if denied")


class UpdateUserSettingsRequest(BaseModel):
    """Payload to update user-specific overrides for a domain."""

    overrides: dict[str, Any] = Field(..., description="Map of key-value user overrides")


class UpdateOrgSettingsRequest(BaseModel):
    """Payload to update organization defaults and locked keys for a domain."""

    config: dict[str, Any] = Field(..., description="Organization default settings")
    locked_keys: list[str] = Field(
        default_factory=list, description="Keys that users are prohibited from overriding"
    )


class ResetSessionsResponse(BaseModel):
    """Response payload after resetting and creating a clean session."""

    status: str = "ok"
    deleted_count: int
    new_session: SessionResponse


class McpHeaderInput(BaseModel):
    name: str = Field(..., description="Header key")
    value: str = Field(..., description="Header value (or secret token to encrypt)")
    is_secret: bool = Field(default=False, description="Whether this header is a write-only secret")


class McpAuthInput(BaseModel):
    auth_type: str = Field(
        default="none", description="none, api_key, bearer, custom_headers, oauth2, basic"
    )
    api_key: str | None = Field(
        default=None, description="Cleartext API key/token (will be encrypted immediately)"
    )
    header_name: str | None = Field(default="Authorization", description="Header name for token")
    header_prefix: str | None = Field(default="Bearer", description="Prefix (Bearer, etc.)")
    headers: list[McpHeaderInput] = Field(default_factory=list, description="Custom headers")
    oauth_token_url: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    oauth_scopes: list[str] = Field(default_factory=list)
    basic_username: str | None = None
    basic_password: str | None = None


class McpToolItem(BaseModel):
    name: str
    description: str = ""
    parameters_summary: str | None = None
    guidance: str | None = None


class McpServerResponseModel(BaseModel):
    id: str
    name: str
    description: str = ""
    transport: str = "stdio"
    endpoint_or_command: str
    guidance: str = ""
    auth_type: str = "none"
    is_builtin: bool = False
    status: str = "connected"
    tools_count: int = 0
    last_probed: str = "Just now"
    tools: list[McpToolItem] = Field(default_factory=list)
    # Masked previews for write-only credentials
    has_api_key: bool = False
    api_key_fingerprint: str | None = None
    headers_preview: list[dict[str, Any]] = Field(default_factory=list)
    has_oauth_secret: bool = False


class CreateMcpServerRequest(BaseModel):
    id: str | None = None
    name: str
    description: str = ""
    transport: str = "stdio"  # stdio, http, sse
    endpoint_or_command: str
    guidance: str = ""
    auth: McpAuthInput = Field(default_factory=McpAuthInput)
    tools: list[McpToolItem] = Field(default_factory=list)


class SubagentConfigModel(BaseModel):
    id: str
    name: str
    role_title: str
    description: str
    system_prompt: str
    model: str | None = None
    max_turns: int = 5
    whitelisted_tools: list[str] = Field(default_factory=lambda: ["*"])
    temperature: float = 0.2
    enabled: bool = True
