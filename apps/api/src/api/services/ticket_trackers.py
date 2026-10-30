"""Jira and Linear: their signed webhooks as trigger events, and the session's outcome posted back
to the ticket as a comment.

Both are configured by the operator (``JIRA_WEBHOOK_SECRET``, ``JIRA_BASE_URL``, ``JIRA_EMAIL``,
``JIRA_API_TOKEN``; ``LINEAR_WEBHOOK_SECRET``, ``LINEAR_API_KEY``). A webhook without a configured
secret is refused, never accepted unsigned. Comments are best effort: a tracker that is down never
fails a turn.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

import httpx

from specifications.interfaces.automation import TriggerEvent

logger = logging.getLogger(__name__)

TRACKER_TIMEOUT_SECONDS = 10.0
# A comment is a summary with a link, never the whole answer.
MAX_COMMENT_CHARS = 3000
TICKET_SOURCE_KEY = "trigger_source"
TICKET_KEY = "ticket_key"
TICKET_ID_KEY = "ticket_id"


class TrackerSignatureError(ValueError):
    """A tracker webhook without a valid signature (or with no secret configured)."""


def _verify(secret_env: str, body: bytes, signature: str | None, *, prefix: str = "") -> None:
    secret = os.getenv(secret_env, "")
    if not secret:
        raise TrackerSignatureError(f"{secret_env} is not configured; the webhook is refused")
    expected = prefix + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature.strip()):
        raise TrackerSignatureError("Invalid webhook signature")


def linear_event(body: bytes, headers: dict[str, str]) -> TriggerEvent | None:
    """A Linear issue created or labelled (``Linear-Signature``: hex HMAC-SHA256 of the body)."""
    lowered = {k.lower(): v for k, v in headers.items()}
    _verify("LINEAR_WEBHOOK_SECRET", body, lowered.get("linear-signature"))
    payload = _json(body)
    data = payload.get("data") or {}
    if payload.get("type") != "Issue" or payload.get("action") not in ("create", "update"):
        return None
    labels = tuple(str(label.get("name") or "") for label in data.get("labels") or [])
    return TriggerEvent(
        source="linear_issue",
        delivery_id=str(lowered.get("linear-delivery") or ""),
        key=str(data.get("identifier") or ""),
        title=str(data.get("title") or ""),
        body=str(data.get("description") or ""),
        labels=labels,
        url=str(data.get("url") or payload.get("url") or ""),
        action=str(payload.get("action") or ""),
        actor=str((payload.get("actor") or {}).get("name") or "linear"),
        # The workspace's own members create and label its issues.
        trusted=True,
        installation_id=None,
    )


def jira_event(body: bytes, headers: dict[str, str]) -> TriggerEvent | None:
    """A Jira issue created or updated (``X-Hub-Signature: sha256=<hex>`` of the body)."""
    lowered = {k.lower(): v for k, v in headers.items()}
    _verify("JIRA_WEBHOOK_SECRET", body, lowered.get("x-hub-signature"), prefix="sha256=")
    payload = _json(body)
    if payload.get("webhookEvent") not in ("jira:issue_created", "jira:issue_updated"):
        return None
    issue = payload.get("issue") or {}
    fields = issue.get("fields") or {}
    base = os.getenv("JIRA_BASE_URL", "").rstrip("/")
    key = str(issue.get("key") or "")
    return TriggerEvent(
        source="jira_issue",
        delivery_id=str(lowered.get("x-atlassian-webhook-identifier") or ""),
        key=key,
        title=str(fields.get("summary") or ""),
        body=_jira_text(fields.get("description")),
        labels=tuple(str(label) for label in fields.get("labels") or []),
        url=f"{base}/browse/{key}" if base and key else "",
        action=str(payload.get("webhookEvent") or "").split(":")[-1],
        actor=str((payload.get("user") or {}).get("displayName") or "jira"),
        trusted=True,
    )


def _jira_text(description: Any) -> str:
    """Jira Cloud sends Atlassian Document Format; the plain text is enough for a prompt."""
    if isinstance(description, str):
        return description
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text":
                parts.append(str(node.get("text") or ""))
            for child in node.get("content") or []:
                walk(child)
            if node.get("type") == "paragraph":
                parts.append("\n")

    walk(description)
    return "".join(parts).strip()


def _json(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


async def post_outcome(metadata: dict[str, Any], text: str, link: str = "") -> bool:
    """Comment the session's outcome on the ticket that started it; False when it could not."""
    source, key = str(metadata.get(TICKET_SOURCE_KEY) or ""), str(metadata.get(TICKET_KEY) or "")
    if not key or source not in ("jira_issue", "linear_issue"):
        return False
    body = text.strip()[:MAX_COMMENT_CHARS] + (f"\n\n{link}" if link else "")
    try:
        if source == "jira_issue":
            return await _jira_comment(key, body)
        return await _linear_comment(key, body)
    except httpx.HTTPError as err:
        logger.warning("Could not comment on %s: %s", key, err)
        return False


async def _jira_comment(key: str, text: str) -> bool:
    base, email, token = (
        os.getenv("JIRA_BASE_URL", "").rstrip("/"),
        os.getenv("JIRA_EMAIL", ""),
        os.getenv("JIRA_API_TOKEN", ""),
    )
    if not (base and email and token):
        return False
    document = {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }
    async with httpx.AsyncClient(timeout=TRACKER_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{base}/rest/api/3/issue/{key}/comment", json={"body": document}, auth=(email, token)
        )
    return response.is_success


async def _linear_comment(identifier: str, text: str) -> bool:
    token = os.getenv("LINEAR_API_KEY", "")
    if not token:
        return False
    query = (
        "mutation($issue: String!, $body: String!) "
        "{ commentCreate(input: {issueId: $issue, body: $body}) { success } }"
    )
    async with httpx.AsyncClient(timeout=TRACKER_TIMEOUT_SECONDS) as client:
        response = await client.post(
            "https://api.linear.app/graphql",
            json={"query": query, "variables": {"issue": identifier, "body": text}},
            headers={"Authorization": token},
        )
    return response.is_success and bool(
        ((response.json().get("data") or {}).get("commentCreate") or {}).get("success")
    )
