"""Whether this process is on its way out, for work that must not look like it finished.

A turn cancelled because the pod is being replaced is not a turn the user stopped: if it was
waiting on a question, the question and the interrupted turn are kept so the person can still
answer after the restart.
"""

from __future__ import annotations

_shutting_down = False


def begin_shutdown() -> None:
    global _shutting_down
    _shutting_down = True


def is_shutting_down() -> bool:
    return _shutting_down


def reset_for_tests() -> None:
    global _shutting_down
    _shutting_down = False
