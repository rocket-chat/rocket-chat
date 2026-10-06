"""Unit tests for ReasoningStreamFilter."""

from llm_gateway.reasoning import ReasoningStreamFilter


def test_reasoning_stream_filter_basic() -> None:
    filter_ = ReasoningStreamFilter()
    emitted = filter_.feed("<think>I am analyzing the problem.</think>Here is the final solution.")
    emitted += filter_.flush()

    reasoning = "".join(text for st, text in emitted if st == "reasoning")
    content = "".join(text for st, text in emitted if st == "text")

    assert reasoning == "I am analyzing the problem."
    assert content == "Here is the final solution."
    assert "<think>" not in content
    assert "</think>" not in content
    assert "<think>" not in reasoning
    assert "</think>" not in reasoning


def test_reasoning_stream_filter_split_across_chunks() -> None:
    filter_ = ReasoningStreamFilter()
    chunks = [
        "Let me start. <th",
        "ink>First reasoning step. ",
        "Second reasoning step.</thi",
        "nk> The output is ready.",
    ]

    emitted: list[tuple[str, str]] = []
    for c in chunks:
        emitted.extend(filter_.feed(c))
    emitted.extend(filter_.flush())

    reasoning = "".join(text for st, text in emitted if st == "reasoning")
    content = "".join(text for st, text in emitted if st == "text")

    assert reasoning == "First reasoning step. Second reasoning step."
    assert content == "Let me start.  The output is ready."


def test_reasoning_stream_filter_non_tag_brackets() -> None:
    filter_ = ReasoningStreamFilter()
    emitted = filter_.feed("Condition: x < y and z > 10. Done.")
    emitted += filter_.flush()

    reasoning = "".join(text for st, text in emitted if st == "reasoning")
    content = "".join(text for st, text in emitted if st == "text")

    assert reasoning == ""
    assert content == "Condition: x < y and z > 10. Done."
