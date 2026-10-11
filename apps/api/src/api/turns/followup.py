"""The suggested next instruction shown under a finished answer."""

from __future__ import annotations

import logging
import re
from typing import Any

from llm_gateway.structured import StructuredOutputError, generate_structured

from api.turns.shared import FollowUpSuggestion
from specifications.interfaces.llm import ChatMessage, ModelRequest

logger = logging.getLogger(__name__)

_LEAK_INDICATORS = (
    "i think",
    "password",
    "we need to",
    "the user",
    "output only",
    "instruction",
    "begin_of_sentence",
    "end_of_sentence",
    "as an ai",
    "suggested prompt",
    "here is",
    "rule:",
    "rules:",
    "introduction",
    "chapter",
)

_SUGGESTION_SYSTEM_PROMPT = (
    "You are an expert developer copilot. Based on the conversation, choose the single "
    "most logical next instruction, prompt, or command the developer would give next. "
    "Rules: 3 to 14 words, plain text only, no quotes, no conversational filler, directly actionable. "
    "Put it in the JSON 'suggestion' field."
)


def clean_answer(content: str) -> str:
    """The answer without internal ``<think>`` scratchpads."""
    if "<think>" in content:
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    return content.strip()


def _acceptable(candidate: str) -> bool:
    """A schema-valid string can still leak prompt text or exceed the length budget."""
    words = candidate.split()
    lowered = candidate.lower()
    return (
        bool(candidate)
        and 8 <= len(candidate) <= 120
        and 2 <= len(words) <= 18
        and not any(indicator in lowered for indicator in _LEAK_INDICATORS)
    )


async def _from_model(gateway: Any, model: str, prompt: str, answer: str) -> str | None:
    summary_slice = answer[-1500:] if len(answer) > 1500 else answer
    request = ModelRequest(
        model=model,
        messages=[
            ChatMessage(role="system", content=_SUGGESTION_SYSTEM_PROMPT),
            ChatMessage(role="user", content=prompt),
            ChatMessage(role="assistant", content=summary_slice),
        ],
        max_tokens=80,
        temperature=0.3,
        stream=False,
    )
    try:
        # Schema-constrained output validated by Pydantic, replacing the provider-specific
        # regex/backtick scraping this used to do.
        suggestion = await generate_structured(gateway, request, FollowUpSuggestion)
    except StructuredOutputError as err:
        # A gateway that cannot honour the schema degrades to the heuristics below rather
        # than leaving the developer with no suggestion.
        logger.debug("Structured suggestion generation failed: %s", err)
        return None
    first_line = suggestion.suggestion.split("\n")[0].strip()
    return first_line if _acceptable(first_line) else None


def _from_answer_text(answer: str) -> str | None:
    """An explicit next command or next step the assistant itself wrote."""
    command = re.search(
        r"(?:run|execute|try|test with)[:\s`]+([a-zA-Z0-9_\-./ ]{5,45})[`\n.]",
        answer,
        re.IGNORECASE,
    )
    if command and len(command.group(1).strip().split()) <= 6:
        return f"Run `{command.group(1).strip()}` to verify"
    action = re.search(
        r"(?:Next steps?|To proceed|You can now)[:\s*-]+\s*([A-Z][^\n\r.]{10,80})", answer
    )
    if action and 3 <= len(action.group(1).strip().split()) <= 14:
        return action.group(1).strip()
    return None


def _from_context(prompt: str, answer: str, executed_tools: list[dict[str, Any]]) -> str | None:
    """A generic but relevant suggestion when nothing better was found."""
    last_tool = executed_tools[-1]["name"] if executed_tools else ""
    prompt_lower, content_lower = prompt.lower(), answer.lower()
    if "test" in prompt_lower or "pytest" in prompt_lower or "test" in last_tool:
        return "Run the full test suite with coverage"
    if (
        last_tool in ("file_write", "file_edit", "apply_patch")
        or "modified" in content_lower
        or "updated" in content_lower
    ):
        return "Verify these changes with automated unit tests"
    if "git" in last_tool or "commit" in prompt_lower:
        return "Review git status and prepare commit"
    if "error" in content_lower or "fail" in content_lower or "exception" in content_lower:
        return "Inspect failure logs and trace the root cause"
    if "docker" in prompt_lower or "container" in prompt_lower:
        return "Check container status and sandbox health"
    if "api" in prompt_lower or "route" in prompt_lower:
        return "Test the endpoint with sample request payload"
    return "What are the recommended next steps?" if answer else None


async def suggest_followup(
    gateway: Any,
    model: str,
    prompt: str,
    answer: str,
    executed_tools: list[dict[str, Any]],
) -> str | None:
    """The next instruction to offer: from the model, else the answer's own words, else context."""
    clean = clean_answer(answer)
    suggestion = await _from_model(gateway, model, prompt, clean) if clean and gateway else None
    if not suggestion and clean:
        suggestion = _from_answer_text(clean)
    return suggestion or _from_context(prompt, clean, executed_tools)
