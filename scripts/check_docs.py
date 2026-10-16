#!/usr/bin/env python3
"""Checks that keep the user documentation site honest.

* every page is in the navigation and every navigation entry exists;
* no page links into ``dev/`` (developer documentation is not part of the site);
* none of the retired concepts or old paths survive in a page;
* the defaults the site states match the code;
* every page's front matter is valid YAML (the site refuses to deploy otherwise).

Run it with ``uv run python scripts/check_docs.py``; ``tests/unit/test_docs_site.py`` runs it in CI.
"""

from __future__ import annotations

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

    expected = {
        "ROCKET_DEFAULT_SANDBOX_IMAGE": DEFAULT_SANDBOX_IMAGE,
        "ROCKET_GLOBAL_CONCURRENCY_LIMIT": str(DEFAULT_GLOBAL_CONCURRENCY_LIMIT),
    }
    problems = []
    for variable, value in expected.items():
        documented = _documented_default(variable)
        if documented is None:
            problems.append(f"install/configuration.md does not document {variable}")
        elif documented != value:
            problems.append(f"{variable}: the site says `{documented}`, the code says `{value}`")
    return problems


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
        *check_front_matter(),
    ]
    for problem in problems:
        print(f"docs: {problem}")
    if not problems:
        print("docs: ok")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
