"""Unit tests for AST verification and execution of custom skills."""

import pytest
from api.services.skills import test_skill_execution as run_skill_execution
from api.services.skills import validate_python_syntax


def test_validate_python_syntax_detects_errors() -> None:
    bad_code = "def invalid_syntax( broken:"
    is_valid, err = validate_python_syntax(bad_code)
    assert is_valid is False
    assert "SyntaxError" in (err or "")


def test_validate_python_syntax_passes_safe_code() -> None:
    safe_code = "def safe_math(a: int, b: int) -> int:\n    return a * b"
    is_valid, err = validate_python_syntax(safe_code)
    assert is_valid is True
    assert err is None


@pytest.mark.asyncio
async def test_skill_without_a_sandbox_fails_closed_and_never_runs_in_process() -> None:
    code = "import builtins\nbuiltins.SKILL_RAN = True\n\ndef f():\n    return 1"
    success, output, _duration, exit_code = await run_skill_execution(
        driver=None, session_id=None, function_name="f", code=code, test_arguments={}
    )
    import builtins

    assert not success and exit_code == 1
    assert "only inside a sandbox" in output
    assert not hasattr(builtins, "SKILL_RAN"), "skill code executed in the API process"


@pytest.mark.asyncio
async def test_skill_runs_in_the_sandbox_with_code_and_args_passed_as_files() -> None:
    from sandbox_driver.fake_driver import FakeSandboxDriver

    from specifications.interfaces.sandbox import ResourceLimits, WorkspaceSpec

    driver = FakeSandboxDriver()
    await driver.ensure_workspace(WorkspaceSpec("s1", "o", "u", "img", resources=ResourceLimits()))
    await driver.start_sandbox("s1")
    # Quotes, command substitution and triple quotes in code/arguments must stay inert.
    code = 'def f(x):\n    return "$(touch /tmp/pwned)" + x'
    await run_skill_execution(
        driver=driver,
        session_id="s1",
        function_name="f",
        code=code,
        test_arguments={"x": "'''; import os"},
    )
    assert len(driver.exec_calls) == 1
    command = driver.exec_calls[0][1]
    assert command.startswith("python3 /workspace/.rocket/skills/_runner.py")
    assert "touch /tmp/pwned" not in command and "import os" not in command
    files = driver.file_snapshot("s1")
    skill_files = {k: v for k, v in files.items() if ".rocket/skills/" in k}
    written_code = next(
        v for k, v in skill_files.items() if k.endswith(".py") and "_runner" not in k
    )
    assert written_code == code
    assert any('"x"' in v for k, v in skill_files.items() if k.endswith(".json"))
