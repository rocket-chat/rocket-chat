"""Git Engine package for version control, commit signing, and GitHub ingress."""

from git_engine.config import GitEngineSettings
from git_engine.engine import GitEngine
from git_engine.trailers import format_commit_message, parse_co_authors

__all__ = [
    "GitEngine",
    "GitEngineSettings",
    "format_commit_message",
    "parse_co_authors",
]
