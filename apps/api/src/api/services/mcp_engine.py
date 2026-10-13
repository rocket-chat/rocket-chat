"""Model Context Protocol (MCP) Client Engine and OAuth 2.1 Service.

Supports stdio, sse, streamable_http, and websocket transports with RFC 9728/PKCE OAuth flows.
"""

import asyncio
import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from agent_core.tools.safe_http import resolve_public_target
from config_engine.crypto import CredentialCipher
from mcp_runner.client import ExitReport, RunnerError

from api.auth import oauth_state
from api.services.mcp_runner import connect_stdio, runner_config
from api.services.mcp_runtime import (
    REMOTE_TRANSPORTS,
    McpServerTarget,
    auth_headers,
    canonical_transport,
    probe_target,
    summarize_tool,
)

logger = logging.getLogger(__name__)

_cipher = CredentialCipher()


def generate_pkce_pair() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return verifier, challenge


MCP_OAUTH_COOKIE = "rocket_mcp_oauth"
OAUTH_FLOW_TTL_SECONDS = oauth_state.STATE_TTL_SECONDS


@dataclass(frozen=True)
class OAuthAuthorization:
    """The provider URL plus the browser cookie holding the flow's secret (the PKCE verifier)."""

    url: str
    cookie_value: str


def create_oauth_authorization_url(
    server_id: str,
    user_id: str,
    auth_config: dict[str, Any],
    redirect_uri: str,
    org_id: str = "",
    member_mode: bool = False,
) -> OAuthAuthorization:
    """Construct the RFC 9728 / OAuth 2.1 PKCE authorization redirect.

    Nothing is kept in this process: the state is a signed token naming the member, the
    organization, the server and the token endpoint, and the PKCE verifier travels only in an
    HttpOnly cookie bound to that state. Any replica can therefore finish the flow.
    """
    auth_url = auth_config.get("oauth_authorization_url") or auth_config.get("authorization_url")
    if not auth_url:
        endpoint = auth_config.get("endpoint_or_command", "")
        if endpoint.startswith(("http://", "https://")):
            auth_url = endpoint.rstrip("/") + "/oauth/authorize"
        else:
            raise ValueError("No OAuth authorization URL configured or discoverable for MCP server")

    client_id = auth_config.get("oauth_client_id") or "rocket-chat"
    scopes = auth_config.get("oauth_scopes") or "mcp:tools"

    verifier, challenge = generate_pkce_pair()
    state = oauth_state.sign(
        {
            "server_id": server_id,
            "user_id": user_id,
            "org_id": org_id,
            "redirect_uri": redirect_uri,
            "token_url": auth_config.get("oauth_token_url"),
            "client_id": client_id,
            "member_mode": member_mode,
            "nonce": secrets.token_urlsafe(8),
        }
    )
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return OAuthAuthorization(
        url=f"{auth_url}?{urlencode(params)}",
        cookie_value=oauth_state.sign({"state": state, "cv": verifier}),
    )


def take_oauth_state(state: str, cookie_value: str | None) -> dict[str, Any]:
    """Verify a returning authorization and recover its flow data, or raise ``ValueError``.

    The state must be genuine and unexpired, and the cookie must be the one issued with it
    (so the browser finishing the flow is the browser that started it).
    """
    claims = oauth_state.verify(state)
    flow = oauth_state.verify(cookie_value or "")
    if flow.get("state") != state:
        raise ValueError("OAuth state does not belong to this browser")
    return {**claims, "code_verifier": flow["cv"]}


async def _post_token_request(token_url: str, payload: dict[str, str]) -> dict[str, Any]:
    """POST to a token endpoint after the SSRF check (members can configure these URLs)."""
    await resolve_public_target(token_url)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            token_url,
            data=payload,
            headers={"Accept": "application/json", "User-Agent": "RocketChat-MCP/1.0"},
        )
    if resp.status_code != 200:
        # The body can echo credentials; log the status only.
        logger.error("OAuth token request failed with status %d", resp.status_code)
        raise ValueError(f"OAuth token request failed: HTTP {resp.status_code}")
    data = resp.json()
    if not isinstance(data, dict) or not data.get("access_token"):
        raise ValueError("OAuth token response did not contain an access_token")
    return data


async def exchange_oauth_code(
    code: str, state_data: dict[str, Any], client_secret: str | None = None
) -> dict[str, Any]:
    """Exchange an authorization code (with the stored PKCE verifier) for tokens."""
    token_url = state_data.get("token_url")
    if not token_url:
        raise ValueError("No token URL configured for OAuth exchange")
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": state_data["redirect_uri"],
        "client_id": state_data["client_id"],
        "code_verifier": state_data["code_verifier"],
    }
    if client_secret:
        payload["client_secret"] = client_secret
    return await _post_token_request(token_url, payload)


# Stored fields that hold the connected account; they survive a re-save of the server form.
OAUTH_TOKEN_KEYS = frozenset({"access_token_enc", "refresh_token_enc", "token_type", "expires_at"})

# Refresh a little early so a token never expires mid-call.
TOKEN_REFRESH_MARGIN_SECONDS = 60


def store_oauth_tokens(auth: dict[str, Any], tokens: dict[str, Any]) -> None:
    """Persist tokens encrypted at rest; drops any legacy plaintext copies."""
    auth["access_token_enc"] = _cipher.encrypt(str(tokens["access_token"]))
    if tokens.get("refresh_token"):
        auth["refresh_token_enc"] = _cipher.encrypt(str(tokens["refresh_token"]))
    auth["token_type"] = tokens.get("token_type") or auth.get("token_type") or "Bearer"
    auth["expires_at"] = time.time() + float(tokens.get("expires_in") or 3600)
    auth.pop("access_token", None)
    auth.pop("refresh_token", None)


def oauth_token_needs_refresh(auth: dict[str, Any]) -> bool:
    expires_at = auth.get("expires_at")
    return bool(
        auth.get("refresh_token_enc")
        and expires_at
        and time.time() >= float(expires_at) - TOKEN_REFRESH_MARGIN_SECONDS
    )


async def refresh_oauth_tokens(auth: dict[str, Any]) -> bool:
    """Refresh the stored access token in place; returns whether it succeeded."""
    token_url = auth.get("oauth_token_url")
    if not token_url or not auth.get("refresh_token_enc"):
        return False
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": _cipher.decrypt(auth["refresh_token_enc"]),
        "client_id": auth.get("oauth_client_id") or "rocket-chat",
    }
    if auth.get("oauth_client_secret"):
        payload["client_secret"] = _cipher.decrypt(auth["oauth_client_secret"])
    try:
        tokens = await _post_token_request(token_url, payload)
    except Exception as err:
        logger.warning("Failed refreshing MCP OAuth token: %s", err)
        return False
    store_oauth_tokens(auth, tokens)
    return True


async def probe_and_fetch_tools(
    transport: str,
    endpoint_or_command: str,
    headers: dict[str, str] | None = None,
    auth_data: dict[str, Any] | None = None,
    timeout_seconds: float = 10.0,
    org_id: str | None = None,
    install_command: str | None = None,
) -> tuple[str, list[dict[str, Any]], str | None]:
    """Connect to an MCP server and list its tools; the status reflects what really happened.

    ``connected`` means an MCP handshake and ``tools/list`` succeeded. Anything else is
    ``offline`` (unreachable, timed out) or ``error`` (refused, invalid, unsupported) with the
    reason - there is no "assume it works" path.
    """
    kind = canonical_transport(transport)
    target = endpoint_or_command.strip()

    if kind == "stdio":
        return await _probe_command(
            target, headers or {}, timeout_seconds, org_id or "default", install_command or ""
        )
    if kind not in REMOTE_TRANSPORTS:
        return "error", [], f"Unsupported MCP transport '{transport}' (use http or sse)."
    if not target.startswith(("http://", "https://")):
        return "error", [], f"Invalid URL for {kind} transport: {target}"

    request_headers = dict(headers or {})
    if auth_data and "Authorization" not in request_headers:
        request_headers.update(auth_headers(auth_data, _cipher))
    return await probe_target(
        McpServerTarget("probe", "probe", kind, target, headers=request_headers), timeout_seconds
    )


async def _probe_command(
    command_line: str,
    headers: dict[str, str],
    timeout_seconds: float,
    org_id: str,
    install_command: str,
) -> tuple[str, list[dict[str, Any]], str | None]:
    """List a command server's tools by starting it on the MCP runner (never in this process).

    The command is an arbitrary program: running it here would put it next to the database
    credentials and the master key, which is why a deployment without a runner cannot test one.
    """
    if not command_line.strip():
        return "error", [], "Empty command"
    config = runner_config()
    if config is None:
        return (
            "offline",
            [],
            "Command servers need the MCP runner, which is not deployed for this installation.",
        )
    report = ExitReport()
    target = McpServerTarget(
        "probe", "probe", "stdio", command_line, headers=headers, setup=install_command
    )
    try:
        async with asyncio.timeout(max(timeout_seconds, 700.0 if install_command else 30.0)):
            async with connect_stdio(config, org_id, report)(target) as client:
                result = await client.list_tools()
    except TimeoutError:
        return "offline", [], "The command did not answer the MCP handshake in time."
    except RunnerError as err:
        return "offline", [], str(err)
    except Exception as err:  # the SDK wraps transport failures in exception groups
        reason = report.describe() or "; ".join(_leaf_messages(err)) or str(err)
        return "error", [], f"The command did not start an MCP server. {reason}".strip()
    return "connected", [summarize_tool(t) for t in result.tools], None


def _leaf_messages(error: BaseException) -> list[str]:
    nested = getattr(error, "exceptions", None)
    if not nested:
        return [str(error)]
    return [message for inner in nested for message in _leaf_messages(inner)]
