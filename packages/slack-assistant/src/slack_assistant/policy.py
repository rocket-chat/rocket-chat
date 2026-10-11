"""Organization policy for the Slack assistant, as resolved from the `slack` settings domain."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# Slack event `channel_type` -> the policy vocabulary used by `allowed_channel_types`.
_EVENT_TO_POLICY_TYPE = {"channel": "public", "group": "private", "im": "im", "mpim": "mpim"}
ALL_CHANNEL_TYPES = frozenset({"public", "private", "im", "mpim"})


def _as_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, list | tuple):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


@dataclass(frozen=True)
class ChannelProfile:
    """How the assistant behaves in one channel: its persona, house rules, tools and image."""

    agent_persona: str = ""
    instructions: str = ""
    allowed_tools: tuple[str, ...] = ()
    sandbox_image: str = ""

    @classmethod
    def from_mapping(cls, raw: Any) -> ChannelProfile | None:
        if not isinstance(raw, Mapping):
            return None
        return cls(
            agent_persona=str(raw.get("agent_persona") or ""),
            instructions=str(raw.get("instructions") or "")[:2000],
            allowed_tools=tuple(_as_list(raw.get("allowed_tools"))),
            sandbox_image=str(raw.get("sandbox_image") or ""),
        )


def _profiles(raw: Any) -> Mapping[str, ChannelProfile]:
    if not isinstance(raw, Mapping):
        return {}
    found = {str(channel): ChannelProfile.from_mapping(value) for channel, value in raw.items()}
    return {channel: profile for channel, profile in found.items() if profile is not None}


@dataclass(frozen=True)
class SlackOrgPolicy:
    """What the org permits the assistant to do (all fields default to the permissive value)."""

    dm_policy: str = ""
    outbound_channels: frozenset[str] = field(default_factory=frozenset)
    allowed_channel_types: frozenset[str] = ALL_CHANNEL_TYPES
    auto_link_by_verified_email: bool = False
    # Empty means "no organization restriction" (the deployment's env allowlist applies).
    allowed_tools: tuple[str, ...] = ()
    default_agent_persona: str = ""
    # Where "Open in Rocket Chat" links point (empty: no link).
    web_base_url: str = ""
    live_progress: bool = True
    long_output_to_file: bool = True
    long_output_chars: int = 3500
    channel_profiles: Mapping[str, ChannelProfile] = field(default_factory=dict)

    def profile_for(self, channel_id: str) -> ChannelProfile | None:
        return self.channel_profiles.get(channel_id)

    def allows_channel_type(self, event_channel_type: str | None) -> bool:
        policy_type = _EVENT_TO_POLICY_TYPE.get(event_channel_type or "im", "public")
        return policy_type in self.allowed_channel_types

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any] | None) -> SlackOrgPolicy:
        source = settings or {}
        types = frozenset(_as_list(source.get("allowed_channel_types"))) or ALL_CHANNEL_TYPES
        return cls(
            dm_policy=str(source.get("dm_policy") or ""),
            outbound_channels=frozenset(_as_list(source.get("outbound_channels"))),
            allowed_channel_types=types,
            auto_link_by_verified_email=bool(source.get("auto_link_by_verified_email", False)),
            allowed_tools=tuple(_as_list(source.get("allowed_tools"))),
            default_agent_persona=str(source.get("default_agent_persona") or ""),
            web_base_url=str(source.get("web_base_url") or "").rstrip("/"),
            live_progress=bool(source.get("live_progress", True)),
            long_output_to_file=bool(source.get("long_output_to_file", True)),
            long_output_chars=int(source.get("long_output_chars") or 3500),
            channel_profiles=_profiles(source.get("channel_profiles")),
        )
