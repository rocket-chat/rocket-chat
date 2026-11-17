"""The session's files as a workspace, for an agent that runs without a sandbox.

``file_read``, ``file_write``, ``file_edit`` and ``list_dir`` keep their behaviour (windows,
block replacement, diffs) through ``ScratchFiles``, which reads and writes the session's
durable files instead of a sandbox. Notes, plans and caches survive the turn and the session's
later turns; nothing here can run a command.
"""

from __future__ import annotations

from agent_core.context import current_execution_ctx
from specifications.interfaces.session import SessionFilesProtocol

# A listing names at most this many files; a longer one says how many were left out.
MAX_LISTED_FILES = 200


class ScratchFiles:
    """``tier2.FileAccess`` over a turn's session files."""

    def __init__(self, files: SessionFilesProtocol) -> None:
        self._files = files

    async def read_file(
        self,
        session_id: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        kept = await self._files.get(path)
        if kept is None:
            raise FileNotFoundError(path)
        if start_line is None and end_line is None:
            return kept.content
        lines = kept.content.splitlines()
        first = max(1, start_line or 1)
        last = min(len(lines), end_line or len(lines))
        return "\n".join(lines[first - 1 : last])

    async def write_file(
        self, session_id: str, path: str, content: str, overwrite: bool = True
    ) -> None:
        if not overwrite and await self._files.get(path) is not None:
            raise FileExistsError(path)
        await self._files.put(path, content, kind="scratch")

    async def list_dir(self, path: str = ".") -> str:
        """The files under ``path``, with their size; folders are path prefixes."""
        prefix = _folder(path)
        names = [
            (f.path, f.size)
            for f in await self._files.list_files()
            if not prefix or f.path.startswith(prefix)
        ]
        if not names:
            return f"No session files under '{path}'."
        shown = sorted(names)[:MAX_LISTED_FILES]
        lines = [f"{size:>10,}  {name}" for name, size in shown]
        if len(names) > len(shown):
            lines.append(f"… {len(names) - len(shown)} more files not listed")
        return "Session files (no sandbox):\n" + "\n".join(lines)


def _folder(path: str) -> str:
    cleaned = (path or ".").strip().removeprefix("/workspace").strip("/")
    return "" if cleaned in ("", ".") else cleaned + "/"


def scratch_files() -> ScratchFiles | None:
    """The turn's session files as a workspace when its agent runs without a sandbox."""
    ctx = current_execution_ctx.get()
    if ctx is None or ctx.policy.sandbox_enabled or ctx.callbacks.session_files is None:
        return None
    return ScratchFiles(ctx.callbacks.session_files)
