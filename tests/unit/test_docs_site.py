"""The user documentation site stays navigable, user-facing and in step with the code."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _checks():
    spec = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check_docs.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_page_is_in_the_navigation_and_every_entry_exists() -> None:
    assert _checks().check_navigation() == []


def test_the_site_never_links_into_the_developer_documentation() -> None:
    assert _checks().check_no_links_into_dev() == []


def test_no_page_describes_retired_features_or_old_paths() -> None:
    assert _checks().check_retired_phrases() == []


def test_the_defaults_the_site_states_match_the_code() -> None:
    assert _checks().check_defaults() == []


def test_the_developer_documentation_is_outside_the_site() -> None:
    assert (ROOT / "dev" / "architecture.md").exists()
    assert (ROOT / "dev" / "security-and-tenancy.md").exists()
    assert not list((ROOT / "doc").glob("architecture"))


def test_only_lasting_documents_are_kept_in_dev() -> None:
    """Plans, trackers and status notes belong in the issue tracker, not in the repository."""
    kept = sorted(path.name for path in (ROOT / "dev").rglob("*") if path.is_file())
    assert kept == ["architecture.md", "security-and-tenancy.md"]


ALLOWED_MARKDOWN = {
    "AGENTS.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "README.md",
    "dev/architecture.md",
    "dev/security-and-tenancy.md",
}


def _tracked_files() -> list[str]:
    import subprocess

    listing = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [name for name in listing.splitlines() if (ROOT / name).is_file()]


def test_markdown_outside_the_site_is_limited_to_lasting_documents() -> None:
    """Plans, roadmaps, trackers and status notes are not committed (see AGENTS.md)."""
    stray = [
        name
        for name in _tracked_files()
        if name.endswith(".md")
        and not name.startswith("doc/")
        and not name.endswith("/README.md")
        and name not in ALLOWED_MARKDOWN
    ]
    assert stray == []


def test_no_internal_tracking_references_remain() -> None:
    """Comments and docs must stand on their own, not point at plan items that no longer exist."""
    import re

    pattern = re.compile(r"plan M\d|[Ss]pec §|gap G\d|ship gate|tracker [MS]\d")
    scanned = (".py", ".ts", ".tsx", ".md", ".yaml", ".yml")
    offenders = [
        name
        for name in _tracked_files()
        if name.endswith(scanned)
        and name not in {"CHANGELOG.md", "tests/unit/test_docs_site.py"}
        and pattern.search((ROOT / name).read_text(errors="ignore"))
    ]
    assert offenders == []
