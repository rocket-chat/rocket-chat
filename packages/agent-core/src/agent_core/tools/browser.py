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


# ``browser_session``: a browser that keeps its state between calls (``rocket-browser-session``
# in the browser sandbox image). Each call sends a few actions; screenshots are named here, so
# the agent never chooses a path the browser writes to.
MAX_SESSION_ACTIONS = 20
SESSION_ACTIONS = (
    "start",
    "navigate",
    "click",
    "type",
    "press",
    "wait",
    "snapshot",
    "screenshot",
    "close",
)


def _session_action(action: dict[str, object], index: int, stamp: int) -> dict[str, object] | str:
    kind = str(action.get("do") or "")
    if kind not in SESSION_ACTIONS:
        return f"action {index + 1}: 'do' is one of {', '.join(SESSION_ACTIONS)}"
    if kind == "navigate" and not _URL.match(str(action.get("url") or "")):
        return f"action {index + 1}: navigate needs a full http(s) url"
    if kind in ("click", "type") and not str(action.get("selector") or "").strip():
        return f"action {index + 1}: {kind} needs a selector"
    if kind == "screenshot":
        return {"do": kind, "path": f"{SCREENSHOT_DIR}/session-{stamp}-{index + 1}.png"}
    allowed = {"do", "url", "selector", "text", "key", "ms", "record"}
    return {key: value for key, value in action.items() if key in allowed and value is not None}


async def browser_session(
    driver: SandboxDriverProtocol, session_id: str, actions: list[dict[str, object]]
) -> str:
    """Run ``actions`` in the session's browser; what each did, and the page's errors."""
    if not actions:
        return "Error: give at least one action."
    if len(actions) > MAX_SESSION_ACTIONS:
        return f"Error: at most {MAX_SESSION_ACTIONS} actions per call."
    stamp = int(time.time())
    prepared: list[dict[str, object]] = []
    for index, action in enumerate(actions):
        checked = _session_action(action, index, stamp)
        if isinstance(checked, str):
            return f"Error: {checked}."
        prepared.append(checked)
    command = (
        f"mkdir -p {SCREENSHOT_DIR} && rocket-browser-session act "
        f"{shlex.quote(json.dumps(prepared))}"
    )
    result = await driver.exec_command(session_id, command, timeout_seconds=BROWSER_TIMEOUT_SECONDS)
    if result.exit_code == 127 or "rocket-browser-session: not found" in result.stderr:
        return f"Error: {NO_BROWSER_HINT}"
    try:
        report = json.loads((result.stdout or "").strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return f"Error: the browser gave no report. {clamp_output(result.stderr or result.stdout)}"
    return clamp_output(_describe_session(report))


def _describe_session(report: dict[str, object]) -> str:
    lines: list[str] = []
    results = report.get("results")
    for done in results if isinstance(results, list) else []:
        if not isinstance(done, dict):
            continue
        kind = done.get("do")
        if kind == "snapshot":
            lines += [
                f"Page {done.get('url')} - title: {done.get('title')!r}",
                "Page structure:",
                str(done.get("accessibility") or "(empty)"),
                "Visible text:",
                str(done.get("text") or "(none)"),
            ]
        elif kind == "screenshot":
            lines.append(
                f"Screenshot saved to {done.get('screenshot')} (look at it with view_image)."
            )
        elif kind == "close" and done.get("video"):
            lines.append(f"Recording saved to {done.get('video')}.")
        else:
            detail = {k: v for k, v in done.items() if k != "do"}
            lines.append(f"{kind}: {json.dumps(detail)}")
    if report.get("error"):
        lines.append(f"Stopped on an error: {report['error']}")
    lines.append(f"Current page: {report.get('url') or '(none)'}")
    errors = report.get("console_errors")
    if isinstance(errors, list) and errors:
        lines += ["Console errors:", *[f"- {e}" for e in errors]]
    return "\n".join(lines)
