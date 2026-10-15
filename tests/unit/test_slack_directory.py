"""The agent's Slack directory: user groups, people, channel members, permalinks and reads.

A recorded Slack client answers the Web API calls; the tools are called the way the agent
calls them, inside a turn whose conversation is a private channel.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from agent_core.context import ExecutionContext, current_execution_ctx
from agent_core.permissions import approval_needed, is_read_call
from slack_assistant.assistant import SlackAssistant
from slack_assistant.config import SlackAssistantSettings

CURRENT = ("G_CURRENT", "1.0")  # the private channel the agent was asked in

PEOPLE = [
    {"id": "U1", "name": "ada", "real_name": "Ada Lovelace", "tz": "Europe/Paris",
     "tz_label": "Central European Time",
     "profile": {"display_name": "ada", "title": "Backend lead", "email": "ada@corp.io",
                 "status_emoji": ":palm_tree:", "status_text": "On holiday"}},
    {"id": "U2", "name": "adam", "real_name": "Adam Smith", "profile": {"email": "adam@corp.io"}},
    {"id": "U3", "name": "grace", "real_name": "Grace Hopper", "profile": {"email": "g@corp.io"}},
]  # fmt: skip
GROUPS = [
    {"id": "S1", "handle": "backend", "name": "Backend team", "user_count": 2,
     "description": "API and data", "users": ["U1", "U3"]},
    {"id": "S2", "handle": "oncall", "name": "On-call", "user_count": 1, "users": ["U3"]},
]  # fmt: skip
CHANNELS = {
    "C_PUBLIC": {"is_channel": True, "is_private": False},
    "G_SECRET": {"is_channel": True, "is_private": True},
    "D_OTHER": {"is_im": True},
}


class _Slack:
    """Answers like the Web API; ``users_list`` pages one member at a time."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, name: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((name, kwargs))

    async def users_list(self, **kwargs: Any) -> dict[str, Any]:
        self._record("users_list", kwargs)
        index = int(kwargs.get("cursor") or 0)
        nxt = str(index + 1) if index + 1 < len(PEOPLE) else ""
        return {"members": [PEOPLE[index]], "response_metadata": {"next_cursor": nxt}}

    async def users_info(self, user: str) -> dict[str, Any]:
        return {"user": next(p for p in PEOPLE if p["id"] == user)}

    async def usergroups_list(self, **kwargs: Any) -> dict[str, Any]:
        self._record("usergroups_list", kwargs)
        if kwargs.get("include_users"):
            return {"usergroups": GROUPS}
        return {"usergroups": [{k: v for k, v in g.items() if k != "users"} for g in GROUPS]}

    async def usergroups_users_list(self, usergroup: str) -> dict[str, Any]:
        return {"users": next(g["users"] for g in GROUPS if g["id"] == usergroup)}

    async def conversations_info(self, channel: str) -> dict[str, Any]:
        return {"channel": CHANNELS.get(channel, {})}

    async def conversations_members(self, **kwargs: Any) -> dict[str, Any]:
        return {"members": ["U1", "U2"], "response_metadata": {"next_cursor": ""}}

    async def conversations_history(self, **kwargs: Any) -> dict[str, Any]:
        self._record("conversations_history", kwargs)
        return {"messages": [{"user": "U1", "text": f"hello from {kwargs['channel']}", "ts": "5"}]}

    async def conversations_replies(self, **kwargs: Any) -> dict[str, Any]:
        self._record("conversations_replies", kwargs)
        return {"messages": [{"user": "U3", "text": "thread reply", "ts": kwargs["ts"]}]}

    async def chat_getPermalink(self, channel: str, message_ts: str) -> dict[str, Any]:
        return {"permalink": f"https://corp.slack.com/archives/{channel}/p{message_ts}"}


@pytest.fixture
def slack() -> Iterator[tuple[SlackAssistant, _Slack]]:
    client = _Slack()
    assistant = SlackAssistant(orchestrator=MagicMock(), settings=SlackAssistantSettings())
    assistant.app = SimpleNamespace(client=client)  # type: ignore[assignment]
    assistant._slack_api_ready = True
    assistant._session_meta["s1"] = CURRENT
    token = current_execution_ctx.set(
        ExecutionContext(session_id="s1", event_queue=asyncio.Queue(), parent_model="m")
    )
    yield assistant, client
    current_execution_ctx.reset(token)


async def test_user_groups_are_listed_with_how_to_mention_them(slack: Any) -> None:
    assistant, client = slack

    listed = await assistant._tool_list_user_groups()
    filtered = await assistant._tool_list_user_groups(query="api")

    assert (
        "S1 @backend — Backend team (2 members; mention as <!subteam^S1>): API and data" in listed
    )
    assert "@oncall" in listed
    assert "@backend" in filtered and "@oncall" not in filtered
    assert client.calls[0] == (
        "usergroups_list",
        {"include_count": True, "include_disabled": False, "include_users": False},
    )


async def test_a_groups_members_are_found_by_handle(slack: Any) -> None:
    assistant, _ = slack

    members = await assistant._tool_get_user_group_members("@backend")

    assert members.startswith("@backend (Backend team), 2 members:")
    assert "U1 — ada" in members and "U3 — Grace Hopper" in members
    assert "No user group 'nope'" in await assistant._tool_get_user_group_members("nope")


async def test_a_person_is_described_without_their_email(slack: Any) -> None:
    assistant, _ = slack

    described = await assistant._tool_get_user("ada@corp.io")

    assert "U1 — Ada Lovelace (@ada, Backend lead)" in described
    assert "Timezone: Central European Time" in described
    assert "Status: :palm_tree: On holiday" in described
    assert "User groups: @backend" in described
    assert "corp.io" not in described


async def test_users_are_found_across_pages_and_by_partial_name(slack: Any) -> None:
    assistant, client = slack

    exact = await assistant._tool_resolve_user("grace")
    several = await assistant._tool_resolve_user("ada")
    partial = await assistant._tool_resolve_user("lovel")

    assert exact.startswith("U3 — Grace Hopper"), "found on the third page"
    assert sum(1 for name, _ in client.calls if name == "users_list") >= 3
    assert several.startswith("U1 — Ada Lovelace"), "an exact handle wins over partial matches"
    assert partial.startswith("U1 — Ada Lovelace")
    ambiguous = await assistant._tool_get_user("ad")
    assert "Several users match 'ad'" in ambiguous and "U2 — Adam Smith" in ambiguous


async def test_channel_members_and_reads_respect_privacy(slack: Any) -> None:
    assistant, client = slack

    public = await assistant._tool_list_channel_members("C_PUBLIC")
    current = await assistant._tool_list_channel_members()
    secret = await assistant._tool_list_channel_members("G_SECRET")
    other_dm = await assistant._tool_read_channel_history(channel="D_OTHER")

    assert public.startswith("2 members of C_PUBLIC:") and "U2 — Adam Smith" in public
    assert current.startswith("2 members of G_CURRENT"), "the private channel it was asked in"
    assert secret.startswith("Blocked:") and other_dm.startswith("Blocked:")
    assert not any(c[1].get("channel") == "D_OTHER" for c in client.calls)


async def test_another_public_thread_and_channel_can_be_read(slack: Any) -> None:
    assistant, _ = slack

    history = await assistant._tool_read_channel_history(channel="C_PUBLIC")
    thread = await assistant._tool_read_thread(channel="C_PUBLIC", thread_ts="9.9")
    missing_ts = await assistant._tool_read_thread(channel="C_PUBLIC")

    assert "hello from C_PUBLIC" in history
    assert "thread reply" in thread
    assert missing_ts.startswith("Error: give the thread_ts")


async def test_a_permalink_is_returned_for_readable_messages_only(slack: Any) -> None:
    assistant, _ = slack

    assert await assistant._tool_get_permalink("C_PUBLIC", "123.456") == (
        "https://corp.slack.com/archives/C_PUBLIC/p123.456"
    )
    assert (await assistant._tool_get_permalink("G_SECRET", "1.2")).startswith("Blocked:")


async def test_missing_scope_is_explained(slack: Any) -> None:
    assistant, client = slack

    async def refused(**_: Any) -> dict[str, Any]:
        raise RuntimeError("missing_scope")

    client.usergroups_list = refused
    assert "usergroups:read" in await assistant._tool_list_user_groups()


def test_slack_reads_need_no_approval_and_sends_are_publications() -> None:
    for name in ("slack_list_user_groups", "slack_get_user", "slack_read_thread"):
        assert is_read_call(name, {}), name
        assert approval_needed("ask", name, {}) is None
    need = approval_needed("auto_edit", "slack_send_message", {"channel": "C1", "text": "x"})
    assert need is not None and need.action_type == "slack_send_message"


def test_the_directory_tools_join_the_slack_toolset() -> None:
    from agent_core.tools.registry import ToolRegistry

    registry = ToolRegistry()
    SlackAssistant(orchestrator=MagicMock(), tool_registry=registry)

    names = {d.name for d in registry.catalog()}
    assert {
        "slack_list_user_groups", "slack_get_user_group_members", "slack_get_user",
        "slack_list_channel_members", "slack_get_permalink",
    } <= names  # fmt: skip
