"""Tier 2 sandbox execution tools delegating to SandboxDriverProtocol."""

from agent_core.editor import create_file_diff, replace_block
from agent_core.tools.clamp import clamp_output
from specifications.interfaces.agent import FileDiff
from specifications.interfaces.sandbox import SandboxDriverProtocol


async def bash_exec(
    driver: SandboxDriverProtocol,
    session_id: str,
    command: str,
    timeout_seconds: int = 120,
) -> str:
    """Execute a shell command inside the active sandbox environment."""
    result = await driver.exec_command(
        session_id=session_id,
        command=command,
        timeout_seconds=timeout_seconds,
    )

    if result.exit_code == 0:
        return clamp_output(result.stdout or "(Command completed with no output)")

    output_lines: list[str] = [f"Command failed with exit code {result.exit_code}"]
    if result.stdout:
        output_lines.append(f"STDOUT:\n{clamp_output(result.stdout.strip())}")
    if result.stderr:
        output_lines.append(f"STDERR:\n{clamp_output(result.stderr.strip())}")
    return "\n\n".join(output_lines)


async def file_read(
    driver: SandboxDriverProtocol,
    session_id: str,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """Read file content with optional 1-indexed start and end line ranges."""
    try:
        content = await driver.read_file(
            session_id=session_id,
            path=path,
            start_line=start_line,
            end_line=end_line,
        )
        return clamp_output(content)
    except FileNotFoundError:
        return f"Error: File not found at path '{path}'"
    except Exception as err:
        return f"Error reading file '{path}': {err}"


async def file_write(
    driver: SandboxDriverProtocol,
    session_id: str,
    path: str,
    content: str,
    overwrite: bool = True,
) -> str:
    """Write or overwrite file contents in the sandbox workspace."""
    try:
        await driver.write_file(
            session_id=session_id,
            path=path,
            content=content,
            overwrite=overwrite,
        )
        return f"Successfully wrote {len(content)} characters to '{path}'"
    except FileExistsError:
        return f"Error: File already exists at path '{path}' and overwrite=False"
    except Exception as err:
        return f"Error writing file '{path}': {err}"


async def apply_patch(
    driver: SandboxDriverProtocol,
    session_id: str,
    path: str,
    patch_content: str,
) -> str:
    """Apply a unified diff patch to a file in the workspace."""
    success = await driver.apply_patch(
        session_id=session_id,
        path=path,
        patch_content=patch_content,
    )
    if success:
        return f"Patch successfully applied to '{path}'"
    return f"Failed to apply patch to '{path}': diff hunks did not match target file"


async def file_edit(
    driver: SandboxDriverProtocol,
    session_id: str,
    path: str,
    target_block: str,
    replacement_block: str,
) -> tuple[str, FileDiff | None]:
    """Perform resilient block replacement on a file in the workspace."""
    try:
        original = await driver.read_file(session_id=session_id, path=path)
        modified = replace_block(original, target_block, replacement_block)
        await driver.write_file(session_id=session_id, path=path, content=modified, overwrite=True)
        diff = create_file_diff(path, original, modified)
        return f"Successfully edited '{path}' (+{diff.additions}/-{diff.deletions})", diff
    except FileNotFoundError:
        return f"Error: File not found at path '{path}'", None
    except ValueError as err:
        return f"Error: {err}", None
    except Exception as err:
        return f"Error editing file '{path}': {err}", None
