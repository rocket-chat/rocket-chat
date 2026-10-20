#!/usr/bin/env python3
"""Checks that keep the user documentation site honest.

* every page is in the navigation and every navigation entry exists;
* no page links into ``dev/`` (developer documentation is not part of the site);
* none of the retired concepts or old paths survive in a page;
* the defaults the site states match the code, and every environment variable the code reads is documented;
* every page's front matter is valid YAML (the site refuses to deploy otherwise).

Run it with ``uv run python scripts/check_docs.py``; ``tests/unit/test_docs_site.py`` runs it in CI.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "doc"
GENERATED = {"api/rest.md"}

# Phrases that describe things the product no longer does, or paths that moved.
RETIRED = (
    "repository picker",
    "repository-first",
    "/rocket session",
    "mint.json",
    "getting-started/",
    "deployment-and-ops/",
    "api-reference/",
    "architecture/",
)
_LINK = re.compile(r"\]\(([^)\s]+)\)")


def navigation_pages() -> list[str]:
    config = json.loads((DOC / "docs.json").read_text(encoding="utf-8"))
    pages: list[str] = []
    for tab in config["navigation"]["tabs"]:
        for group in tab["groups"]:
            pages.extend(group["pages"])
    return pages


def site_pages() -> set[str]:
    return {
        str(p.relative_to(DOC).with_suffix(""))
        for p in DOC.rglob("*.md")
        if "assets" not in p.relative_to(DOC).parts
    }


def check_navigation() -> list[str]:
    nav, pages = set(navigation_pages()), site_pages()
    problems = [f"navigation entry without a page: {name}" for name in sorted(nav - pages)]
    problems += [f"page missing from the navigation: {name}" for name in sorted(pages - nav)]
    return problems


def check_no_links_into_dev() -> list[str]:
    problems = []
    for page in DOC.rglob("*.md"):
        for target in _LINK.findall(page.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            resolved = (
                (page.parent / target.split("#")[0]).resolve()
                if not target.startswith("/")
                else None
            )
            if (
                "/dev/" in f"/{target}"
                or target.startswith("dev/")
                or (resolved is not None and ROOT / "dev" in resolved.parents)
            ):
                problems.append(f"{page.relative_to(ROOT)} links into dev/: {target}")
    return problems


def check_retired_phrases() -> list[str]:
    problems = []
    for page in DOC.rglob("*.md"):
        relative = str(page.relative_to(DOC))
        if relative in GENERATED:
            continue
        text = page.read_text(encoding="utf-8").lower()
        problems += [
            f"{page.relative_to(ROOT)} mentions retired '{phrase}'"
            for phrase in RETIRED
            if phrase in text
        ]
    return problems


def _documented_default(variable: str) -> str | None:
    text = (DOC / "install" / "configuration.md").read_text(encoding="utf-8")
    match = re.search(rf"\|\s*`{variable}`\s*\|[^|]*\|\s*`([^`]*)`\s*\|", text)
    return match.group(1) if match else None


def check_defaults() -> list[str]:
    sys.path.insert(0, str(ROOT))
    from api.services.metrics import DEFAULT_GLOBAL_CONCURRENCY_LIMIT
    from config_engine.registry import DEFAULT_SANDBOX_IMAGE

    from specifications.interfaces.agent import DEFAULT_MAX_TURNS

    expected = {
        "ROCKET_DEFAULT_SANDBOX_IMAGE": DEFAULT_SANDBOX_IMAGE,
        "ROCKET_GLOBAL_CONCURRENCY_LIMIT": str(DEFAULT_GLOBAL_CONCURRENCY_LIMIT),
        "AGENT_MAX_TURNS": str(DEFAULT_MAX_TURNS),
    }
    problems = []
    for variable, value in expected.items():
        documented = _documented_default(variable)
        if documented is None:
            problems.append(f"install/configuration.md does not document {variable}")
        elif documented != value:
            problems.append(f"{variable}: the site says `{documented}`, the code says `{value}`")
    return problems


# Variables the code reads that are not deployment configuration: the egress proxy's own
# container environment, the shell's, a platform convention, or switches of the test suite.
NOT_DEPLOYMENT_CONFIGURATION = frozenset(
    {
        "PATH",
        "PORT",
        "NODE_ENV",
        "EGRESS_ALLOW",
        "EGRESS_BLOCK_INTERNAL",
        "EGRESS_PINNED",
        "ROCKET_STRICT_SETTINGS",
        "ROCKET_VALIDATE_EVENTS",
        "TEST_DATABASE_URL",
    }
)
_ENV_READERS = {"getenv", "env_value"}


def _environment_names(source: Path) -> set[str]:
    """Names a source file reads with ``os.getenv``, ``os.environ`` or ``env_value``."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = node.func
            reader = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            is_environ_get = (
                reader == "get"
                and isinstance(func, ast.Attribute)
                and ast.unparse(func.value) == "os.environ"
            )
            if reader in _ENV_READERS or is_environ_get:
                names |= {
                    arg.value
                    for arg in node.args
                    if isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and re.fullmatch(r"[A-Z][A-Z0-9_]+", arg.value)
                }
        elif (
            isinstance(node, ast.Subscript)
            and ast.unparse(node.value) == "os.environ"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            names.add(node.slice.value)
    return names


def check_environment_variables() -> list[str]:
    """Every environment variable the code reads is in install/configuration.md."""
    documented = (DOC / "install" / "configuration.md").read_text(encoding="utf-8")
    read: dict[str, Path] = {}
    for root in ("apps/api/src", "packages"):
        for source in sorted((ROOT / root).rglob("*.py")):
            if "tests" in source.parts:
                continue
            for name in _environment_names(source):
                read.setdefault(name, source)
    return [
        f"install/configuration.md does not document {name} (read in {source.relative_to(ROOT)})"
        for name, source in sorted(read.items())
        if name not in NOT_DEPLOYMENT_CONFIGURATION and f"`{name}`" not in documented
    ]


def check_front_matter() -> list[str]:
    problems = []
    for page in sorted(DOC.rglob("*.md")):
        text = page.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            continue
        header = text[4:].split("\n---", 1)[0]
        try:
            yaml.safe_load(header)
        except yaml.YAMLError as err:
            line = str(err).splitlines()[0]
            problems.append(f"{page.relative_to(DOC)}: front matter is not valid YAML ({line})")
    return problems


def main() -> int:
    problems = [
        *check_navigation(),
        *check_no_links_into_dev(),
        *check_retired_phrases(),
        *check_defaults(),
        *check_environment_variables(),
        *check_front_matter(),
    ]
    for problem in problems:
        print(f"docs: {problem}")
    if not problems:
        print("docs: ok")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
