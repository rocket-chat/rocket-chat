"""Every backend URL the cockpit calls must exist.

Three shipped bugs were URL typos that unit tests could not see: the budget page called a
path without `/v1`, the GitHub org form PUT the member endpoint, and the knowledge test
query used a route that does not exist. This test reads the web sources and fails on any
`fetch()` target no FastAPI route can serve.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from api.main import app

ROOT = Path(__file__).resolve().parents[2]
WEB_SRC = ROOT / "apps" / "web" / "src"

# Targets handled by Next.js rewrites to the backend or by pages, not API routes.
_FETCH_TARGET = re.compile(r"""fetch\(\s*([`"'])(/[^`"']*)\1""")
# Routes hidden from the OpenAPI schema (probes, development helpers).
_UNDOCUMENTED = ["/v1/ready", "/v1/drain", "/metrics", "/health", "/v1/health"]


def _backend_patterns() -> list[re.Pattern[str]]:
    paths = list(app.openapi()["paths"]) + _UNDOCUMENTED
    # WebSocket and include_in_schema=False routes are not in the schema; list them.
    for route in getattr(app.router, "routes", []):
        candidate = getattr(route, "path", None)
        if candidate:
            paths.append(candidate)
    return [re.compile("^" + re.sub(r"\{[^}/]+\}", "[^/]+", path) + "$") for path in set(paths)]


def _frontend_targets() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for source in WEB_SRC.rglob("*.ts*"):
        text = source.read_text()
        for match in _FETCH_TARGET.finditer(text):
            raw = match.group(2)
            normalized = re.sub(r"\$\{[^}]*\}", "X", raw).split("?")[0].rstrip("/")
            found.append((str(source.relative_to(ROOT)), normalized or "/"))
    return found


def test_the_scan_finds_calls() -> None:
    assert len(_frontend_targets()) > 30


@pytest.mark.parametrize(("source", "target"), sorted(set(_frontend_targets())))
def test_every_fetch_target_is_served_by_the_backend(source: str, target: str) -> None:
    patterns = _backend_patterns()
    assert any(p.match(target) for p in patterns), f"{source} calls {target}, which no route serves"
