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
