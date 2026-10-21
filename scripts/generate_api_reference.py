#!/usr/bin/env python3
"""Generate the REST API reference (``doc/api/rest.md``) and ``doc/api/openapi.json``.

Both come from the running app's own OpenAPI schema, so the reference cannot describe an
endpoint the code does not serve or miss one it does. ``tests/unit/test_api_reference.py``
fails when the committed files are stale; regenerate with::

    uv run python scripts/generate_api_reference.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN = ROOT / "doc" / "api" / "rest.md"
SCHEMA = ROOT / "doc" / "api" / "openapi.json"
METHOD_ORDER = {"get": 0, "post": 1, "put": 2, "patch": 3, "delete": 4}

_INTRO = """\
---
title: REST API
description: Every endpoint the Rocket Chat API serves, generated from its OpenAPI schema.
---

# REST API

Every endpoint below is generated from the API's own OpenAPI schema, so it is always the
current surface. The machine-readable schema is [`openapi.json`](/api/openapi.json); a running
API also serves interactive documentation at `/docs` and `/redoc`.

**Authentication.** Browsers use the session cookie set at sign-in; programmatic clients send
`Authorization: Bearer <token>` with a personal API token (`POST /v1/user/api-tokens`), or a guest token for a client without an account (`POST /v1/admin/api-tokens`). Every endpoint is scoped to the caller's organization.
Endpoints under `/v1/admin` need an administrator; a *read-only* member can read but not change
sessions. Errors are JSON `{"detail": ...}` with the usual HTTP status codes (`401` not signed
in, `403` not allowed, `404` not found or not yours, `422` invalid input).

For live updates use the [WebSocket protocol](/api/websocket).
"""


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _type_of(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return _ref_name(schema["$ref"])
    if "anyOf" in schema:
        parts = [_type_of(s) for s in schema["anyOf"] if s.get("type") != "null"]
        return " or ".join(parts) or "any"
    kind = schema.get("type", "any")
    if kind == "array":
        return f"list of {_type_of(schema.get('items', {}))}"
    if "enum" in schema:
        return " / ".join(f"`{v}`" for v in schema["enum"])
    return str(kind)


def _fields(schemas: dict[str, Any], schema: dict[str, Any]) -> list[tuple[str, str, bool, str]]:
    if "$ref" in schema:
        schema = schemas.get(_ref_name(schema["$ref"]), {})
    required = set(schema.get("required", []))
    return [
        (name, _type_of(prop), name in required, str(prop.get("description", "")).strip())
        for name, prop in schema.get("properties", {}).items()
    ]


def _table(rows: list[tuple[str, ...]], header: tuple[str, ...]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(":---" for _ in header) + " |"]
    for row in rows:
        lines.append(
            "| " + " | ".join(cell.replace("|", "\\|").replace("\n", " ") for cell in row) + " |"
        )
    return [*lines, ""]


def _operation(schemas: dict[str, Any], path: str, method: str, op: dict[str, Any]) -> list[str]:
    lines = [f"### `{method.upper()} {path}`", ""]
    summary = str(op.get("summary", "")).strip()
    description = str(op.get("description", "")).strip()
    if description:
        lines += [description, ""]
    elif summary:
        lines += [summary, ""]
    params = [
        (p["name"], p["in"], "yes" if p.get("required") else "no", _type_of(p.get("schema", {})))
        for p in op.get("parameters", [])
    ]
    if params:
        lines += ["**Parameters**", "", *_table(params, ("Name", "In", "Required", "Type"))]
    body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
    if body:
        fields = _fields(schemas, body)
        if fields:
            rows = [(n, t, "yes" if r else "no", d) for n, t, r, d in fields]
            lines += [
                "**Request body**",
                "",
                *_table(rows, ("Field", "Type", "Required", "Description")),
            ]
    statuses = sorted(code for code in op.get("responses", {}) if code != "422")
    if statuses:
        lines += ["**Responses:** " + ", ".join(f"`{code}`" for code in statuses), ""]
    return lines


def render_markdown(spec: dict[str, Any]) -> str:
    schemas = spec.get("components", {}).get("schemas", {})
    by_tag: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for path, operations in spec["paths"].items():
        for method, op in operations.items():
            for tag in op.get("tags") or ["Other"]:
                by_tag.setdefault(tag, []).append((path, method, op))
    out = [_INTRO]
    for tag in sorted(by_tag, key=str.lower):
        out += [f"## {tag}", ""]
        for path, method, op in sorted(
            by_tag[tag], key=lambda item: (item[0], METHOD_ORDER.get(item[1], 9))
        ):
            out += _operation(schemas, path, method, op)
    return "\n".join(out).rstrip() + "\n"


def render_schema(spec: dict[str, Any]) -> str:
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from api.main import app

    spec = app.openapi()
    MARKDOWN.write_text(render_markdown(spec), encoding="utf-8")
    SCHEMA.write_text(render_schema(spec), encoding="utf-8")
    print(f"Wrote {MARKDOWN.relative_to(ROOT)} and {SCHEMA.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
