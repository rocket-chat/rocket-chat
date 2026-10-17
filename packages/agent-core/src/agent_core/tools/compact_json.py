"""Compact rendering of structured JSON tool results (GitHub, Atlassian, MCP).

Drops nulls, empties, and metadata noise, rendering as concise YAML-like text
to significantly preserve token budget while keeping essential fields.
"""

from __future__ import annotations

import json
from typing import Any

# Metadata keys that add token weight without assisting agent reasoning
_NOISY_KEYS = frozenset(
    {
        "avatarUrls",
        "avatar_url",
        "gravatar_id",
        "iconUrl",
        "self",
        "expand",
        "operations",
        "versionedRepresentations",
        "node_id",
        "_links",
        "links",
        "diff_url",
        "patch_url",
        "issue_url",
        "commits_url",
        "review_comments_url",
        "statuses_url",
        "comments_url",
        "events_url",
        "html_url",
        "author_association",
        "reactions",
        "watchers_count",
        "stargazers_count",
        "open_issues_count",
        "forks_count",
        "timetracking",
        "workratio",
        "schema",
        "names",
    }
)


def clean_structured_data(data: Any) -> Any:
    """Recursively prune nulls, empties, and noisy metadata keys from data structures."""
    if isinstance(data, dict):
        cleaned: dict[str, Any] = {}
        for key, val in data.items():
            if key in _NOISY_KEYS:
                continue
            child = clean_structured_data(val)
            if child is not None and child != "" and child != [] and child != {}:
                cleaned[key] = child
        return cleaned if cleaned else None
    if isinstance(data, list):
        cleaned_list = [
            clean_structured_data(item)
            for item in data
            if item is not None and item != "" and item != [] and item != {}
        ]
        return [c for c in cleaned_list if c is not None and c != "" and c != {}]
    return data


def format_yaml_like(data: Any, indent: int = 0) -> str:
    """Render dict/list as clean, concise YAML-like text without JSON punctuation overhead."""
    prefix = "  " * indent
    if isinstance(data, dict):
        lines: list[str] = []
        for key, val in data.items():
            if isinstance(val, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.append(format_yaml_like(val, indent + 1))
            else:
                lines.append(f"{prefix}{key}: {val}")
        return "\n".join(lines)
    if isinstance(data, list):
        lines = []
        for item in data:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.append(format_yaml_like(item, indent + 1))
            else:
                lines.append(f"{prefix}- {item}")
        return "\n".join(lines)
    return f"{prefix}{data}"


def compact_structured_payload(text: str) -> str:
    """If ``text`` is JSON, clean and render as compact text; otherwise return as-is."""
    stripped = text.strip()
    if not (
        (stripped.startswith("{") and stripped.endswith("}"))
        or (stripped.startswith("[") and stripped.endswith("]"))
    ):
        return text
    try:
        parsed = json.loads(stripped)
    except Exception:
        return text

    cleaned = clean_structured_data(parsed)
    if cleaned is None:
        return "{}"
    rendered = format_yaml_like(cleaned)
    return rendered if len(rendered) < len(text) else text
