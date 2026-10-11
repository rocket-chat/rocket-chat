"""The agent's browser tool: what it runs, and how it reports what it saw."""

from __future__ import annotations

import json
from typing import Any

import pytest
from agent_core.tools import browser
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import ExecResult

REPORT = {
    "url": "http://localhost:3000/",
    "status": 200,
    "title": "Demo App",
    "text": "Hello browser\nClick me",
    "accessibility": '- heading "Hello browser" [level=1]\n- button "Click me"',
    "console_errors": ["boom from page"],
    "failed_requests": ["GET http://localhost:3000/missing.json net::ERR_ABORTED"],
}


class _Driver(FakeSandboxDriver):
    def __init__(self, result: ExecResult) -> None:
        super().__init__()
        self.result = result
        self.commands: list[str] = []

    async def exec_command(self, session_id: str, command: str, *a: Any, **k: Any) -> ExecResult:
        self.commands.append(command)
        return self.result


def _ok(report: dict[str, Any]) -> ExecResult:
    return ExecResult(0, "some npm noise\n" + json.dumps(report) + "\n", "", 1)


async def test_the_report_is_readable_and_includes_what_went_wrong() -> None:
    driver = _Driver(_ok(REPORT))
    out = await browser.browser_inspect(driver, "s", "http://localhost:3000/")
    assert "HTTP 200" in out and "'Demo App'" in out
    assert '- button "Click me"' in out and "Hello browser" in out
    assert "Console errors:" in out and "boom from page" in out
    assert "Failed requests:" in out and "missing.json" in out


async def test_a_screenshot_is_requested_and_reported() -> None:
    driver = _Driver(_ok({**REPORT, "screenshot": "/workspace/.rocket/screenshots/shot-1.png"}))
    out = await browser.browser_inspect(driver, "s", "http://localhost:3000/", screenshot=True)
    assert (
        "--screenshot" in driver.commands[0]
        and "mkdir -p /workspace/.rocket/screenshots" in driver.commands[0]
    )
    assert "Screenshot saved to /workspace/.rocket/screenshots/shot-1.png" in out


@pytest.mark.parametrize(
    "url",
    [
        "localhost:3000",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://x/ $(rm -rf /)",
        "http://x/`id`",
        "",
        "http://x/'; id; '",
    ],
)
async def test_only_plain_http_urls_reach_the_command_line(url: str) -> None:
    driver = _Driver(_ok(REPORT))
    out = await browser.browser_inspect(driver, "s", url)
    assert out.startswith("Error: give a full http")
    assert driver.commands == []


async def test_the_wait_is_bounded() -> None:
    driver = _Driver(_ok(REPORT))
    await browser.browser_inspect(driver, "s", "http://localhost:3000/", wait_ms=10**9)
    assert "--wait 10000" in driver.commands[0]


async def test_an_image_without_a_browser_says_how_to_get_one() -> None:
    driver = _Driver(ExecResult(127, "", "sh: 1: rocket-browser: not found", 1))
    out = await browser.browser_inspect(driver, "s", "http://localhost:3000/")
    assert out.startswith("Error:") and "Dockerfile.sandbox-browser" in out


async def test_a_page_that_cannot_load_is_an_error_not_a_crash() -> None:
    driver = _Driver(ExecResult(1, json.dumps({"error": "net::ERR_CONNECTION_REFUSED"}), "", 1))
    out = await browser.browser_inspect(driver, "s", "http://localhost:4999/")
    assert out.startswith("Error: could not load http://localhost:4999/")
    assert "CONNECTION_REFUSED" in out


async def test_garbage_output_is_reported_not_raised() -> None:
    driver = _Driver(ExecResult(0, "", "Segmentation fault", 1))
    assert (await browser.browser_inspect(driver, "s", "http://localhost:3000/")).startswith(
        "Error:"
    )
