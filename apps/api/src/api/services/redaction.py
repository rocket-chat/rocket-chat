"""Redaction pipeline for sensitive data crossing prompts, logs and Slack.

The pipeline scrubs a value in place and reports *how many* spans it replaced, so a
caller can always record that redaction occurred without ever storing the removed
value. It is deliberately independent of :mod:`api.services.memory`'s secret guard:
that guard *refuses* a write that looks like a credential, whereas this pipeline
rewrites the text and must keep the surrounding content usable.

Two pattern families exist, with different defaults to match the spec's stated risk
(false positives destroying legitimate content):

* **secret patterns** — API keys, JWTs, private keys, and ``password: …`` assignments.
  Active whenever redaction is enabled, because leaking a credential is never benign.
* **PII patterns** and **org regexes** — disabled by default and enabled per organisation.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from config_engine.crypto import CredentialCipher

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_FINGERPRINTS_PER_REDACTION",
    "REDACTION_MARKER",
    "RedactionPolicy",
    "RedactionResult",
    "Redactor",
    "fingerprint",
]

# One marker shape for every replacement so a reader can see *that* a span was removed
# and which family it belonged to, without a per-pattern vocabulary to learn.
REDACTION_MARKER = "[REDACTED:{label}]"

# A single tool result can hold thousands of matches; the audit row only needs enough
# fingerprints to correlate the event, so the list is bounded.
MAX_FINGERPRINTS_PER_REDACTION = 8

# Built-in secret detectors. These mirror the well-known credential prefixes rather than
# trying to be exhaustive: a novel provider still trips the ``password:`` assignment rule,
# and an organisation can add its own patterns.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
    (
        "assignment",
        re.compile(
            r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|client[_-]?secret)"
            r"\s*[:=]\s*\S+"
        ),
    ),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{20,}")),
)

# PII detectors kept separate because the spec ships them off by default: a false
# positive here silently destroys legitimate prose, which is worse than a rare miss.
_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"\b\+?\d[\d\s().-]{7,}\d\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
)


def fingerprint(value: str) -> str:
    """A correlation token for a redacted value that never reveals the full value."""
    return CredentialCipher.fingerprint(value)


@dataclass(frozen=True)
class RedactionPolicy:
    """Per-organisation redaction configuration.

    Defaults are safe out of the box: secret scrubbing on, PII and custom regexes off.
    """

    enabled: bool = True
    pii_enabled: bool = False
    extra_patterns: tuple[str, ...] = ()

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any] | None) -> RedactionPolicy:
        """Build a policy from an org ``governance`` settings mapping.

        Malformed keys are ignored rather than raising: a settings typo must degrade to
        the safe default, never break the turn path.
        """
        source = settings or {}
        raw_extra = source.get("redaction_patterns")
        extra: tuple[str, ...] = ()
        if isinstance(raw_extra, str):
            extra = tuple(part.strip() for part in raw_extra.split(",") if part.strip())
        elif isinstance(raw_extra, Sequence):
            extra = tuple(str(item).strip() for item in raw_extra if str(item).strip())
        return cls(
            enabled=bool(source.get("redaction_enabled", True)),
            pii_enabled=bool(source.get("pii_redaction_enabled", False)),
            extra_patterns=extra,
        )


@dataclass(frozen=True)
class RedactionResult:
    """The scrubbed text plus evidence of what was removed."""

    text: str
    count: int
    fingerprints: tuple[str, ...] = field(default_factory=tuple)

    @property
    def applied(self) -> bool:
        return self.count > 0


def _compile_extra(patterns: Sequence[str]) -> tuple[tuple[str, re.Pattern[str]], ...]:
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for pattern in patterns:
        try:
            compiled.append(("org", re.compile(pattern)))
        except re.error as err:
            # An org-supplied regex must never break the redaction path: drop it, keep
            # the built-in families working, and make the failure visible in logs.
            logger.warning("Ignoring invalid org redaction pattern %r: %s", pattern, err)
    return tuple(compiled)


class Redactor:
    """Applies a :class:`RedactionPolicy` to arbitrary text."""

    def __init__(self, policy: RedactionPolicy | None = None) -> None:
        self._policy = policy or RedactionPolicy()
        families = list(_SECRET_PATTERNS)
        if self._policy.pii_enabled:
            families.extend(_PII_PATTERNS)
        families.extend(_compile_extra(self._policy.extra_patterns))
        self._families = tuple(families)

    @property
    def policy(self) -> RedactionPolicy:
        return self._policy

    def redact(self, text: str | None) -> RedactionResult:
        """Replace every sensitive span and report the count and correlation tokens."""
        if not text or not self._policy.enabled:
            return RedactionResult(text=text or "", count=0)
        redacted = text
        total = 0
        tokens: list[str] = []
        for label, pattern in self._families:
            matches = pattern.findall(redacted)
            if not matches:
                continue
            total += len(matches)
            for match in matches[: MAX_FINGERPRINTS_PER_REDACTION - len(tokens)]:
                token = fingerprint(match if isinstance(match, str) else str(match))
                if token not in tokens:
                    tokens.append(token)
            redacted = pattern.sub(REDACTION_MARKER.format(label=label), redacted)
        return RedactionResult(text=redacted, count=total, fingerprints=tuple(tokens))

    def redact_text(self, text: str) -> tuple[str, int]:
        """Adapter matching the ``(text, count)`` redactor hook the memory service takes."""
        result = self.redact(text)
        return result.text, result.count
