"""``browser_inspect``: look at a page the way a person would.

Runs ``rocket-browser`` (headless Chromium, baked into the browser variant of the devbox
image) *inside the sandbox*, so the browser reaches exactly what the sandbox can reach and the
platform's own network is never involved.
"""

from __future__ import annotations

import json
import re
import shlex
import time

from agent_core.tools.clamp import clamp_output
from specifications.interfaces.sandbox import SandboxDriverProtocol

SCREENSHOT_DIR = "/workspace/.rocket/screenshots"
_URL = re.compile(r"^https?://[^\s'\"`$\\]+$")
BROWSER_TIMEOUT_SECONDS = 90
NO_BROWSER_HINT = (
    "This sandbox image has no browser. Add the browser variant of the devbox image "
    "(deploy/docker/Dockerfile.sandbox-browser) to the sandbox catalogue with 'Browser tools' "
    "ticked (Settings > Organization > Sandboxes), then start the session in it."
)


async def browser_inspect(
    driver: SandboxDriverProtocol,
    session_id: str,
    url: str,
    screenshot: bool = False,
    wait_ms: int = 500,
) -> str:
    target = url.strip()
    if not _URL.match(target):
        return "Error: give a full http:// or https:// URL, for example http://localhost:3000/."
    wait = max(0, min(int(wait_ms), 10_000))
    command = f"rocket-browser {shlex.quote(target)} --wait {wait}"
    shot_path = ""
    if screenshot:
        shot_path = f"{SCREENSHOT_DIR}/shot-{int(time.time())}.png"
        command = f"mkdir -p {SCREENSHOT_DIR} && {command} --screenshot {shlex.quote(shot_path)}"
    result = await driver.exec_command(session_id, command, timeout_seconds=BROWSER_TIMEOUT_SECONDS)
    if result.exit_code == 127 or "rocket-browser: not found" in result.stderr:
        return f"Error: {NO_BROWSER_HINT}"
    try:
        report = json.loads((result.stdout or "").strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return (
            f"Error: the browser produced no report. {clamp_output(result.stderr or result.stdout)}"
        )
    if "error" in report:
        return f"Error: could not load {target}: {report['error']}"

    lines = [
        f"Loaded {report.get('url')} - HTTP {report.get('status')} - title: {report.get('title')!r}",
        "",
        "Page structure:",
        report.get("accessibility") or "(empty)",
        "",
        "Visible text:",
        report.get("text") or "(none)",
    ]
    if report.get("console_errors"):
        lines += ["", "Console errors:", *[f"- {e}" for e in report["console_errors"]]]
    if report.get("failed_requests"):
        lines += ["", "Failed requests:", *[f"- {r}" for r in report["failed_requests"]]]
    if report.get("screenshot"):
        lines += ["", f"Screenshot saved to {report['screenshot']} (visible in the Files tab)."]
    return clamp_output("\n".join(lines))
