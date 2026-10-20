"""Progress tracking, loop detection, and stall prevention (Item A5).

Detects:
1. Repeated command failures (exit codes >= 3, identical command failing).
2. File edit/revert oscillations (A -> B -> A).

Triggers:
- Level 1: System nudge injected into the next prompt note.
- Level 2: Decision required interactive question for the user.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass

NUDGE_FAIL_THRESHOLD = 3
DECISION_FAIL_THRESHOLD = 5


@dataclass
class LoopDetectionResult:
    level: int  # 1 = nudge, 2 = decision_required
    reason: str
    suggested_nudge: str
    question_text: str | None = None
    options: list[str] | None = None


class ProgressTracker:
    """Tracks tool operations within a turn to catch unproductive loops."""

    def __init__(self) -> None:
        self._command_history: list[tuple[str, int]] = []
        self._file_hashes: dict[str, list[str]] = defaultdict(list)
        self._oscillation_counts: dict[str, int] = defaultdict(int)

    def record_command(self, command: str, exit_code: int) -> LoopDetectionResult | None:
        """Record command execution and evaluate failure loops."""
        clean_cmd = " ".join(command.strip().split())
        self._command_history.append((clean_cmd, exit_code))

        if exit_code == 0:
            return None

        # Count consecutive identical failing commands
        consecutive_same_fails = 0
        for cmd, code in reversed(self._command_history):
            if cmd == clean_cmd and code != 0:
                consecutive_same_fails += 1
            else:
                break

        if consecutive_same_fails >= DECISION_FAIL_THRESHOLD:
            return LoopDetectionResult(
                level=2,
                reason=f"Command `{clean_cmd}` failed {consecutive_same_fails} consecutive times.",
                suggested_nudge="",
                question_text=(
                    f"Command `{clean_cmd}` has failed {consecutive_same_fails} times with exit code {exit_code}. "
                    "How would you like to proceed?"
                ),
                options=[
                    "Try a different approach / skip this step",
                    "Continue trying",
                    "Abort current turn",
                ],
            )

        if consecutive_same_fails >= NUDGE_FAIL_THRESHOLD:
            return LoopDetectionResult(
                level=1,
                reason=f"Command `{clean_cmd}` failed {consecutive_same_fails} times.",
                suggested_nudge=(
                    f"[Loop Warning: Command `{clean_cmd}` has failed {consecutive_same_fails} times with exit code {exit_code}. "
                    "Do not repeat the identical command without changing parameters, dependencies, or approach. "
                    "Diagnose the root cause or choose another solution.]"
                ),
            )

        return None

    def record_file_edit(self, path: str, content: str) -> LoopDetectionResult | None:
        """Record file edit and detect edit-revert oscillations."""
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        history = self._file_hashes[path]
        history.append(content_hash)

        if len(history) >= 3 and history[-1] == history[-3] and history[-1] != history[-2]:
            self._oscillation_counts[path] += 1
            count = self._oscillation_counts[path]

            if count >= 2:
                return LoopDetectionResult(
                    level=2,
                    reason=f"Edit/revert oscillation detected on `{path}` ({count} times).",
                    suggested_nudge="",
                    question_text=(
                        f"Repeated edit/revert oscillation detected on `{path}`. "
                        "The agent is toggling between two states. How should we proceed?"
                    ),
                    options=[
                        "Keep current file state and move to next task",
                        "Revert file to original state and reconsider approach",
                        "Abort current turn",
                    ],
                )

            return LoopDetectionResult(
                level=1,
                reason=f"Edit/revert oscillation detected on `{path}`.",
                suggested_nudge=(
                    f"[Loop Warning: Edit/revert oscillation detected on `{path}`. "
                    "The file was edited and immediately reverted to a previous state. "
                    "Stop oscillating between the same changes; inspect the tests or assumptions carefully.]"
                ),
            )

        return None
