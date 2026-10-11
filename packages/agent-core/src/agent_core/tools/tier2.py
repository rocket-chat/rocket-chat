"""Tier 2 sandbox execution tools delegating to SandboxDriverProtocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from agent_core.editor import create_file_diff, replace_block
from agent_core.tools.clamp import clamp_output
from specifications.interfaces.agent import FileDiff
from specifications.interfaces.sandbox import SandboxDriverProtocol


def _sh_quote(value: str) -> str:
    """POSIX-quote a string for safe interpolation inside a single-quoted shell literal."""
    return "'" + value.replace("'", "'\\''") + "'"


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


@dataclass(frozen=True)
class FilePatch:
    """One file's section of a possibly multi-file unified diff."""

    path: str
    content: str
    is_new_file: bool = False


def _normalize_diff_path(value: str) -> str:
    cleaned = value.strip().strip('"')
    if cleaned == "/dev/null":
        return ""
    for prefix in ("a/", "b/"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    return cleaned[2:] if cleaned.startswith("./") else cleaned


def _path_from_git_header(line: str) -> str:
    _, sep, rest = line.partition(" b/")
    if sep:
        return _normalize_diff_path("b/" + rest)
    return _normalize_diff_path(line.rsplit(" ", 1)[-1])


def _is_new_file_block(lines: list[str]) -> bool:
    return any(
        line.startswith("new file mode")
        or (line.startswith("--- ") and line[4:].strip().strip('"') == "/dev/null")
        for line in lines
    )


def _split_git_patches(diff_content: str) -> list[FilePatch]:
    blocks: list[list[str]] = []
    for line in diff_content.splitlines():
        if line.startswith("diff --git "):
            blocks.append([])
        if blocks:
            blocks[-1].append(line)
    patches: list[FilePatch] = []
    for block in blocks:
        path = _path_from_git_header(block[0])
        if path:
            patches.append(
                FilePatch(
                    path=path,
                    content="\n".join(block) + "\n",
                    is_new_file=_is_new_file_block(block),
                )
            )
    return patches


def _split_marker_patches(diff_content: str) -> list[FilePatch]:
    lines = diff_content.splitlines()
    blocks: list[list[str]] = []
    for index, line in enumerate(lines):
        # A file section starts only where `--- a/x` is followed by `+++ b/x`; a
        # removed line whose content begins with `-- ` also reads as `--- `, so the
        # look-ahead is what keeps the two apart.
        is_start = (
            line.startswith("--- ")
            and index + 1 < len(lines)
            and lines[index + 1].startswith("+++ ")
        )
        if is_start:
            blocks.append([])
        if blocks:
            blocks[-1].append(line)

    patches: list[FilePatch] = []
    for block in blocks:
        path = _normalize_diff_path(block[0][4:])
        if not path and len(block) > 1:
            path = _normalize_diff_path(block[1][4:])
        if path:
            patches.append(
                FilePatch(
                    path=path,
                    content="\n".join(block) + "\n",
                    is_new_file=_is_new_file_block(block),
                )
            )
    return patches


def split_file_patches(diff_content: str) -> list[FilePatch]:
    """Split a unified diff into per-file sections, git-style or plain markers."""
    if "diff --git " in diff_content:
        return _split_git_patches(diff_content)
    return _split_marker_patches(diff_content)


def extract_file_patch(diff_content: str, path: str) -> FilePatch | None:
    """Return the single file section of ``path`` from a multi-file diff, if present."""
    target = _normalize_diff_path(path)
    if not target:
        return None
    for patch in split_file_patches(diff_content):
        if patch.path == target:
            return patch
    return None


def _added_lines(section: str) -> str:
    """Reconstruct new-file content from a creation diff's added lines."""
    added: list[str] = []
    in_hunk = False
    for line in section.splitlines():
        if line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("+"):
            added.append(line[1:])
    return "\n".join(added) + ("\n" if added else "")


async def apply_diff_file(
    driver: SandboxDriverProtocol,
    session_id: str,
    diff_content: str,
    path: str,
) -> tuple[str, FileDiff | None]:
    """Apply only one file's hunks from a multi-file diff to the workspace."""
    patch = extract_file_patch(diff_content, path)
    if patch is None:
        return f"Error: no diff section found for '{path}' in the provided patch", None

    original = ""
    if not patch.is_new_file:
        try:
            original = await driver.read_file(session_id=session_id, path=path)
        except Exception as err:
            return f"Error: cannot read '{path}' before applying the patch: {err}", None

    try:
        if patch.is_new_file:
            await driver.write_file(
                session_id=session_id,
                path=path,
                content=_added_lines(patch.content),
                overwrite=False,
            )
        else:
            applied = await driver.apply_patch(
                session_id=session_id, path=path, patch_content=patch.content
            )
            if not applied:
                return (
                    f"Failed to apply patch for '{path}': hunks did not match the target file",
                    None,
                )
        modified = await driver.read_file(session_id=session_id, path=path)
    except Exception as err:
        return f"Error applying patch for '{path}': {err}", None

    diff = create_file_diff(path, original, modified, is_new_file=patch.is_new_file)
    action = "Created" if patch.is_new_file else "Applied patch to"
    return f"{action} '{path}' (+{diff.additions}/-{diff.deletions})", diff


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


async def list_dir(
    driver: SandboxDriverProtocol,
    session_id: str,
    path: str = ".",
) -> str:
    """List directory contents with a long-format, human-readable listing."""
    result = await driver.exec_command(
        session_id=session_id,
        command=f"ls -la -- {_sh_quote(path)} 2>&1 || ls -la {_sh_quote(path)}",
        timeout_seconds=30,
    )
    output = (result.stdout or "").strip() or (result.stderr or "").strip()
    if not output:
        return f"(empty directory: {path})"
    return clamp_output(output)


async def grep_search(
    driver: SandboxDriverProtocol,
    session_id: str,
    pattern: str,
    path: str = ".",
    file_glob: str | None = None,
    case_sensitive: bool = True,
    max_results: int = 200,
) -> str:
    """Search file contents recursively for a regex pattern (ripgrep with grep fallback)."""
    quoted_pattern = _sh_quote(pattern)
    quoted_path = _sh_quote(path)
    limit = max(1, min(max_results, 1000))

    rg_flags = ["--line-number", "--no-heading", "--color=never"]
    grep_flags = ["-rn", "-I", "--binary-files=without-match"]
    if not case_sensitive:
        rg_flags.append("--ignore-case")
        grep_flags.append("-i")
    if file_glob:
        rg_flags += ["-g", _sh_quote(file_glob)]
        grep_flags.append(f"--include={_sh_quote(file_glob)}")

    rg_cmd = " ".join(["rg", *rg_flags, "-e", quoted_pattern, quoted_path])
    grep_cmd = " ".join(["grep", *grep_flags, "-e", quoted_pattern, quoted_path])
    command = (
        f"if command -v rg >/dev/null 2>&1; then {rg_cmd} 2>&1; "
        f"else {grep_cmd} 2>&1; fi | head -n {limit}"
    )
    result = await driver.exec_command(session_id=session_id, command=command, timeout_seconds=60)
    output = (result.stdout or "").strip()
    if not output:
        return f"No matches found for pattern {pattern!r} in {path!r}."
    return clamp_output(output)


async def glob_files(
    driver: SandboxDriverProtocol,
    session_id: str,
    pattern: str,
    path: str = ".",
) -> str:
    """Find files matching a glob pattern (supports ** for recursive matching)."""
    regex = _glob_to_regex(pattern)
    command = (
        f"find {_sh_quote(path)} -type f 2>/dev/null | sed 's|^\\./||' | "
        f"grep -E {_sh_quote(regex)} | sort | head -n 500"
    )
    result = await driver.exec_command(session_id=session_id, command=command, timeout_seconds=60)
    output = (result.stdout or "").strip()
    if not output:
        return f"No files matched pattern {pattern!r} under {path!r}."
    return clamp_output(output)


def _glob_to_regex(pattern: str) -> str:
    """Translate a shell glob (with ** support) into an extended POSIX regex."""
    import re

    out: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
                if i < len(pattern) and pattern[i] == "/":
                    i += 1
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    return "(^|/)" + "".join(out) + "$"


async def git_status(
    driver: SandboxDriverProtocol,
    session_id: str,
) -> str:
    """Return the structured porcelain git status of the workspace."""
    command = (
        "git rev-parse --is-inside-work-tree >/dev/null 2>&1 || "
        "{ echo 'Error: workspace is not a git repository'; exit 0; }; "
        "git status --porcelain=v1 --branch"
    )
    result = await driver.exec_command(session_id=session_id, command=command, timeout_seconds=30)
    output = (result.stdout or "").strip()
    if not output or output.startswith("Error:"):
        return output or "(clean working tree)"
    return clamp_output(output)


async def git_diff(
    driver: SandboxDriverProtocol,
    session_id: str,
    path: str | None = None,
    staged: bool = False,
    stat_only: bool = False,
) -> str:
    """Show the current unified git diff (optionally staged, scoped, or stat-only)."""
    parts = ["git diff --stat"] if stat_only else ["git diff"]
    if staged:
        parts.append("--cached")
    if path:
        parts += ["--", _sh_quote(path)]
    result = await driver.exec_command(
        session_id=session_id, command=" ".join(parts) + " 2>&1", timeout_seconds=30
    )
    output = (result.stdout or "").strip()
    return clamp_output(output) if output else "(no differences)"


class GitLogEntry(TypedDict):
    """A parsed commit: identity, date, subject and the files it touched."""

    sha: str
    author: str
    date: str
    subject: str
    files: list[str]


# `%x1e`/`%x1f` are git's escapes for the ASCII record/unit separators. Using git
# escapes keeps the command line ASCII-safe while still giving an unambiguous,
# newline-tolerant envelope to parse.
_GIT_LOG_RECORD_SEP = "\x1e"
_GIT_LOG_FIELD_SEP = "\x1f"
_GIT_LOG_PRETTY_FORMAT = "%x1e%H%x1f%an%x1f%ad%x1f%s"


def parse_git_log(output: str) -> list[GitLogEntry]:
    """Parse ``git log --name-only`` output into structured entries."""
    entries: list[GitLogEntry] = []
    current: GitLogEntry | None = None
    # Split on '\n' deliberately: str.splitlines() also breaks on the ASCII record
    # separator (\x1e) that marks each commit, which would shred every record.
    for line in output.split("\n"):
        if _GIT_LOG_RECORD_SEP in line:
            _, _, payload = line.partition(_GIT_LOG_RECORD_SEP)
            fields = payload.split(_GIT_LOG_FIELD_SEP)
            sha, author, date, subject = [*fields, "", "", "", ""][:4]
            current = GitLogEntry(sha=sha, author=author, date=date, subject=subject, files=[])
            entries.append(current)
        elif current is not None and line.strip():
            current["files"].append(line.strip())
    return entries


def format_git_log(entries: list[GitLogEntry]) -> str:
    """Render parsed commits as a compact, readable block."""
    lines: list[str] = []
    for entry in entries:
        files = ", ".join(entry["files"]) if entry["files"] else "(no files changed)"
        lines.append(f"{entry['sha'][:8]} {entry['date']} {entry['author']}: {entry['subject']}")
        lines.append(f"  files: {files}")
    return "\n".join(lines)


async def git_log(
    driver: SandboxDriverProtocol,
    session_id: str,
    max_count: int = 20,
    path: str | None = None,
    author: str | None = None,
) -> str:
    """Return structured commit history (author, date, subject, changed files)."""
    count = max(1, min(max_count, 100))
    parts = [
        "git log",
        f"-n {count}",
        "--date=short",
        "--name-only",
        f"--pretty=format:{_sh_quote(_GIT_LOG_PRETTY_FORMAT)}",
    ]
    if author:
        parts.append(f"--author={_sh_quote(author)}")
    if path:
        parts += ["--", _sh_quote(path)]
    command = (
        "git rev-parse --is-inside-work-tree >/dev/null 2>&1 || "
        "{ echo 'Error: workspace is not a git repository'; exit 0; }; " + " ".join(parts) + " 2>&1"
    )
    result = await driver.exec_command(session_id=session_id, command=command, timeout_seconds=30)
    raw = result.stdout or ""
    # str.strip() treats \x1e as whitespace and would eat the first record separator,
    # so the emptiness/error guards run on a stripped copy while parsing sees the raw text.
    stripped = raw.strip()
    if not stripped or stripped.startswith("Error:"):
        return stripped or "(no commits)"
    entries = parse_git_log(raw)
    if not entries:
        return "(no commits)"
    return clamp_output(format_git_log(entries))


async def run_tests(
    driver: SandboxDriverProtocol,
    session_id: str,
    command: str | None = None,
    timeout_seconds: int = 600,
) -> str:
    """Detect and run the project test suite, returning a parsed pass/fail summary."""
    if command:
        test_cmd = command
        framework = "custom"
    else:
        test_cmd, framework = _detect_test_command()

    result = await driver.exec_command(
        session_id=session_id,
        command=test_cmd,
        timeout_seconds=timeout_seconds,
    )
    raw = "\n".join(
        part for part in ((result.stdout or "").strip(), (result.stderr or "").strip()) if part
    )
    detected = _extract_framework(raw) or framework
    verdict = "PASSED" if result.exit_code == 0 else f"FAILED (exit {result.exit_code})"
    header = f"[{detected}] Tests {verdict}"
    summary = _summarize_test_output(raw)
    if summary:
        header += f" — {summary}"
    body = clamp_output(raw) if raw else "(no test output)"
    return f"{header}\n\n{body}"


def _detect_test_command() -> tuple[str, str]:
    """Detect the test runner for the workspace, returning (command, framework_label)."""
    return (
        "if [ -f package.json ] && command -v npm >/dev/null 2>&1; then "
        "echo '__ROCKET_FRAMEWORK__:npm'; npm test --silent; "
        "elif [ -f Cargo.toml ] && command -v cargo >/dev/null 2>&1; then "
        "echo '__ROCKET_FRAMEWORK__:cargo'; cargo test; "
        "elif [ -f go.mod ] && command -v go >/dev/null 2>&1; then "
        "echo '__ROCKET_FRAMEWORK__:go'; go test ./...; "
        "elif [ -f pyproject.toml ] || [ -f pytest.ini ] || [ -f tox.ini ] || [ -d tests ]; then "
        "echo '__ROCKET_FRAMEWORK__:pytest'; "
        "if command -v pytest >/dev/null 2>&1; then pytest -q; else python3 -m pytest -q; fi; "
        "else echo '__ROCKET_FRAMEWORK__:none'; "
        "echo 'No supported test framework detected (npm, cargo, go, pytest).'; exit 2; fi",
        "auto",
    )


def _extract_framework(output: str) -> str:
    """Read the framework marker emitted by the auto-detection script, if present."""
    import re

    match = re.search(r"__ROCKET_FRAMEWORK__:(\w+)", output)
    return match.group(1) if match else ""


def _summarize_test_output(output: str) -> str:
    """Extract a concise pass/fail summary line from common test-runner output."""
    import re

    if not output:
        return ""
    patterns = [
        r"\d+ passed[^\n]*",
        r"\d+ failed[^\n]*",
        r"test result: [^\n]*",
        r"Tests:\s+[^\n]*",
        r"\d+ passing[^\n]*",
        r"\bok\s+\S+",
        r"FAILED[^\n]*",
    ]
    for pattern in patterns:
        found = re.search(pattern, output)
        if found:
            return found.group(0).strip()
    return ""
