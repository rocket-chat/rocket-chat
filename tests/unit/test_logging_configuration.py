"""Unit tests for structured logging configuration and Helm-driven log levels."""

import logging

from api.logging_config import VALID_LOG_LEVELS, setup_logging


def test_valid_log_levels_mapping() -> None:
    assert VALID_LOG_LEVELS["DEBUG"] == logging.DEBUG
    assert VALID_LOG_LEVELS["INFO"] == logging.INFO
    assert VALID_LOG_LEVELS["ERROR"] == logging.ERROR


def test_setup_logging_applies_log_level() -> None:
    lvl = setup_logging(default_level="DEBUG", default_format="console")
    assert lvl == logging.DEBUG
    root = logging.getLogger()
    assert root.level == logging.DEBUG

    # Reset back to INFO
    lvl_info = setup_logging(default_level="INFO", default_format="console")
    assert lvl_info == logging.INFO
    assert root.level == logging.INFO
