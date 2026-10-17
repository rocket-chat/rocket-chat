"""Tests for compact rendering of structured JSON tool results (Item B5)."""

from __future__ import annotations

import json

from agent_core.tools.compact_json import (
    clean_structured_data,
    compact_structured_payload,
)


def test_clean_structured_data_drops_nulls_empties_and_noise() -> None:
    raw = {
        "id": "PROJ-123",
        "key": "PROJ-123",
        "self": "https://jira.example.com/rest/api/2/issue/10001",
        "expand": "names,schema",
        "fields": {
            "summary": "Fix connection pool exhaustion",
            "description": "Pool runs out of sockets under peak load.",
            "avatarUrls": {"48x48": "https://jira.example.com/avatar/48"},
            "iconUrl": "https://jira.example.com/icon.png",
            "empty_list": [],
            "empty_dict": {},
            "null_field": None,
            "status": {"name": "In Progress"},
        },
    }

    cleaned = clean_structured_data(raw)
    assert cleaned is not None
    assert "self" not in cleaned
    assert "expand" not in cleaned
    assert "avatarUrls" not in cleaned["fields"]
    assert "iconUrl" not in cleaned["fields"]
    assert "empty_list" not in cleaned["fields"]
    assert "null_field" not in cleaned["fields"]
    assert cleaned["fields"]["summary"] == "Fix connection pool exhaustion"
    assert cleaned["fields"]["status"]["name"] == "In Progress"


def test_github_pr_payload_compaction() -> None:
    github_payload = {
        "number": 42,
        "title": "Add compaction checkpoints",
        "node_id": "PR_kwDO12345",
        "user": {
            "login": "octocat",
            "avatar_url": "https://github.com/images/error/octocat_happy.gif",
            "gravatar_id": "",
            "type": "User",
        },
        "labels": [{"name": "enhancement", "color": "a2eeef"}],
        "state": "open",
        "draft": False,
        "body": "Implements durable compaction checkpoints in DB.",
    }

    compacted = compact_structured_payload(json.dumps(github_payload))
    assert "node_id" not in compacted
    assert "avatar_url" not in compacted
    assert "gravatar_id" not in compacted
    assert "number: 42" in compacted
    assert "Add compaction checkpoints" in compacted
    assert "octocat" in compacted
    assert len(compacted) < len(json.dumps(github_payload))


def test_non_json_string_preserved() -> None:
    plain_text = "Standard stdout: build passed successfully (exit 0)"
    assert compact_structured_payload(plain_text) == plain_text
