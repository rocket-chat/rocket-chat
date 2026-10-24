"""Unit tests for loop and stall detection (Item A5)."""

from __future__ import annotations

from agent_core.progress import ProgressTracker


def test_repeated_failing_command_detection() -> None:
    tracker = ProgressTracker()
    cmd = "npm run build --prefix frontend"

    # Calls 1 & 2 fail: no alarm yet
    assert tracker.record_command(cmd, 1) is None
    assert tracker.record_command(cmd, 1) is None

    # Call 3 fails: level 1 nudge triggered
    res1 = tracker.record_command(cmd, 1)
    assert res1 is not None
    assert res1.level == 1
    assert "Command `npm run build --prefix frontend` has failed 3 times" in res1.suggested_nudge

    # Call 4 fails: still level 1
    res2 = tracker.record_command(cmd, 1)
    assert res2 is not None
    assert res2.level == 1

    # Call 5 fails: level 2 decision required triggered
    res3 = tracker.record_command(cmd, 1)
    assert res3 is not None
    assert res3.level == 2
    assert "has failed 5 times with exit code 1" in (res3.question_text or "")
    assert res3.options is not None
    assert "Try a different approach / skip this step" in res3.options


def test_successful_command_resets_failure_chain() -> None:
    tracker = ProgressTracker()
    cmd = "pytest tests/unit"

    assert tracker.record_command(cmd, 1) is None
    assert tracker.record_command(cmd, 1) is None
    # Succeeds
    assert tracker.record_command(cmd, 0) is None
    # Fails again: starts new count (not 3)
    assert tracker.record_command(cmd, 1) is None


def test_intervening_diagnostic_reads_do_not_reset_failing_command_loop() -> None:
    tracker = ProgressTracker()
    cmd = "pytest tests/unit"

    # Fail 1
    assert tracker.record_command(cmd, 1) is None
    # Intervening diagnostic read (git diff) exit 0
    assert tracker.record_command("git diff", 0) is None
    # Fail 2
    assert tracker.record_command(cmd, 1) is None
    # Intervening diagnostic read (git status) exit 0
    assert tracker.record_command("git status", 0) is None
    # Fail 3: loop detector must NOT be blinded by git diff / git status
    res = tracker.record_command(cmd, 1)
    assert res is not None
    assert res.level == 1
    assert "has failed 3 times with exit code 1" in res.suggested_nudge


def test_file_mutation_resets_failing_command_loop() -> None:
    tracker = ProgressTracker()
    cmd = "pytest tests/unit"

    assert tracker.record_command(cmd, 1) is None
    assert tracker.record_command(cmd, 1) is None
    # File edit happens
    tracker.notify_file_mutation()
    # Next failure should be treated as attempt 1 under the new file state
    assert tracker.record_command(cmd, 1) is None


def test_before_content_detects_immediate_revert() -> None:
    tracker = ProgressTracker()
    path = "src/main.py"
    initial_content = "def run(): return 1\n"
    edited_content = "def run(): return 2\n"

    # Edit 1 (from initial to edited)
    assert tracker.record_file_edit(path, edited_content, before_content=initial_content) is None
    # Revert immediately back to initial
    res = tracker.record_file_edit(path, initial_content)
    assert res is not None
    assert res.level == 1
    assert "Edit/revert oscillation detected on `src/main.py`" in res.suggested_nudge


def test_edit_revert_oscillation_detected() -> None:
    tracker = ProgressTracker()
    path = "src/utils.py"

    state_a = "def compute(): return 42\n"
    state_b = "def compute(): return 43\n"

    # Step 1: Write State A
    assert tracker.record_file_edit(path, state_a) is None
    # Step 2: Edit to State B
    assert tracker.record_file_edit(path, state_b) is None
    # Step 3: Revert back to State A (A -> B -> A)
    res = tracker.record_file_edit(path, state_a)
    assert res is not None
    assert res.level == 1
    assert "Edit/revert oscillation detected on `src/utils.py`" in res.suggested_nudge

    # Step 4: Edit again to State B (B -> A -> B is the second oscillation)
    res2 = tracker.record_file_edit(path, state_b)
    assert res2 is not None
    assert res2.level == 2
    assert "Repeated edit/revert oscillation detected on `src/utils.py`" in (
        res2.question_text or ""
    )


def test_classify_risky_git_operations() -> None:
    from agent_core.tools.git_tools import classify_risky_git_operation

    # Risky operations
    assert classify_risky_git_operation("git reset --hard HEAD~1") == "reset_hard"
    assert classify_risky_git_operation("git reset origin/main --hard") == "reset_hard"
    assert classify_risky_git_operation("git rebase main") == "rebase"
    assert classify_risky_git_operation("git merge feature") == "merge"
    assert classify_risky_git_operation("git push origin main --force") == "force_push"

    # Aborts should be ignored
    assert classify_risky_git_operation("git rebase --abort") is None
    assert classify_risky_git_operation("git merge --abort") is None
    assert classify_risky_git_operation("git cherry-pick --abort") is None

    # Safe operations
    assert classify_risky_git_operation("git status") is None
    assert classify_risky_git_operation("git log -n 5") is None
    assert classify_risky_git_operation("git diff") is None


def test_notice_for_manual_github_write() -> None:
    from agent_core.tools.git_tools import notice_for_manual_github_write

    # git push variants
    res = notice_for_manual_github_write("git push origin feat/fif-123")
    assert res is not None
    assert "Direct 'git push' from the sandbox shell cannot authenticate" in res
    assert "git_push" in res

    res2 = notice_for_manual_github_write("git push -u origin main")
    assert res2 is not None

    # gh pr create
    res3 = notice_for_manual_github_write("gh pr create --title 'Fix nil panic' --body '...'")
    assert res3 is not None
    assert "git_open_pr" in res3

    # gh issue create
    res4 = notice_for_manual_github_write("gh issue create -t 'Bug' -b '...'")
    assert res4 is not None
    assert "github_issues" in res4

    # benign commands without notice
    assert notice_for_manual_github_write("git status") is None
    assert notice_for_manual_github_write("git diff") is None
    assert notice_for_manual_github_write("go test ./...") is None
    assert notice_for_manual_github_write("make build") is None


def test_alternating_tool_oscillation_detection() -> None:
    tracker = ProgressTracker()
    step_a = [("file_read", '{"path": "a.py"}')]
    step_b = [("grep_search", '{"pattern": "foo"}')]

    # Step 1: A
    assert tracker.record_step_signature(step_a) is None
    # Step 2: B
    assert tracker.record_step_signature(step_b) is None
    # Step 3: A
    assert tracker.record_step_signature(step_a) is None
    # Step 4: B -> ABAB complete!
    res = tracker.record_step_signature(step_b)
    assert res is not None
    assert res.level == 1
    assert "ABAB pattern" in res.reason


def test_monologue_step_detection() -> None:
    tracker = ProgressTracker()
    assert tracker.record_monologue_step(has_tool_calls=False) is None
    assert tracker.record_monologue_step(has_tool_calls=False) is None
    res = tracker.record_monologue_step(has_tool_calls=False)
    assert res is not None
    assert res.level == 1
    assert "3 consecutive steps without calling any tools" in res.suggested_nudge

    # Calling a tool resets the counter
    assert tracker.record_monologue_step(has_tool_calls=True) is None
    assert tracker.record_monologue_step(has_tool_calls=False) is None


def test_block_edits_at_different_locations_do_not_false_positive_oscillation() -> None:
    tracker = ProgressTracker()
    # Edit 1 at function A with replacement "return None"
    assert tracker.record_file_edit("src/app.py", "return None", before_content="return 1") is None
    # Edit 2 at function B with the same replacement "return None" (different target_block)
    assert tracker.record_file_edit("src/app.py", "return None", before_content="return 2") is None
    # Edit 3 at function C with the same replacement "return None"
    assert tracker.record_file_edit("src/app.py", "return None", before_content="return 3") is None
    # Reverting function C back to "return 3" triggers true oscillation
    res = tracker.record_file_edit("src/app.py", "return 3", before_content="return None")
    assert res is not None and res.level == 1
