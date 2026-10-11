"""Durable LLM transcript contract.

The *transcript* is the exact message list the model sees (system notices, user turns,
assistant tool calls, tool results, assistant answers). It is distinct from the UI history
on the session record, which only keeps what the cockpit renders. The orchestrator
appends to it as a turn progresses and rehydrates from it after a restart, a fork, or on
another replica.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from specifications.interfaces.llm import ChatMessage


class TranscriptStoreProtocol(ABC):
    """Append-only, per-session, tenant-scoped message log."""

    @abstractmethod
    async def load(self, session_id: str, org_id: str) -> list[ChatMessage]:
        """Every message of the session in order."""

    @abstractmethod
    async def append(self, session_id: str, org_id: str, messages: Sequence[ChatMessage]) -> None:
        """Append ``messages`` after the session's current last message."""

    @abstractmethod
    async def copy_prefix(
        self,
        source_session_id: str,
        target_session_id: str,
        org_id: str,
        user_turns: int | None = None,
    ) -> int:
        """Copy the first ``user_turns`` user turns (all when ``None``) into another session.

        Returns the number of messages copied. Used by fork so the child's next model
        request carries the parent's full tool context.
        """

    @abstractmethod
    async def delete(self, session_id: str, org_id: str) -> None:
        """Drop the session's transcript."""
