"""Unified logging configuration supporting LOG_LEVEL and LOG_FORMAT across Rocket Chat."""

import json
import logging
import os
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from typing import ClassVar

from opentelemetry import trace

# Correlation ids for the work in flight (request, turn, tool). A ContextVar rather than a
# logger adapter so every library log line emitted under the same task carries the tenant.
_NO_FIELDS: Mapping[str, str] = MappingProxyType({})
_LOG_FIELDS: ContextVar[Mapping[str, str]] = ContextVar("log_fields", default=_NO_FIELDS)

CORRELATION_FIELDS = ("request_id", "org_id", "user_id", "session_id")


def bind_log_context(**fields: str | None) -> None:
    """Add correlation ids to every log line emitted by the current task."""
    merged = {**_LOG_FIELDS.get(), **{k: v for k, v in fields.items() if v}}
    _LOG_FIELDS.set(merged)


@contextmanager
def log_context(**fields: str | None) -> Iterator[None]:
    """Scope correlation ids to a block; the previous context is restored on exit."""
    token = _LOG_FIELDS.set({**_LOG_FIELDS.get(), **{k: v for k, v in fields.items() if v}})
    try:
        yield
    finally:
        _LOG_FIELDS.reset(token)


class CorrelationFilter(logging.Filter):
    """Stamp each record with the bound correlation ids and the active trace/span ids."""

    def filter(self, record: logging.LogRecord) -> bool:
        fields = _LOG_FIELDS.get()
        for name in CORRELATION_FIELDS:
            setattr(record, name, fields.get(name, ""))
        span_context = trace.get_current_span().get_span_context()
        record.trace_id = f"{span_context.trace_id:032x}" if span_context.is_valid else ""
        record.span_id = f"{span_context.span_id:016x}" if span_context.is_valid else ""
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, safe for messages containing quotes and tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, str] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in (*CORRELATION_FIELDS, "trace_id", "span_id"):
            value = getattr(record, name, "")
            if value:
                payload[name] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


VALID_LOG_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "TRACE": logging.DEBUG,
}


class RocketConsoleFormatter(logging.Formatter):
    """Clean colorized console formatter for development and production."""

    LEVEL_COLORS: ClassVar[dict[int, str]] = {
        logging.DEBUG: "\x1b[36m",  # Cyan
        logging.INFO: "\x1b[32m",  # Green
        logging.WARNING: "\x1b[33m",  # Yellow
        logging.ERROR: "\x1b[31m",  # Red
        logging.CRITICAL: "\x1b[35m",  # Magenta
    }
    RESET = "\x1b[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelno, "")
        levelname = (
            f"{color}{record.levelname:<8}{self.RESET}" if color else f"{record.levelname:<8}"
        )
        time_str = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        name = f"[{record.name}]"
        ids = " ".join(
            f"{field}={value}"
            for field in ("org_id", "session_id", "request_id")
            if (value := getattr(record, field, ""))
        )
        suffix = f" ({ids})" if ids else ""
        return f"{time_str} {levelname} {name} {record.getMessage()}{suffix}"


def setup_logging(
    default_level: str = "INFO",
    default_format: str = "console",
) -> int:
    """Initialize root and module-level loggers with configurable level and format."""
    raw_level = (os.getenv("LOG_LEVEL") or default_level).upper().strip()
    log_level = VALID_LOG_LEVELS.get(raw_level, logging.INFO)

    log_format = (os.getenv("LOG_FORMAT") or default_format).lower().strip()

    handler = logging.StreamHandler(sys.stdout)
    if log_format == "json":
        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = RocketConsoleFormatter()

    handler.setFormatter(formatter)
    handler.addFilter(CorrelationFilter())

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Avoid duplicate handlers if setup_logging is called repeatedly
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # Set internal package loggers
    for pkg in (
        "api",
        "agent_core",
        "sandbox_driver",
        "slack_assistant",
        "git_engine",
        "llm_gateway",
        "config_engine",
    ):
        logging.getLogger(pkg).setLevel(log_level)

    # Silence noisy third-party loggers unless in TRACE/DEBUG
    if log_level > logging.DEBUG:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)

    return log_level
