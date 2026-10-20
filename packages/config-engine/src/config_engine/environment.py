"""Deployment environment variables that have one documented name and retired aliases."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_warned: set[str] = set()


def env_value(canonical: str, *deprecated: str) -> str | None:
    """The value of ``canonical``, else of the first retired alias that is set.

    An alias still works, but is logged once so an operator moves to the documented name before
    the alias is removed.
    """
    value = os.getenv(canonical)
    if value:
        return value
    for alias in deprecated:
        value = os.getenv(alias)
        if value:
            if alias not in _warned:
                _warned.add(alias)
                logger.warning("%s is deprecated; use %s instead", alias, canonical)
            return value
    return None
