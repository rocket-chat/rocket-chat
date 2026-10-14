"""Tool calls a model wrote as plain text instead of making them.

Some models (DeepSeek's DSML format, older ``<tool_call>`` tags) sometimes emit their native
tool-call syntax in the message text when the provider's parser does not turn it into a
structured call. Nothing runs, the reply looks like an answer, and a person is shown markup.
The orchestrator uses this module to notice it, keep the markup off the live stream, and ask
the model to make the call properly.
"""

from __future__ import annotations

# DeepSeek writes its delimiters with a fullwidth bar and a lower-block "word joiner".
_BAR = "\uff5c"
_JOIN = "\u2581"

# Every one starts with "<", which is what lets the stream filter hold back only a short tail.
LEAK_MARKERS: tuple[str, ...] = (
    f"<{_BAR}DSML{_BAR}",
    "<|DSML|",
    f"<{_BAR}tool{_JOIN}calls{_JOIN}begin{_BAR}",
    f"<{_BAR}tool_calls_begin{_BAR}",
    "<function_calls>",
    "<tool_call>",
    "<invoke ",
)

MAX_LEAK_RETRIES = 2

LEAK_NOTICE = (
    "Your last reply wrote tool calls as text (markup such as a DSML block or <tool_call>), so "
    "nothing ran and it was discarded. Make each call through the function-calling interface "
    "with the parameter names from its definition, or answer in plain text. Do not write call "
    "markup."
)

LEAK_GIVE_UP = (
    "The model wrote a tool call as text instead of making it, more than once, so nothing "
    "ran. Please try again, or choose another model for this agent."
)


def contains_leaked_calls(text: str) -> bool:
    return any(marker in text for marker in LEAK_MARKERS)


def strip_leaked_calls(text: str) -> str:
    """The text before the first marker (what the model said before the markup)."""
    cut = min((i for m in LEAK_MARKERS if (i := text.find(m)) != -1), default=len(text))
    return text[:cut].strip()


class LeakFilter:
    """Passes streamed text through, withholding anything that is or may become call markup.

    ``feed`` returns what is safe to show now. A tail that could still turn into a marker (a
    lone ``<`` or the start of a DSML tag) is held until the next piece settles it; once a marker is seen the
    rest of the reply is withheld. ``finish`` releases a held tail that never became one.
    """

    def __init__(self) -> None:
        self._pending = ""
        self._leaking = False

    def feed(self, delta: str) -> str:
        if self._leaking:
            return ""
        text = self._pending + delta
        self._pending = ""
        start = text.find("<")
        while start != -1:
            tail = text[start:]
            if any(tail.startswith(marker) for marker in LEAK_MARKERS):
                self._leaking = True
                return text[:start]
            if any(marker.startswith(tail) for marker in LEAK_MARKERS):
                self._pending = tail
                return text[:start]
            start = text.find("<", start + 1)
        return text

    def finish(self) -> str:
        held, self._pending = self._pending, ""
        return "" if self._leaking else held
