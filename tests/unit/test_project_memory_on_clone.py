from unittest.mock import AsyncMock, patch

import pytest
from api.services.memory import MemoryRecord
from api.turns.callbacks import _recall_project_memory_for_repo


@pytest.mark.asyncio
async def test_recall_project_memory_matches_repo_name() -> None:
    mock_db = object()
    mock_records = [
        MemoryRecord(
            id="m1",
            org_id="org_1",
            user_id="user_1",
            scope="org",
            kind="convention",
            content="backend-svc: run tests with uv run pytest",
            confidence=1.0,
            source_session_id=None,
            created_at=1000,
            last_used_at=None,
        ),
        MemoryRecord(
            id="m2",
            org_id="org_1",
            user_id="user_1",
            scope="org",
            kind="preference",
            content="frontend-svc: uses pnpm test",
            confidence=1.0,
            source_session_id=None,
            created_at=1000,
            last_used_at=None,
        ),
    ]

    with patch("api.turns.callbacks.MemoryService") as mock_mem_service_cls:
        instance = mock_mem_service_cls.return_value
        instance.recall = AsyncMock(return_value=mock_records)

        result = await _recall_project_memory_for_repo(
            mock_db, "org_1", "user_1", "/workspace/backend-svc"
        )
        assert result is not None
        assert "Project memory for backend-svc:" in result
        assert "backend-svc: run tests with uv run pytest" in result
        # frontend-svc should be filtered out
        assert "frontend-svc" not in result


@pytest.mark.asyncio
async def test_recall_project_memory_none_when_no_match_or_no_db() -> None:
    assert await _recall_project_memory_for_repo(None, "org", "user", "/workspace/foo") is None

    mock_db = object()
    with patch("api.turns.callbacks.MemoryService") as mock_mem_service_cls:
        instance = mock_mem_service_cls.return_value
        instance.recall = AsyncMock(return_value=[])

        result = await _recall_project_memory_for_repo(mock_db, "org", "user", "/workspace/foo")
        assert result is None
