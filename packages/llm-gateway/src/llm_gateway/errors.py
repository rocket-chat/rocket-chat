"""Provider error classification and retryability checks (Item A4)."""

from __future__ import annotations

# Non-retryable error substrings / signatures that represent user or client configuration mistakes
_FATAL_PATTERNS = (
    "authentication",
    "unauthorized",
    "invalid api key",
    "api key not found",
    "permission denied",
    "model not found",
    "not_found",
    "invalid_request",
    "context_window_exceeded",
    "maximum context length",
    "content policy",
    "content filter",
)

# Retryable error substrings representing transient provider overload or network faults
_RETRYABLE_PATTERNS = (
    "rate limit",
    "429",
    "too many requests",
    "overloaded",
    "resource exhausted",
    "temporarily unavailable",
    "service unavailable",
    "503",
    "bad gateway",
    "502",
    "gateway timeout",
    "504",
    "internal server error",
    "500",
    "timeout",
    "timed out",
    "connection closed",
    "connection reset",
    "connection error",
    "network disconnect",
    "peer reset",
    "peer closed",
    "closed connection",
    "eof occurred",
    "broken pipe",
    "server disconnected",
    "remote protocol error",
    "remoteprotocolerror",
)


def is_retryable_provider_error(err: Exception | str) -> bool:
    """Classify whether a provider failure is transient and safe to retry.

    Retryable errors include rate limits (429), capacity overload, network drops,
    and 5xx server issues. Fatal errors (auth, 404, bad requests, context window)
    fail immediately without burning retries.
    """
    if isinstance(err, Exception):
        err_type = type(err).__name__
        if err_type in (
            "AuthenticationError",
            "NotFoundError",
            "InvalidRequestError",
            "BadRequestError",
            "ContextWindowExceededError",
            "BudgetExceededError",
        ):
            return False

        if err_type in (
            "RateLimitError",
            "APIConnectionError",
            "APITimeoutError",
            "Timeout",
            "InternalServerError",
            "ServiceUnavailableError",
            "ConnectionResetError",
            "ConnectionError",
            "TimeoutError",
        ):
            return True

        message = str(err).lower()
    else:
        message = str(err).lower()

    for fatal in _FATAL_PATTERNS:
        if fatal in message:
            return False

    for retryable in _RETRYABLE_PATTERNS:
        if retryable in message:
            return True

    return False
