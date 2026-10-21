"""Contract for durable compaction checkpoints.

Compaction checkpoints preserve the compacted context window (summary, anchors,
running context) up to a boundary sequence number so sessions can rehydrate
without losing history or recompacting from scratch after an API or sandbox restart.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from specifications.interfaces.llm import ChatMessage


@dataclass
class CompactionCheckpoint:
    """A durable snapshot of compacted session context."""

    id: str
    session_id: str
    org_id: str
    boundary_seq: int
    summary: str | None
    compacted_messages: list[ChatMessage]
    tokens_before: int
    tokens_after: int
    created_at: int
    # How the summary was made: "model", "model_retry" or "digest" (rebuilt from the tool
    # history); "legacy" for checkpoints stored before summaries were checked.
    summary_status: str = "legacy"


class CompactionStoreProtocol(ABC):
    """Storage contract for compaction checkpoints."""

    @abstractmethod
    async def record_compaction(self, checkpoint: CompactionCheckpoint) -> None:
        """Persist a compaction checkpoint."""

    @abstractmethod
    async def get_latest_compaction(
        self, session_id: str, org_id: str
    ) -> CompactionCheckpoint | None:
        """Fetch the most recent compaction checkpoint for a session."""

    @abstractmethod
    async def list_compactions(self, session_id: str, org_id: str) -> list[CompactionCheckpoint]:
        """List all compaction checkpoints for a session."""

    @abstractmethod
    async def delete_compactions(self, session_id: str, org_id: str) -> None:
        """Remove all compaction checkpoints for a session."""
