"""Contract test keeping the REST API reference aligned with the code.

The REST document had drifted silently: it described a health endpoint as the
database check (it is a static liveness signal), documented a decision-submission
path that does not exist, and omitted every operational endpoint shipped in wave 3
(``/v1/ready``, ``/metrics``, ``/v1/drain``) plus the memory and audit routes. A
reference that describes endpoints the code does not serve is worse than no
reference, so this test fails whenever a key endpoint lands without documentation
or a documented endpoint stops being registered.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from api.main import app
from starlette.routing import Route

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "doc" / "api" / "rest.md"

# The endpoints the document must cover, as ``(method, path)``. The path is the
# literal the document is expected to contain; comparison against the router
# normalises path parameters (``{session_id}`` vs ``{id}``).
REQUIRED_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("GET", "/v1/ready"),
    ("POST", "/v1/drain"),
    ("GET", "/metrics"),
    ("GET", "/v1/sessions/{session_id}/turns"),
    ("POST", "/v1/sessions/{session_id}/fork"),
    ("PATCH", "/v1/sessions/{session_id}/flags"),
    ("GET", "/v1/sessions/{session_id}/attachments"),
    ("POST", "/v1/sessions/{session_id}/attachments"),
    ("POST", "/v1/sessions/{session_id}/questions/{question_id}"),
    ("POST", "/v1/sessions/{session_id}/approvals/{action_id}"),
    ("GET", "/v1/memories"),
    ("DELETE", "/v1/memories/{memory_id}"),
    ("GET", "/v1/admin/audit"),
    ("GET", "/v1/slack/status"),
)

_PARAM = re.compile(r"\{[^}]+\}")


def _normalise(path: str) -> str:
    return _PARAM.sub("{}", path.split("?", 1)[0])


def _registered_paths(routes: Iterable[Any], prefix: str = "") -> set[str]:
    """Collect HTTP paths, descending into lazily included routers.

    FastAPI wraps ``include_router`` results in an object carrying
    ``original_router`` rather than flattening them into ``app.routes``.
    """
    paths: set[str] = set()
    for route in routes:
        included_router = getattr(route, "original_router", None)
        if included_router is not None:
            child_prefix = prefix + (route.include_context.prefix or "")
            paths |= _registered_paths(included_router.routes, child_prefix)
        elif isinstance(route, Route):
            paths.add(_normalise(f"{prefix}{route.path}"))
    return paths


def test_every_required_endpoint_is_registered() -> None:
    registered = _registered_paths(app.routes)
    missing = [
        (method, path) for method, path in REQUIRED_ENDPOINTS if _normalise(path) not in registered
    ]
    assert not missing, f"Documented endpoints not served by the app: {missing}"


def test_every_required_endpoint_is_documented() -> None:
    doc = DOC.read_text(encoding="utf-8")
    missing = [f"{method} {path}" for method, path in REQUIRED_ENDPOINTS if path not in doc]
    assert not missing, f"Registered endpoints missing from the REST reference: {missing}"


def test_health_is_documented_as_static_liveness_not_a_database_check() -> None:
    doc = DOC.read_text(encoding="utf-8")
    assert "Static **liveness** signal" in doc, (
        "The reference must describe /v1/health as static liveness; the deep database "
        "check lives at /v1/ready, and conflating them hides a restart-loop hazard."
    )
