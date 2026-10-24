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

_DIAGNOSTIC_COMMAND_PREFIXES = (
    "git status",
    "git diff",
    "git log",
    "git show",
    "git branch",
    "ls",
    "cat",
    "echo",
    "head",
    "tail",
    "grep",
    "rg",
    "find",
    "pwd",
    "which",
)


def _is_diagnostic_read(clean_cmd: str) -> bool:
    """True for read-only inspection commands that do not change workspace or build state."""
    lower = clean_cmd.lower()
    return any(
        lower == prefix or lower.startswith(f"{prefix} ") for prefix in _DIAGNOSTIC_COMMAND_PREFIXES
    )


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
        self._command_history: list[tuple[str, int, int]] = []
        self._file_hashes: dict[str, list[str]] = defaultdict(list)
        self._file_contents: dict[str, str] = {}
        self._oscillation_counts: dict[str, int] = defaultdict(int)
        self._mutations_count: int = 0
        self._step_signatures: list[tuple[tuple[str, str], ...]] = []
        self._consecutive_monologues: int = 0

    def notify_file_mutation(self) -> None:
        """Call whenever a workspace file is modified or written."""
        self._mutations_count += 1
        self._step_signatures.clear()

    def record_step_signature(
        self, calls: list[tuple[str, str]], mutated: bool = False
    ) -> LoopDetectionResult | None:
        """Detect multi-step oscillation loops like ABAB or ABCABC."""
        if mutated or not calls:
            self._step_signatures.clear()
            return None

        clean_sig = tuple((name, " ".join(args.strip().split())) for name, args in calls)
        self._step_signatures.append(clean_sig)

        # Check period 2 (ABAB) over 4 steps
        if len(self._step_signatures) >= 4:
            s = self._step_signatures
            if s[-1] == s[-3] and s[-2] == s[-4] and s[-1] != s[-2]:
                return LoopDetectionResult(
                    level=1,
                    reason="Alternating tool oscillation detected (ABAB pattern).",
                    suggested_nudge=(
                        "[Loop Warning: You are alternating between the same set of actions without "
                        "workspace progress. Stop this cycle; change your strategy or diagnose what is blocking you.]"
                    ),
                )

        # Check period 3 (ABCABC) over 6 steps
        if len(self._step_signatures) >= 6:
            s = self._step_signatures
            if s[-1] == s[-4] and s[-2] == s[-5] and s[-3] == s[-6]:
                return LoopDetectionResult(
                    level=1,
                    reason="Cyclic tool oscillation detected (ABCABC pattern).",
                    suggested_nudge=(
                        "[Loop Warning: A 3-step repeating cycle was detected without workspace progress. "
                        "Stop repeating this loop; re-evaluate your assumptions.]"
                    ),
                )

        return None

    def record_monologue_step(self, has_tool_calls: bool) -> LoopDetectionResult | None:
        """Detect consecutive reasoning/explanation steps that perform no actions."""
        if has_tool_calls:
            self._consecutive_monologues = 0
            return None

        self._consecutive_monologues += 1
        if self._consecutive_monologues >= 3:
            return LoopDetectionResult(
                level=1,
                reason="Consecutive steps without action.",
                suggested_nudge=(
                    "[Pacing Warning: You have taken 3 consecutive steps without calling any tools. "
                    "Take action using tools or complete your turn with a final answer.]"
                ),
            )
        return None

    def record_command(self, command: str, exit_code: int) -> LoopDetectionResult | None:
        """Record command execution and evaluate failure loops."""
        clean_cmd = " ".join(command.strip().split())
        self._command_history.append((clean_cmd, exit_code, self._mutations_count))

        if exit_code == 0:
            return None

        # Count consecutive identical failing commands since the last mutation or success
        consecutive_same_fails = 0
        for cmd, code, mut_count in reversed(self._command_history):
            if mut_count != self._mutations_count:
                # A file was modified between commands; break the loop count
                break
            if cmd == clean_cmd and code != 0:
                consecutive_same_fails += 1
            elif code == 0 and not _is_diagnostic_read(cmd):
                # An intervening non-diagnostic command succeeded
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

    def record_file_edit(
        self, path: str, content: str, before_content: str | None = None
    ) -> LoopDetectionResult | None:
        """Record file edit and detect edit-revert oscillations."""
        self.notify_file_mutation()
        history = self._file_hashes[path]
        current = self._file_contents.get(path)

        if before_content is None:
            state = content
        elif current is None:
            history.append(hashlib.sha256(before_content.encode("utf-8")).hexdigest()[:16])
            state = content
        elif before_content in current:
            state = content.join(current.rsplit(before_content, 1))
        else:
            pre_state = f"{current}\n{before_content}"
            if history:
                history[-1] = hashlib.sha256(pre_state.encode("utf-8")).hexdigest()[:16]
            else:
                history.append(hashlib.sha256(pre_state.encode("utf-8")).hexdigest()[:16])
            state = f"{current}\n{content}"

        self._file_contents[path] = state
        content_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()[:16]
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
