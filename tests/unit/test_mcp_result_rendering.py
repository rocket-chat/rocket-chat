"""A large MCP result reaches the offload whole, so the agent can process it from the file."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from agent_core.tools.offload import offload_tool_output, read_offloaded
from api.services.mcp_runtime import MAX_RESULT_CHARS, render_result


def _result(text: str, *, is_error: bool = False) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], is_error=is_error)


def _prometheus_answer(series: int) -> str:
    data = [
        {"metric": {"namespace": "sigma", "container": f"svc-{i}"}, "value": [1, str(i * 1024)]}
        for i in range(series)
    ]
    return json.dumps({"data": data})


@pytest.mark.asyncio
async def test_a_large_one_line_json_result_is_saved_whole_and_shown_bounded() -> None:
    answer = _prometheus_answer(1_000)
    assert len(answer) > 40_000 and "\n" not in answer

    rendered = render_result(_result(answer))
    shown = await offload_tool_output(rendered, session_id="s-mcp", call_id="c1", threshold=6_000)

    assert rendered == answer
    assert "Full output saved to /workspace/.rocket/outputs/1_c1.txt" in shown
    assert len(shown) < 8_000
    saved = await read_offloaded("s-mcp", "/workspace/.rocket/outputs/1_c1.txt", None)
    assert saved is not None and json.loads(saved)["data"][-1]["metric"]["container"] == "svc-999"


def test_only_a_result_past_the_session_file_limit_is_cut() -> None:
    rendered = render_result(_result("x" * (MAX_RESULT_CHARS + 10)))

    assert rendered.startswith("x" * 100)
    assert rendered.endswith(f"[10 characters cut: the result exceeded {MAX_RESULT_CHARS:,}]")
