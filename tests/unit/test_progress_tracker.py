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


def test_checklist_stagnation_detection() -> None:
    tracker = ProgressTracker()
    tasks = [
        {"id": "1", "status": "completed"},
        {"id": "2", "status": "in_progress"},
        {"id": "3", "status": "pending"},
    ]

    # Call 1 sets initial baseline. Then 24 steps with same checklist state: no alarm
    for _ in range(25):
        assert tracker.record_checklist_state(tasks) is None

    # Step 25 without progress: triggers level 1 stall warning
    res1 = tracker.record_checklist_state(tasks)
    assert res1 is not None
    assert res1.level == 1
    assert "25 steps have completed without updating any checklist item" in res1.suggested_nudge

    # Run up to 74 steps
    for _ in range(49):
        tracker.record_checklist_state(tasks)

    # Step 75: triggers level 2 decision required
    res2 = tracker.record_checklist_state(tasks)
    assert res2 is not None
    assert res2.level == 2
    assert "has continued for 75 steps without updating any checklist task" in (
        res2.question_text or ""
    )


def test_checklist_all_completed_no_stagnation() -> None:
    tracker = ProgressTracker()
    all_done = [
        {"id": "1", "status": "completed"},
        {"id": "2", "status": "completed"},
    ]
    for _ in range(20):
        assert tracker.record_checklist_state(all_done) is None


def test_record_step_stagnation() -> None:
    tracker = ProgressTracker()
    for _ in range(24):
        assert tracker.record_step(has_active_checklist=True) is None

    res1 = tracker.record_step(has_active_checklist=True)
    assert res1 is not None
    assert res1.level == 1
    assert "25 steps have completed without updating any checklist item" in res1.suggested_nudge

    for _ in range(49):
        tracker.record_step(has_active_checklist=True)

    res2 = tracker.record_step(has_active_checklist=True)
    assert res2 is not None
    assert res2.level == 2
    assert "75 steps" in (res2.question_text or "")


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
