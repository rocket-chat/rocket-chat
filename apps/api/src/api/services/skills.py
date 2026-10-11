"""Python Custom Skill vetting, AST safety parsing, and Sandbox execution service."""

import ast
import json
import logging
import shlex
import time
import uuid
from typing import Any

from specifications.interfaces.sandbox import SandboxDriverProtocol

logger = logging.getLogger(__name__)


def validate_python_syntax(code: str) -> tuple[bool, str | None]:
    """Verify code can be parsed into Python AST without syntax errors."""
    try:
        ast.parse(code)
        return True, None
    except SyntaxError as err:
        return False, f"SyntaxError at line {err.lineno}: {err.msg}"


SKILL_RUNNER = """\
import importlib.util, json, sys, traceback

module_path, function_name, args_path = sys.argv[1:4]
spec = importlib.util.spec_from_file_location("rocket_skill", module_path)
module = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(module)
    function = getattr(module, function_name, None)
    if function is None:
        print(f"Error: Function '{function_name}' not found in code", file=sys.stderr)
        sys.exit(2)
    with open(args_path) as handle:
        arguments = json.load(handle)
    print(json.dumps(function(**arguments), default=str))
except Exception:
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
"""

SKILL_DIR = "/workspace/.rocket/skills"
NO_SANDBOX_MESSAGE = "Skills run only inside a sandbox; none is available for this session."


async def test_skill_execution(
    driver: SandboxDriverProtocol | None,
    session_id: str | None,
    function_name: str,
    code: str,
    test_arguments: dict[str, Any],
) -> tuple[bool, str, float, int]:
    """Run a custom skill inside the session's sandbox.

    Returns ``(success, output, duration_ms, exit_code)``. Skill code is arbitrary user
    Python, so it never runs in the API process: without a sandbox the call fails closed.
    The code and its arguments travel as files, so neither can break out of the runner
    through quoting.
    """
    valid, err_msg = validate_python_syntax(code)
    if not valid:
        return False, f"Validation failed: {err_msg}", 0.0, 1
    if driver is None or not session_id:
        return False, NO_SANDBOX_MESSAGE, 0.0, 1

    run_id = uuid.uuid4().hex
    module_path = f"{SKILL_DIR}/{run_id}.py"
    args_path = f"{SKILL_DIR}/{run_id}.json"
    runner_path = f"{SKILL_DIR}/_runner.py"
    start_time = time.time()
    try:
        await driver.write_file(session_id, module_path, code)
        await driver.write_file(session_id, args_path, json.dumps(test_arguments))
        await driver.write_file(session_id, runner_path, SKILL_RUNNER)
        res = await driver.exec_command(
            session_id=session_id,
            command=(
                f"python3 {shlex.quote(runner_path)} {shlex.quote(module_path)} "
                f"{shlex.quote(function_name)} {shlex.quote(args_path)}; "
                f"status=$?; rm -f {shlex.quote(module_path)} {shlex.quote(args_path)}; "
                "exit $status"
            ),
        )
    except Exception as err:
        logger.warning("Sandbox execution failed for skill %s: %s", function_name, err)
        return False, f"Sandbox execution failed: {err}", 0.0, 1

    duration_ms = (time.time() - start_time) * 1000.0
    out = res.stdout if res.exit_code == 0 else (res.stderr or res.stdout)
    return (res.exit_code == 0, out.strip(), round(duration_ms, 2), res.exit_code)
