"""The committed REST reference and OpenAPI schema are exactly what the code generates."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from api.main import app

ROOT = Path(__file__).resolve().parents[2]


def _generator():
    spec = importlib.util.spec_from_file_location(
        "generate_api_reference", ROOT / "scripts" / "generate_api_reference.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_rest_reference_is_current() -> None:
    generator = _generator()
    expected = generator.render_markdown(app.openapi())
    committed = (ROOT / "doc" / "api" / "rest.md").read_text(encoding="utf-8")
    assert committed == expected, (
        "doc/api/rest.md is stale; run `uv run python scripts/generate_api_reference.py`"
    )


def test_the_openapi_schema_is_current() -> None:
    generator = _generator()
    committed = (ROOT / "doc" / "api" / "openapi.json").read_text(encoding="utf-8")
    assert committed == generator.render_schema(app.openapi()), (
        "doc/api/openapi.json is stale; run `uv run python scripts/generate_api_reference.py`"
    )


def test_operational_endpoints_are_documented_for_operators() -> None:
    reference = (ROOT / "doc" / "api" / "rest.md").read_text(encoding="utf-8")
    for needle in ("`GET /metrics`", "`GET /v1/ready`", "`POST /v1/drain`"):
        assert needle in reference
