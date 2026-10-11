"""Contract tests keeping the documented WebSocket protocol aligned with the code.

The protocol document had silently drifted from the implementation three times
(fictional event names, the wrong envelope key, and a non-existent connection
URL). That drift is not cosmetic: it concealed a dead approval-gate path, because
the frames the docs described were never emitted by the backend. These tests fail
fast whenever the document and the code disagree again.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from api.main import app
from api.sockets.session_socket import SessionSocket
from starlette.routing import WebSocketRoute

from specifications.interfaces.events import EventType

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_DOC = ROOT / "doc" / "api" / "websocket.md"

# Control frames are deliberately not session events: the state sync pushed on
# connect, the keepalive reply, the reconnect replay envelope, and the graceful-drain
# notice sent before a draining pod closes the socket. Every other documented server
# frame must map onto a real EventType member.
CONTROL_FRAMES = {"sync", "pong", "replay", "draining"}

_JSON_BLOCK = re.compile(r"```json\n(.*?)```", re.DOTALL)
_WS_URL = re.compile(r"ws://<host>:\d+(\S+)")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(doc: str, start_marker: str, end_marker: str | None) -> str:
    _, _, remainder = doc.partition(start_marker)
    if end_marker:
        section, _, _ = remainder.partition(end_marker)
        return section
    return remainder


def _json_examples(section: str) -> list[dict[str, Any]]:
    """Parse every fenced JSON example in a document section."""
    examples: list[dict[str, Any]] = []
    for raw in _JSON_BLOCK.findall(section):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as err:
            pytest.fail(f"Malformed JSON example in {PROTOCOL_DOC.name}: {err}")
        if isinstance(parsed, dict):
            examples.append(parsed)
    return examples


def _accepted_client_frames() -> set[str]:
    """The client frames the WebSocket endpoint actually handles."""
    return set(SessionSocket.accepted_frames())


def _collect_websocket_paths(routes: Iterable[Any], prefix: str = "") -> set[str]:
    """Recursively collect WebSocket paths, descending into included routers.

    FastAPI keeps ``include_router`` results lazily behind ``_IncludedRouter``
    wrappers instead of flattening them into ``app.routes``, so a naive scan finds
    nothing.
    """
    paths: set[str] = set()
    for route in routes:
        included_router = getattr(route, "original_router", None)
        if included_router is not None:
            child_prefix = prefix + (route.include_context.prefix or "")
            paths |= _collect_websocket_paths(included_router.routes, child_prefix)
        elif isinstance(route, WebSocketRoute):
            paths.add(f"{prefix}{route.path}")
    return paths


def _registered_websocket_paths() -> set[str]:
    return _collect_websocket_paths(app.routes)


@pytest.fixture(scope="module")
def protocol_doc() -> str:
    return _read(PROTOCOL_DOC)


@pytest.fixture(scope="module")
def server_examples(protocol_doc: str) -> list[dict[str, Any]]:
    examples = _json_examples(_section(protocol_doc, "## 1.", "## 3."))
    assert examples, "No JSON examples found in the server-to-client documentation"
    return examples


@pytest.fixture(scope="module")
def client_examples(protocol_doc: str) -> list[dict[str, Any]]:
    examples = _json_examples(_section(protocol_doc, "## 3.", "## `plan_updated`"))
    assert examples, "No JSON examples found in the client-to-server documentation"
    return examples


def _documented_events(examples: list[dict[str, Any]]) -> set[str]:
    return {example["event_type"] for example in examples if "event_type" in example}


def test_documented_events_are_real_event_types(server_examples: list[dict[str, Any]]) -> None:
    """Every documented ``event_type`` must exist in the ``EventType`` enum."""
    documented = _documented_events(server_examples)
    known = {member.value for member in EventType}

    assert not documented - known, (
        f"Documented event types missing from EventType: {sorted(documented - known)}"
    )
    for example in server_examples:
        if "event_type" in example:
            assert "payload" in example, (
                'Server events must use the {"event_type", "payload"} envelope the endpoint emits'
            )


def test_gate_events_stay_documented(server_examples: list[dict[str, Any]]) -> None:
    """The human-in-the-loop gates must remain documented and therefore wired."""
    assert {"decision_required", "approval_required"} <= _documented_events(server_examples), (
        "Both human-in-the-loop gates must be documented: an undocumented gate is "
        "exactly how the approval flow previously became unreachable dead code"
    )


def test_documented_control_frames_are_known(server_examples: list[dict[str, Any]]) -> None:
    """Non-event server frames must be limited to the known control frames."""
    frames = {
        example["type"]
        for example in server_examples
        if "event_type" not in example and "type" in example
    }

    assert frames <= CONTROL_FRAMES, (
        f"Unexpected server control frames documented: {sorted(frames - CONTROL_FRAMES)}. "
        "Telemetry must use the event_type envelope instead."
    )


def test_documented_client_frames_are_accepted(client_examples: list[dict[str, Any]]) -> None:
    """Every documented client frame must be handled by the WebSocket endpoint."""
    accepted = _accepted_client_frames()
    assert {"turn", "cancel", "answer_question", "approval"} <= accepted, (
        "Could not derive the expected client frames from the WebSocket endpoint source"
    )

    documented = {example["type"] for example in client_examples if "type" in example}
    assert not documented - accepted, (
        f"Documented client frames the endpoint does not handle: {sorted(documented - accepted)}"
    )
    assert {"turn", "answer_question", "approval"} <= documented, (
        "Turn submission, decision answers, and approvals must stay documented"
    )


def test_protocol_doc_catalogues_every_event_type(protocol_doc: str) -> None:
    """Every ``EventType`` member must be documented in the protocol catalogue.

    This keeps the document a complete contract: adding an event without
    documenting it fails here instead of silently shipping an undocumented frame.
    """
    missing = sorted(
        member.value for member in EventType if f"`{member.value}`" not in protocol_doc
    )
    assert not missing, f"EventType members missing from the WebSocket protocol document: {missing}"


def test_documented_websocket_url_is_registered(protocol_doc: str) -> None:
    """The documented connection URL must match a registered WebSocket route."""
    match = _WS_URL.search(protocol_doc)
    assert match, "The protocol document must document a concrete WebSocket URL"

    documented_path = match.group(1).split("?")[0]
    registered = _registered_websocket_paths()
    assert documented_path in registered, (
        f"Documented WebSocket path {documented_path!r} is not registered; "
        f"registered routes: {sorted(registered)}"
    )
