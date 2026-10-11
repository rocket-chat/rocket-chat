"""
Interface specifications for Model Context Protocol (MCP) servers,
schemes (stdio, http, sse), authentication modes, and tool guidance.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class McpTransportScheme(str, Enum):
    """Transport protocol scheme for connecting to the MCP server."""

    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


class McpAuthType(str, Enum):
    """Supported authentication mechanisms for external MCP servers."""

    NONE = "none"
    API_KEY = "api_key"
    BEARER = "bearer"
    CUSTOM_HEADERS = "custom_headers"
    OAUTH2 = "oauth2"
    BASIC = "basic"


class McpAuthScope(str, Enum):
    """Ownership scope of the credentials used to authenticate an MCP server.

    ORG: a single shared service credential (API key, bearer token, or OAuth2
    client-credentials) configured once by an administrator and reused by every
    agent, including the Slack bot, regardless of the end user.
    USER: a per-user credential obtained through an interactive OAuth 2.1
    authorization-code flow, so each user connects their own account.
    """

    ORG = "org"
    USER = "user"


class McpHeaderConfig(BaseModel):
    """Configuration for a single header, supporting masked secrets."""

    name: str = Field(..., description="Header name (e.g. X-API-Key, Authorization)")
    value: str = Field(..., description="Plain value or encrypted secret token")
    is_secret: bool = Field(
        default=False, description="Whether this header value is encrypted write-only secret"
    )


class McpAuthConfig(BaseModel):
    """Comprehensive authentication credentials for connecting to an MCP provider."""

    auth_type: McpAuthType = Field(default=McpAuthType.NONE, description="Authentication mechanism")

    # Bearer / API Key
    api_key_encrypted: str | None = Field(
        default=None, description="Encrypted API key or bearer token"
    )
    header_name: str | None = Field(
        default="Authorization", description="Target header for API key/Bearer"
    )
    header_prefix: str | None = Field(
        default="Bearer", description="Header prefix if any (e.g. Bearer)"
    )

    # Custom Headers
    headers: list[McpHeaderConfig] = Field(default_factory=list, description="Custom headers list")

    # OAuth2 Client Credentials
    oauth_token_url: str | None = Field(default=None, description="Token exchange endpoint")
    oauth_client_id: str | None = Field(default=None, description="Client ID")
    oauth_client_secret_encrypted: str | None = Field(
        default=None, description="Encrypted client secret"
    )
    oauth_scopes: list[str] = Field(default_factory=list, description="Requested OAuth scopes")

    # Basic Auth
    basic_username: str | None = Field(default=None, description="Username for Basic auth")
    basic_password_encrypted: str | None = Field(
        default=None, description="Encrypted password for Basic auth"
    )


class McpToolDefinition(BaseModel):
    """Discovered capability / tool schema from an MCP server."""

    name: str = Field(..., description="Unique tool name")
    description: str = Field(default="", description="Tool documentation")
    parameters_json_schema: dict[str, Any] = Field(
        default_factory=dict, description="JSON Schema for arguments"
    )
    guidance: str | None = Field(
        default=None, description="Optional custom user instruction/hint for this tool"
    )


class McpServerDefinition(BaseModel):
    """Complete specification of an MCP server registered in Rocket Chat."""

    id: str = Field(..., description="Unique identifier of the MCP server")
    name: str = Field(..., description="Human-friendly display name")
    description: str = Field(
        default="", description="High-level description of what this server provides"
    )
    transport: McpTransportScheme = Field(
        default=McpTransportScheme.STDIO, description="Transport scheme"
    )
    endpoint_or_command: str = Field(..., description="HTTP/SSE URL or stdio command")
    guidance: str = Field(
        default="",
        description="Custom context & instructions to guide the autonomous agent when choosing or executing these tools",
    )
    auth: McpAuthConfig = Field(
        default_factory=McpAuthConfig, description="Authentication configuration"
    )
    is_builtin: bool = Field(
        default=False, description="Whether this is a core built-in platform provider"
    )
    tools: list[McpToolDefinition] = Field(
        default_factory=list, description="Cached discovered tools"
    )
    status: str = Field(
        default="connected", description="Server status: connected, probing, offline, error"
    )
    last_probed: str = Field(default="Just now", description="Timestamp of last probe")
    tenant_org_id: str = Field(default="default_org", description="Tenant organization isolation")


class McpCatalogEntry(BaseModel):
    """Curated, pre-configured connector definition for a well-known productivity tool.

    Catalog entries remove the need to hand-code integrations per tool: an
    administrator (or user) installs an entry, which materializes a regular
    ``McpServerDefinition`` using organization-wide service credentials or a
    per-user OAuth flow.
    """

    id: str = Field(..., description="Stable catalog identifier (e.g. 'jira', 'notion')")
    name: str = Field(..., description="Human-friendly connector name")
    description: str = Field(default="", description="What the connector provides")
    category: str = Field(default="productivity", description="Catalog grouping")
    icon: str = Field(default="plug", description="lucide-react icon name")
    docs_url: str | None = Field(default=None, description="Provider setup documentation URL")
    transport: McpTransportScheme = Field(
        default=McpTransportScheme.HTTP, description="Transport scheme"
    )
    endpoint_or_command: str = Field(
        ..., description="Default remote MCP endpoint (admin-overridable)"
    )
    authorization_url: str | None = Field(
        default=None, description="OAuth authorization endpoint (per-user or client-credentials)"
    )
    token_url: str | None = Field(default=None, description="OAuth token endpoint")
    default_scopes: list[str] = Field(default_factory=list, description="Recommended OAuth scopes")
    supported_auth_types: list[McpAuthType] = Field(
        default_factory=lambda: [McpAuthType.OAUTH2],
        description="Authentication mechanisms offered by the provider",
    )
    recommended_scope: McpAuthScope = Field(
        default=McpAuthScope.USER, description="Default credential ownership scope"
    )
    supports_org_wide_auth: bool = Field(
        default=True,
        description=(
            "Whether a shared service credential (API key / bearer / client-credentials) "
            "can authenticate the connector for the whole organization."
        ),
    )
    supports_per_user_oauth: bool = Field(
        default=True, description="Whether per-user interactive OAuth is supported"
    )
    guidance: str = Field(
        default="", description="Custom context injected into the agent prompt for this connector"
    )
