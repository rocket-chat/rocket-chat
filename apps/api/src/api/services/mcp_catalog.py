"""Curated connector catalog for well-known productivity MCP servers.

Provides a declarative registry of remote Model Context Protocol servers
(Jira/Confluence, Notion, Google Drive, GitHub, Slack, Linear, ...) so that
organizations can onboard third-party tools without hand-coding a bespoke
adapter per provider.

Each entry declares which authentication modes the provider supports:
- Organization-wide service credentials (API key, bearer token, Basic, or
  OAuth2 client-credentials) that a single administrator configures once and
  every agent (including the Slack bot) reuses, independent of the end user.
- Per-user interactive OAuth 2.1, where each user authorizes their own account.

Endpoints are sensible provider defaults and remain admin-overridable at
install time, since vendors periodically revise their remote MCP URLs.
"""

from __future__ import annotations

from specifications.interfaces.mcp import (
    McpAuthScope,
    McpAuthType,
    McpCatalogEntry,
    McpTransportScheme,
)

_CATALOG: list[McpCatalogEntry] = [
    McpCatalogEntry(
        id="jira",
        name="Jira & Confluence (Atlassian)",
        description=(
            "Read and update Jira issues, sprints, and Confluence pages. "
            "Supports org-wide service accounts and per-user OAuth."
        ),
        category="productivity",
        icon="square-kanban",
        docs_url="https://www.atlassian.com/platform/remote-mcp-server",
        transport=McpTransportScheme.SSE,
        endpoint_or_command="https://mcp.atlassian.com/v1/sse",
        authorization_url="https://auth.atlassian.com/authorize",
        token_url="https://auth.atlassian.com/oauth/token",
        default_scopes=["read:jira-work", "write:jira-work", "read:confluence-content.all"],
        supported_auth_types=[McpAuthType.OAUTH2, McpAuthType.API_KEY, McpAuthType.BEARER],
        recommended_scope=McpAuthScope.USER,
        supports_org_wide_auth=True,
        supports_per_user_oauth=True,
        guidance=(
            "Use this connector to triage Jira issues, link them to code changes, "
            "and summarize Confluence specifications referenced in the thread."
        ),
    ),
    McpCatalogEntry(
        id="notion",
        name="Notion",
        description=(
            "Search and read Notion pages and databases. Supports a shared internal "
            "integration token (org-wide) or per-user OAuth."
        ),
        category="productivity",
        icon="notebook",
        docs_url="https://developers.notion.com/docs/mcp",
        transport=McpTransportScheme.HTTP,
        endpoint_or_command="https://mcp.notion.com/mcp",
        authorization_url="https://api.notion.com/v1/oauth/authorize",
        token_url="https://api.notion.com/v1/oauth/token",
        default_scopes=[],
        supported_auth_types=[McpAuthType.API_KEY, McpAuthType.BEARER, McpAuthType.OAUTH2],
        recommended_scope=McpAuthScope.ORG,
        supports_org_wide_auth=True,
        supports_per_user_oauth=True,
        guidance=(
            "Use this connector to retrieve product specs, RFCs, and meeting notes "
            "stored in the organization's Notion workspace."
        ),
    ),
    McpCatalogEntry(
        id="google-drive",
        name="Google Drive & Workspace",
        description=(
            "Search and read Google Drive documents, Sheets, and Slides. Google "
            "requires per-user consent or domain-wide delegated service accounts."
        ),
        category="productivity",
        icon="hard-drive",
        docs_url="https://developers.google.com/workspace/guides/configure-mcp",
        transport=McpTransportScheme.HTTP,
        endpoint_or_command="https://drivemcp.googleapis.com/mcp/v1",
        authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        default_scopes=[
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/documents.readonly",
        ],
        supported_auth_types=[McpAuthType.OAUTH2],
        recommended_scope=McpAuthScope.USER,
        supports_org_wide_auth=False,
        supports_per_user_oauth=True,
        guidance=(
            "Use this connector to read design docs and spreadsheets from Google "
            "Drive. Access is scoped to the signed-in user's own permissions."
        ),
    ),
    McpCatalogEntry(
        id="github",
        name="GitHub",
        description=(
            "Browse repositories, pull requests, issues, and Actions. Supports a "
            "shared service PAT (org-wide) or per-user OAuth."
        ),
        category="engineering",
        icon="github",
        docs_url="https://docs.github.com/en/copilot/customizing-copilot/using-model-context-protocol",
        transport=McpTransportScheme.HTTP,
        endpoint_or_command="https://api.githubcopilot.com/mcp/",
        default_scopes=["repo", "read:org"],
        supported_auth_types=[McpAuthType.BEARER, McpAuthType.API_KEY, McpAuthType.OAUTH2],
        recommended_scope=McpAuthScope.ORG,
        supports_org_wide_auth=True,
        supports_per_user_oauth=True,
        guidance=(
            "Use this connector to inspect repositories, review pull requests, and "
            "cross-reference issues beyond the local sandbox checkout."
        ),
    ),
    McpCatalogEntry(
        id="slack",
        name="Slack",
        description=(
            "Search workspace messages and channels using a shared bot token "
            "(org-wide) so agents can operate without a per-user identity."
        ),
        category="productivity",
        icon="message-square",
        docs_url="https://docs.slack.dev/mcp",
        transport=McpTransportScheme.HTTP,
        endpoint_or_command="https://mcp.slack.com/mcp",
        default_scopes=[
            "channels:history",
            "groups:history",
            "im:history",
            "users:read",
            "search:read",
        ],
        supported_auth_types=[McpAuthType.BEARER, McpAuthType.OAUTH2],
        recommended_scope=McpAuthScope.ORG,
        supports_org_wide_auth=True,
        supports_per_user_oauth=True,
        guidance=(
            "Use this connector to search Slack history for prior decisions and "
            "context relevant to the current request."
        ),
    ),
    McpCatalogEntry(
        id="linear",
        name="Linear",
        description=(
            "Read and update Linear issues, projects, and cycles. Supports per-user "
            "OAuth or a shared workspace API key."
        ),
        category="engineering",
        icon="list-checks",
        docs_url="https://linear.app/docs/mcp",
        transport=McpTransportScheme.SSE,
        endpoint_or_command="https://mcp.linear.app/sse",
        authorization_url="https://linear.app/oauth/authorize",
        token_url="https://api.linear.app/oauth/token",
        default_scopes=["read", "issues:create"],
        supported_auth_types=[McpAuthType.OAUTH2, McpAuthType.API_KEY],
        recommended_scope=McpAuthScope.USER,
        supports_org_wide_auth=True,
        supports_per_user_oauth=True,
        guidance=(
            "Use this connector to look up Linear tickets, update statuses, and "
            "link engineering work to tracked issues."
        ),
    ),
]

_BY_ID: dict[str, McpCatalogEntry] = {entry.id: entry for entry in _CATALOG}


def get_catalog_entries() -> list[McpCatalogEntry]:
    """Return the full curated connector catalog."""
    return list(_CATALOG)


def get_catalog_entry(entry_id: str) -> McpCatalogEntry | None:
    """Return a single catalog entry by its stable identifier."""
    return _BY_ID.get(entry_id)
