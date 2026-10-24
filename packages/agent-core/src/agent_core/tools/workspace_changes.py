"""Which files a shell command changed in a repository, as diffs for the cockpit.

``file_write``, ``file_edit`` and patches report their own diffs; a command run with
``bash_exec`` (``sed -i``, a code generator, a formatter) changes files silently. Around such a
command the repository's changed files are fingerprinted (path and blob hash, from
``git status``), and every file whose fingerprint moved gets a diff against ``HEAD`` (or against
nothing for a new file). Outside a repository, nothing is reported.
"""

from __future__ import annotations

import shlex

from specifications.interfaces.agent import FileDiff
from specifications.interfaces.sandbox import SandboxDriverProtocol

MAX_REPORTED_FILES = 20
_ROOT = "@root "
_FILE = "@@rocket-file "
_SNAPSHOT = (
    'top=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0; cd "$top" || exit 0; '
    'echo "@root $top"; '
    'tmp=$(mktemp -d 2>/dev/null || mktemp -d -t "rk"); '
    "git status --porcelain=v1 -uall --no-renames -z 2>/dev/null | tr '\\0' '\\n' | head -n 500 > \"$tmp/raw\"; "
    'awk -v out="$tmp/exist" \'length($0) > 3 { st=substr($0,1,2); f=substr($0,4); if (st ~ /D/) print "gone " f; else print f > out }\' "$tmp/raw"; '
    'if [ -s "$tmp/exist" ]; then '
    'git hash-object --stdin-paths < "$tmp/exist" 2>/dev/null > "$tmp/hashes"; '
    'paste -d " " "$tmp/hashes" "$tmp/exist"; '
    "fi; "
    'rm -rf "$tmp"'
)

Snapshot = tuple[str, dict[str, str]]


async def snapshot(driver: SandboxDriverProtocol, session_id: str, workdir: str) -> Snapshot:
    """(repository root, path -> content fingerprint) of the files that differ from HEAD."""
    result = await driver.exec_command(session_id, _SNAPSHOT, workdir=workdir, timeout_seconds=30)
    root, files = "", {}
    for line in (result.stdout or "").splitlines():
        if line.startswith(_ROOT):
            root = line[len(_ROOT) :].strip()
        elif " " in line:
            fingerprint, _, path = line.partition(" ")
            files[path] = fingerprint
    return root, files


def changed_paths(before: Snapshot, after: Snapshot) -> list[str]:
    if not after[0] or before[0] != after[0]:
        return []
    paths = set(before[1]) | set(after[1])
    return sorted(p for p in paths if before[1].get(p) != after[1].get(p))[:MAX_REPORTED_FILES]


async def diffs_for(
    driver: SandboxDriverProtocol, session_id: str, root: str, paths: list[str]
) -> list[FileDiff]:
    """Each path's diff against HEAD (a new file against nothing), in one command."""
    if not paths:
        return []
    script = "; ".join(
        f"echo {shlex.quote(_FILE + path)}; "
        f"if git ls-files --error-unmatch -- {shlex.quote(path)} >/dev/null 2>&1; "
        f"then git diff --no-color HEAD -- {shlex.quote(path)}; "
        f"else git diff --no-color --no-index /dev/null {shlex.quote(path)}; fi"
        for path in paths
    )
    result = await driver.exec_command(
        session_id, f"{script}; true", workdir=root, timeout_seconds=60
    )
    return _parse(result.stdout or "", _relative_to_workspace(root))


def _relative_to_workspace(root: str) -> str:
    prefix = root.removeprefix("/workspace").strip("/")
    return f"{prefix}/" if prefix else ""


def _parse(output: str, prefix: str) -> list[FileDiff]:
    diffs: list[FileDiff] = []
    for block in output.split(_FILE)[1:]:
        path, _, body = block.partition("\n")
        lines = body.splitlines()
        diffs.append(
            FileDiff(
                path=f"{prefix}{path.strip()}",
                diff_content=body,
                additions=sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++")),
                deletions=sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---")),
                is_new_file="new file mode" in body or "--- /dev/null" in body,
            )
        )
    return diffs
