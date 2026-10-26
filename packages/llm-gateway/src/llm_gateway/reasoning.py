"""Parser and filter for extracting reasoning tokens from streaming LLM responses."""

from typing import Any

_BLOCKS, _CONTENT = "thinking_blocks", "reasoning_content"
DEFAULT_MAX_REPLAY_REASONING_CHARS = 12_000


def strip_trailing_repetition(text: str) -> str:
    """Strips repetitive loop patterns from the end of a reasoning string."""
    words = text.split()
    if len(words) < 15:
        return text
    for cycle_len in range(1, 25):
        if len(words) < cycle_len * 3:
            continue
        pattern = words[-cycle_len:]
        repeats = 0
        idx = len(words)
        while idx >= cycle_len and words[idx - cycle_len : idx] == pattern:
            repeats += 1
            idx -= cycle_len
        threshold = 8 if cycle_len == 1 else (5 if cycle_len <= 4 else 3)
        if repeats >= threshold:
            kept_words = words[:idx]
            return " ".join(kept_words) if kept_words else ""
    return text


def sanitize_replayed_reasoning(
    content: str, max_chars: int = DEFAULT_MAX_REPLAY_REASONING_CHARS
) -> str:
    """Clamps and sanitizes replayed reasoning so runaway loops do not cascade into tool steps."""
    if not content:
        return ""
    cleaned = strip_trailing_repetition(content)
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars]
    return cleaned


def reasoning_replay(model: str) -> str | None:
    """How a provider family wants a model's reasoning back, if at all.

    Anthropic-style extended thinking (direct, or through OpenRouter, Bedrock or Vertex) needs
    its signed thinking blocks with the tool calls they led to; DeepSeek's reasoning models need
    ``reasoning_content`` back during a tool loop; OpenAI's Chat Completions API takes none, and
    Gemini's thought signatures already travel in the tool-call ids LiteLLM gives out.
    """
    name = (model or "").lower()
    if "claude" in name or "anthropic" in name:
        return _BLOCKS
    if "deepseek" in name:
        return _CONTENT
    return None


class ReasoningStreamFilter:
    """
    Parses a stream of text chunks to cleanly isolate in-band <think>...</think>
    reasoning blocks without leaking delimiters into either reasoning or text streams.
    """

    OPEN_TAG = "<think>"
    CLOSE_TAG = "</think>"

    def __init__(self) -> None:
        self.in_think: bool = False
        self._buffer: str = ""

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        """Feed a raw token chunk and return list of (stream_type, content) pairs."""
        self._buffer += chunk
        emitted: list[tuple[str, str]] = []

        while self._buffer:
            if not self.in_think:
                tag_idx = self._buffer.find(self.OPEN_TAG)
                if tag_idx != -1:
                    text_before = self._buffer[:tag_idx]
                    if text_before:
                        emitted.append(("text", text_before))
                    self._buffer = self._buffer[tag_idx + len(self.OPEN_TAG) :]
                    self.in_think = True
                    continue

                prefix_len = self._potential_prefix_length(self._buffer, self.OPEN_TAG)
                if prefix_len > 0:
                    safe_text = self._buffer[:-prefix_len]
                    if safe_text:
                        emitted.append(("text", safe_text))
                    self._buffer = self._buffer[-prefix_len:]
                    break

                emitted.append(("text", self._buffer))
                self._buffer = ""
                break
            else:
                tag_idx = self._buffer.find(self.CLOSE_TAG)
                if tag_idx != -1:
                    reasoning_before = self._buffer[:tag_idx]
                    if reasoning_before:
                        emitted.append(("reasoning", reasoning_before))
                    self._buffer = self._buffer[tag_idx + len(self.CLOSE_TAG) :]
                    self.in_think = False
                    continue

                prefix_len = self._potential_prefix_length(self._buffer, self.CLOSE_TAG)
                if prefix_len > 0:
                    safe_reasoning = self._buffer[:-prefix_len]
                    if safe_reasoning:
                        emitted.append(("reasoning", safe_reasoning))
                    self._buffer = self._buffer[-prefix_len:]
                    break

                emitted.append(("reasoning", self._buffer))
                self._buffer = ""
                break

        return emitted

    def flush(self) -> list[tuple[str, str]]:
        """Flush any pending buffered tokens at the end of the stream."""
        emitted: list[tuple[str, str]] = []
        if self._buffer:
            stream_type = "reasoning" if self.in_think else "text"
            emitted.append((stream_type, self._buffer))
            self._buffer = ""
        return emitted

    @staticmethod
    def _potential_prefix_length(text: str, tag: str) -> int:
        max_check = min(len(text), len(tag) - 1)
        for i in range(max_check, 0, -1):
            if tag.startswith(text[-i:]):
                return i
        return 0


def merge_reasoning_blocks(blocks: list[dict[str, Any]], pieces: list[dict[str, Any]]) -> None:
    """Fold streamed pieces into whole reasoning blocks, in place.

    A thinking block streams as text pieces followed by a piece carrying only its signature; a
    redacted block arrives whole. Providers verify the signature against the full text, so the
    pieces must be joined exactly.
    """
    for piece in pieces:
        kind = piece.get("type", "thinking")
        last = blocks[-1] if blocks else None
        open_thinking = (
            last is not None and last.get("type") == "thinking" and not last.get("signature")
        )
        if kind == "thinking" and open_thinking and last is not None:
            last["thinking"] = str(last.get("thinking") or "") + str(piece.get("thinking") or "")
            if piece.get("signature"):
                last["signature"] = piece["signature"]
        else:
            blocks.append(dict(piece))
