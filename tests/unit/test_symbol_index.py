import pytest
from api.services.code_index import CodeIndex, _symbol_boost, search_code_index


def test_symbol_boost_heuristics() -> None:
    func_chunk = "def run_workflow_engine(session_id: str) -> None:\n    pass"
    class_chunk = "class WorkflowEngine:\n    def __init__(self):\n        pass"
    plain_chunk = "logger.info('calling workflow engine now')"

    # Exact function definition match
    assert _symbol_boost(func_chunk, "run_workflow_engine") >= 0.35
    # Exact class definition match
    assert _symbol_boost(class_chunk, "WorkflowEngine") >= 0.35
    # Token match in definition
    assert _symbol_boost(func_chunk, "workflow") >= 0.20
    # Plain usage without symbol definition
    assert _symbol_boost(plain_chunk, "workflow") == 0.0


@pytest.mark.asyncio
async def test_search_code_index_boosts_symbol_definitions() -> None:
    index = CodeIndex()
    # Dummy embedding vectors
    dummy_vec = [0.1] * 1536

    index.add(
        path="src/workflow.py",
        start_line=1,
        text="def calculate_cost(tokens: int) -> float:\n    return tokens * 0.001",
        vector=dummy_vec,
    )
    index.add(
        path="src/other.py",
        start_line=10,
        text="# We should calculate_cost whenever tokens are reported\nlogger.debug('cost calculation')",
        vector=dummy_vec,
    )

    results = await search_code_index(index, "calculate_cost", limit=2)
    assert len(results) == 2
    # The definition chunk should have received the symbol boost and be ranked first
    assert results[0][1].path == "src/workflow.py"
    assert results[0][0] > results[1][0]
