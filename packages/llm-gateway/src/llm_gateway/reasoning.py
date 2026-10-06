"""Parser and filter for extracting reasoning tokens from streaming LLM responses."""


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
