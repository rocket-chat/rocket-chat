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
    model: str = Field(default="", description="Model ID; empty inherits the user/org default")
    whitelisted_tools: list[str] = Field(
        default_factory=list, description="List of allowed tool names"
    )
    skill_ids: list[str] = Field(default_factory=list, description="Attached custom skill IDs")
    knowledge_base_ids: list[str] = Field(
        default_factory=list, description="Attached knowledge base IDs"
    )
    scope: str = Field(default="org", description="'org' or 'user'")
    user_id: str | None = Field(default=None, description="Owner user ID for personal agents")
    built_in: bool = Field(
        default=False,
        description="Shipped with the platform: read-only; customize it by saving a copy",
    )
    icon: str = Field(default="bot", description="Avatar/icon key")
    sandbox_image: str = Field(
        default="", description="Sandbox image; empty inherits the organization default"
    )
    cpu_limit: str = Field(default="", description="CPU cores; empty inherits the org setting")
    memory_limit: str = Field(
        default="", description="Memory limit; empty inherits the org setting"
    )
    temperature: float | None = Field(
        default=None, ge=0.0, le=2.0, description="Sampling temperature override"
    )
    thinking_budget_tokens: int | None = Field(
        default=None, description="Extended thinking budget tokens override"
    )
    reasoning_effort: str | None = Field(
        default=None, description="Reasoning effort tier override ('low', 'medium', 'high')"
    )
    max_turns: int | None = Field(
        default=None, ge=1, le=100, description="Maximum ReAct turn cycles override"
    )


class CreateSessionRequest(BaseModel):
    """Payload for creating a new mission session."""

    title: str = Field(default="New Session", description="Session title or task name")
    tenant_org_id: str = Field(default="default_org", description="Organization ID")
    tenant_user_id: str = Field(default="dev_user", description="User ID")
    agent_id: str | None = Field(default=None, description="Active custom agent persona ID")
    model: str | None = Field(default=None, description="Optional model override")
    initial_prompt: str | None = Field(
        default=None, description="Initial prompt to ignite the first turn on creation"
    )
    sandbox_id: str | None = Field(
        default=None,
        description="A sandbox from the organization's catalogue (GET /v1/sandboxes); empty "
        "uses the catalogue's default",
    )
    container_image: str | None = Field(
        default=None,
        description="An image from the catalogue; prefer sandbox_id",
    )
    is_shared: bool = Field(
        default=False, description="Whether session is shared with organization"
    )
    collaborators: list[str] = Field(
        default_factory=list, description="List of user IDs with shared access"
    )
    permission_mode: str | None = Field(
        default=None,
        description="Supervision level: plan, ask, auto_edit, auto or bypass (default follows settings)",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Client-settable session metadata (inference_mode, selected_model, agent_id)",
    )


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
    attachment_ids: list[str] = Field(
        default_factory=list,
        description="Attachment ids to attach to this turn only (never re-sent on later turns)",
    )


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
    member_override: bool = Field(
        default=False,
        description=(
            "Command servers only: each member may set their own value for this variable. "
            "An empty value then means the server is usable only by members who set one."
        ),
    )


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
    oauth_authorization_url: str | None = None
    oauth_token_url: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    oauth_scopes: list[str] = Field(default_factory=list)
    oauth_mode: str = Field(
        default="org",
        pattern="^(org|member)$",
        description=(
            "'org': administrators connect one shared account. 'member': every member connects "
            "their own account to this organization server."
        ),
    )
    basic_username: str | None = None
    basic_password: str | None = None
    install_command: str | None = Field(
        default=None,
        description=(
            "Command servers only: a script that installs the program the command runs, executed "
            "once on the MCP runner (for example downloading a release binary)."
        ),
    )


class McpToolItem(BaseModel):
    name: str
    description: str = ""
    parameters_summary: str | None = None
    guidance: str | None = None


class ValidateMcpServerRequest(BaseModel):
    transport: str = "stdio"
    endpoint_or_command: str
    scope: str = "org"
    auth: McpAuthInput = Field(default_factory=McpAuthInput)


class ValidateMcpServerResponse(BaseModel):
    valid: bool
    status: str
    tools: list[McpToolItem] = Field(default_factory=list)
    error: str | None = None


class McpRunnerStatus(BaseModel):
    """Whether command (stdio) MCP servers can run on this installation."""

    configured: bool = Field(..., description="An MCP runner is part of this deployment")
    available: bool = Field(..., description="The runner answered a health check just now")
    detail: str = ""


class McpServerResponseModel(BaseModel):
    id: str
    name: str
    description: str = ""
    transport: str = "stdio"
    endpoint_or_command: str
    guidance: str = ""
    auth_type: str = "none"
    scope: str = "org"
    user_id: str | None = None
    is_builtin: bool = False
    status: str = "connected"
    tools_count: int = 0
    last_probed: str = ""
    tools: list[McpToolItem] = Field(default_factory=list)
    # Masked previews for write-only credentials
    has_api_key: bool = False
    api_key_fingerprint: str | None = None
    headers_preview: list[dict[str, Any]] = Field(default_factory=list)
    has_oauth_secret: bool = False
    oauth_status: str | None = None
    oauth_mode: str = "org"
    oauth_authorization_url: str | None = None
    install_command: str | None = None
    overridable_env: list[str] = Field(
        default_factory=list, description="Variables members may set for themselves"
    )
    member_env_set: list[str] = Field(
        default_factory=list, description="Of those, the ones the calling member has set"
    )


class MemberEnvVariable(BaseModel):
    name: str
    has_default: bool = Field(..., description="The organization provides a value")
    overridden: bool = Field(..., description="The calling member has set their own value")


class MemberEnvView(BaseModel):
    variables: list[MemberEnvVariable] = Field(default_factory=list)


class MemberEnvUpdate(BaseModel):
    values: dict[str, str] = Field(
        default_factory=dict,
        description="Variable name to the member's value; an empty value removes the override",
    )


class CreateMcpServerRequest(BaseModel):
    id: str | None = None
    name: str
    description: str = ""
    transport: str = "stdio"  # stdio, http, sse, streamable_http, websocket
    endpoint_or_command: str
    guidance: str = ""
    scope: str = "org"
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


class GitHubLinkRequest(BaseModel):
    """Payload to link a GitHub OAuth access token to current user."""

    access_token: str
    username: str | None = None
    avatar_url: str | None = None
    scopes: list[str] = Field(default_factory=list)
    auth_type: str = "oauth"


class GitHubPATRequest(BaseModel):
    """Payload to configure a personal access token for GitHub."""

    pat: str


class GitHubTestPATRequest(BaseModel):
    """Payload to test a personal access token against GitHub."""

    pat: str | None = None


class GitHubCredentialStatus(BaseModel):
    """Sanitized GitHub connection and machine injection status."""

    is_linked: bool
    username: str | None = None
    avatar_url: str | None = None
    scopes: list[str] = Field(default_factory=list)
    auth_type: str = "none"  # "oauth", "pat", "none"
    updated_at: int | None = None
    injection_mode: str = "none"  # "user_token", "app_readonly", "none"


class KnowledgeBaseCreateRequest(BaseModel):
    name: str = Field(..., description="Collection name")
    description: str = Field(default="", description="Collection description")
    scope: str = Field(default="org", description="'org' or 'user'")
    vector_index: str = Field(default="pgvector_hnsw_default", description="Index name")
    embedding_model: str = Field(
        default="text-embedding-3-small (1536d)", description="Embedding model identifier"
    )
    hybrid_search: bool = Field(default=True, description="Enable hybrid BM25/vector search")


class KnowledgeBaseResponse(BaseModel):
    id: str
    name: str
    description: str
    scope: str = "org"
    user_id: str | None = None
    vector_index: str
    embedding_model: str
    hybrid_search: bool
    documents_count: int = 0
    created_at: int
    updated_at: int


class KnowledgeDocumentCreateRequest(BaseModel):
    title: str = Field(..., description="Document title")
    content: str = Field(..., description="Raw text or markdown content")
    source_type: str = Field(default="text", description="Document source type (text, file, url)")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadata dictionary")


class KnowledgeDocumentResponse(BaseModel):
    id: str
    kb_id: str
    title: str
    source_type: str
    chunk_count: int
    created_at: int


class KnowledgeQueryRequest(BaseModel):
    query: str = Field(..., description="Natural language search query")
    limit: int = Field(default=5, ge=1, le=20, description="Max result chunks")


class KnowledgeQueryResultItem(BaseModel):
    id: str
    score: float
    title: str
    content: str
    chunk_index: int


class KnowledgeQueryResponse(BaseModel):
    results: list[KnowledgeQueryResultItem]
    query: str


class CustomSkillCreateRequest(BaseModel):
    name: str = Field(..., description="Skill name or title")
    title: str | None = Field(default=None, description="Display title")
    description: str = Field(default="", description="Skill purpose summary")
    body: str | None = Field(default=None, description="Prompt instructions and guidelines")
    code: str | None = Field(default=None, description="Legacy code fallback")
    signature: str = Field(default="", description="Function signature")
    scope: str = Field(default="org", description="'org' or 'user'")
    is_active: bool = Field(default=True, description="Whether skill is active")


class CustomSkillImportRequest(BaseModel):
    markdown: str = Field(..., description="Contents of a SKILL.md file")
    scope: str = Field(default="user", description="'org' (administrators) or 'user'")


class CustomSkillResponse(BaseModel):
    id: str
    name: str
    title: str = ""
    description: str = ""
    body: str = ""
    scope: str = "org"
    user_id: str | None = None
    runtime: str = "text"
    is_active: bool = True
    signature: str = ""
    code: str = ""
    created_at: int
    updated_at: int


class CustomSkillTestRequest(BaseModel):
    test_arguments: dict[str, Any] = Field(
        default_factory=dict, description="Arguments to invoke skill with"
    )


class CustomSkillTestResponse(BaseModel):
    success: bool
    output: str
    duration_ms: float
    exit_code: int = 0
