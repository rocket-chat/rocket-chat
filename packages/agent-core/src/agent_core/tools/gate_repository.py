"""Which GitHub repository a gated call acts in, for approvals remembered per repository."""

from __future__ import annotations

import logging
import shlex

from agent_core.tools.repository import InvalidRepositoryError, parse_repository
from specifications.interfaces.sandbox import SandboxDriverProtocol

logger = logging.getLogger(__name__)


async def repository_of_folder(driver: SandboxDriverProtocol, session_id: str, folder: str) -> str:
    """``owner/name`` of the clone holding ``folder`` (from its ``origin``), or ``""``.

    The remote URL may carry a credential, so only the parsed slug ever leaves this function.
    """
    try:
        result = await driver.exec_command(
            session_id,
            f"git -C {shlex.quote(folder)} remote get-url origin",
            timeout_seconds=15,
        )
    except Exception as err:
        logger.debug("No repository for %s in %s: %s", folder, session_id, err)
        return ""
    url = (result.stdout or "").strip().splitlines()
    if result.exit_code != 0 or not url:
        return ""
    raw = url[0].strip()
    try:
        return parse_repository(raw).slug.lower()
    except InvalidRepositoryError:
        # An ``https://user:token@github.com/...`` remote is refused by the strict parser; the
        # slug is still the path's last two segments.
        path = raw.split("github.com", 1)[-1].strip(":/").removesuffix(".git")
        parts = [p for p in path.split("/") if p]
        return "/".join(parts[-2:]).lower() if len(parts) >= 2 and "github.com" in raw else ""
