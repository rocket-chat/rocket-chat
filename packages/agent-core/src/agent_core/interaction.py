"""
Decision Gate and Human-In-The-Loop interaction manager.
"""

from __future__ import annotations

import asyncio

from specifications.interfaces.agent import ApprovalRequest, InteractiveQuestion


class DecisionGateManager:
    """Manages pending human clarifications, approval requests, and interactive gates."""

    def __init__(self) -> None:
        self._pending_questions: dict[str, tuple[str, asyncio.Event, InteractiveQuestion]] = {}
        self._question_answers: dict[str, tuple[list[str], str | None]] = {}
        self._pending_approvals: dict[str, tuple[str, asyncio.Event, ApprovalRequest]] = {}
        self._approval_decisions: dict[str, bool] = {}

    def register_question(
        self,
        question_id: str,
        session_id: str,
        wait_event: asyncio.Event,
        question_obj: InteractiveQuestion,
    ) -> None:
        self._pending_questions[question_id] = (session_id, wait_event, question_obj)

    def submit_question_answer(
        self,
        session_id: str,
        question_id: str,
        selected_options: list[str],
        custom_text: str | None = None,
    ) -> None:
        item = self._pending_questions.get(question_id)
        if not item or item[0] != session_id:
            raise KeyError(f"Pending question {question_id} not found for session {session_id}")

        _, wait_event, _ = item
        self._question_answers[question_id] = (selected_options, custom_text)
        wait_event.set()

    def get_question_answer(self, question_id: str) -> tuple[list[str], str | None]:
        return self._question_answers.get(question_id, ([], None))

    def register_approval(
        self,
        action_id: str,
        session_id: str,
        wait_event: asyncio.Event,
        approval_obj: ApprovalRequest,
    ) -> None:
        self._pending_approvals[action_id] = (session_id, wait_event, approval_obj)

    def submit_human_approval(
        self,
        session_id: str,
        action_id: str,
        approved: bool,
    ) -> None:
        item = self._pending_approvals.get(action_id)
        if not item or item[0] != session_id:
            raise KeyError(f"Pending approval {action_id} not found for session {session_id}")

        _, wait_event, _ = item
        self._approval_decisions[action_id] = approved
        wait_event.set()

    def get_approval_decision(self, action_id: str) -> bool:
        return self._approval_decisions.get(action_id, False)

    def cancel_session_gates(self, session_id: str) -> None:
        """Unblock any waiters belonging to the cancelled session."""
        for q_session_id, q_event, _ in self._pending_questions.values():
            if q_session_id == session_id:
                q_event.set()

        for a_session_id, a_event, _ in self._pending_approvals.values():
            if a_session_id == session_id:
                a_event.set()
