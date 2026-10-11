"""SKILL.md import: portable prompt skills."""

from __future__ import annotations

import pytest
from api.services.skill_import import SkillImportError, parse_skill_markdown
from fastapi.testclient import TestClient

ADMIN = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "admin_user", "X-User-Role": "admin"}
MEMBER = {
    "X-Tenant-Org-Id": "test_org",
    "X-Tenant-User-Id": "member_user",
    "X-User-Role": "developer",
}
OTHER_ORG = {
    "X-Tenant-Org-Id": "usage_org",
    "X-Tenant-User-Id": "usage_user",
    "X-User-Role": "admin",
}

DOCUMENT = """---
name: release-notes
description: Draft release notes from merged pull requests
---

# Release notes

1. List merged PRs since the last tag.
2. Group them by label.
"""


def test_front_matter_and_body_are_split() -> None:
    skill = parse_skill_markdown(DOCUMENT)
    assert skill.name == "release-notes"
    assert skill.description == "Draft release notes from merged pull requests"
    assert skill.body.startswith("# Release notes")
    assert "---" not in skill.body.splitlines()[0]


def test_crlf_and_bom_documents_parse() -> None:
    skill = parse_skill_markdown("﻿" + DOCUMENT.replace("\n", "\r\n"))
    assert skill.name == "release-notes"


@pytest.mark.parametrize(
    "document, message",
    [
        ("no front matter", "front matter"),
        ("---\nname: ok\n---\n", "no instructions"),
        ("---\ndescription: x\n---\nbody", "'name'"),
        ("---\nname: has spaces\n---\nbody", "'name'"),
        ("---\nname: [unclosed\n---\nbody", "valid YAML"),
        ("---\n- a\n- b\n---\nbody", "mapping"),
        ("---\nname: big\n---\n" + "x" * 70_000, "larger than"),
    ],
)
def test_invalid_documents_are_rejected_with_a_reason(document: str, message: str) -> None:
    with pytest.raises(SkillImportError, match=message):
        parse_skill_markdown(document)


def test_import_creates_a_skill_the_member_can_list(client: TestClient) -> None:
    created = client.post(
        "/v1/skills/import", json={"markdown": DOCUMENT, "scope": "user"}, headers=MEMBER
    )
    assert created.status_code == 200, created.text
    assert created.json()["body"].startswith("# Release notes")

    assert "release-notes" in {s["name"] for s in client.get("/v1/skills", headers=MEMBER).json()}
    assert "release-notes" not in {
        s["name"] for s in client.get("/v1/skills", headers=OTHER_ORG).json()
    }


def test_only_administrators_import_organization_skills(client: TestClient) -> None:
    refused = client.post(
        "/v1/skills/import", json={"markdown": DOCUMENT, "scope": "org"}, headers=MEMBER
    )
    assert refused.status_code == 403
    allowed = client.post(
        "/v1/skills/import", json={"markdown": DOCUMENT, "scope": "org"}, headers=ADMIN
    )
    assert allowed.status_code == 200


def test_a_malformed_document_is_a_422_not_a_500(client: TestClient) -> None:
    response = client.post("/v1/skills/import", json={"markdown": "nope"}, headers=MEMBER)
    assert response.status_code == 422
    assert "front matter" in response.json()["detail"]
