"""Progress tracking, loop detection, and stall prevention (Item A5).

Detects:
1. Repeated command failures (exit codes >= 3, identical command failing).
2. File edit/revert oscillations (A -> B -> A).
3. Stalled checklist progress with active steps.

Triggers:
- Level 1: System nudge injected into the next prompt note.
- Level 2: Decision required interactive question for the user.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

NUDGE_FAIL_THRESHOLD = 3
DECISION_FAIL_THRESHOLD = 5
NUDGE_STALL_STEPS = 25
DECISION_STALL_STEPS = 75


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
        self._last_checklist_hash: str | None = None
        self._steps_without_checklist_progress: int = 0

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

    def _check_stalled(self) -> LoopDetectionResult | None:
        stalled = self._steps_without_checklist_progress
        if stalled >= DECISION_STALL_STEPS:
            return LoopDetectionResult(
                level=2,
                reason=f"Checklist progress stalled for {stalled} consecutive steps.",
                suggested_nudge="",
                question_text=(
                    f"Task execution has continued for {stalled} steps without updating any checklist task. "
                    "Would you like to give guidance or continue?"
                ),
                options=[
                    "Continue running",
                    "Update checklist and refocus on next pending item",
                    "Abort current turn",
                ],
            )

        if stalled >= NUDGE_STALL_STEPS:
            return LoopDetectionResult(
                level=1,
                reason=f"Checklist progress stalled for {stalled} steps.",
                suggested_nudge=(
                    f"[Stall Warning: {stalled} steps have completed without updating any checklist item. "
                    "Review your checklist status with `update_checklist` and ensure progress towards the active goal.]"
                ),
            )

        return None

    def record_step(self, has_active_checklist: bool = True) -> LoopDetectionResult | None:
        """Advance turn step counter and detect checklist stagnation."""
        if not has_active_checklist:
            return None
        self._steps_without_checklist_progress += 1
        return self._check_stalled()

    def record_checklist_state(
        self, checklist_tasks: list[dict[str, Any]] | None
    ) -> LoopDetectionResult | None:
        """Track checklist progress and notice stagnation."""
        if not checklist_tasks:
            return None

        # Compute hash of tasks and statuses
        state_repr = "-".join(f"{t.get('id', '')}:{t.get('status', '')}" for t in checklist_tasks)
        curr_hash = hashlib.sha256(state_repr.encode("utf-8")).hexdigest()[:16]

        has_unfinished = any(
            t.get("status") not in ("completed", "cancelled") for t in checklist_tasks
        )
        if not has_unfinished:
            self._steps_without_checklist_progress = 0
            self._last_checklist_hash = curr_hash
            return None

        if self._last_checklist_hash == curr_hash:
            self._steps_without_checklist_progress += 1
        else:
            self._steps_without_checklist_progress = 0
            self._last_checklist_hash = curr_hash

        return self._check_stalled()
