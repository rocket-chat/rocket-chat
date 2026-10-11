#!/usr/bin/env python3
"""Generate doc/administer/settings-reference.md from the typed settings registry.

The reference is generated so it can never drift from the code: `tests/unit/test_settings_doc.py`
fails when the committed file differs from this script's output.

    uv run python scripts/generate_settings_reference.py          # rewrite the file
    uv run python scripts/generate_settings_reference.py --check  # exit 1 if out of date
"""

from __future__ import annotations

import sys
from pathlib import Path

from config_engine.registry import REGISTRY, SettingSpec

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "doc" / "administer" / "settings-reference.md"

DOMAIN_TITLES = {
    "general": "General",
    "models": "Models",
    "routing": "Cost routing",
    "sandboxes": "Sandboxes",
    "github": "Git & GitHub",
    "mcp": "MCP policy",
    "slack": "Slack",
    "governance": "Governance & data controls",
    "memory": "Long-term memory",
    "user_preferences": "Member preferences",
}
SCOPE_LABEL = {
    "org": "Organization",
    "user": "Member",
    "both": "Org default, member override",
}


def _default(spec: SettingSpec) -> str:
    if spec.default is None:
        return "unset"
    if spec.default == "" or spec.default == []:
        return "empty"
    return f"`{spec.default}`"


def _constraint(spec: SettingSpec) -> str:
    if spec.choices:
        return " / ".join(f"`{c}`" for c in spec.choices)
    bounds = [
        f"{label} {value:g}"
        for label, value in (("min", spec.minimum), ("max", spec.maximum))
        if value is not None
    ]
    return ", ".join(bounds) if bounds else spec.kind


def _mdx_safe(text: str) -> str:
    """Braces start expressions in MDX; escape them in prose."""
    return text.replace("{", "\\{").replace("}", "\\}")


def render() -> str:
    lines = [
        "# Settings Reference",
        "",
        "> Generated from the typed registry in `packages/config-engine/src/config_engine/registry.py` "
        "by `scripts/generate_settings_reference.py`. Do not edit by hand.",
        "",
        "Every setting declares who may write it (**Scope**), its type, its default and the "
        "runtime file that reads it. Organization settings are edited by administrators at "
        "`PUT /v1/admin/settings/{domain}`; member settings at `PUT /v1/settings/{domain}`. "
        "Settings with scope *Org default, member override* can be **locked** by an "
        "administrator so members cannot change them. Unknown keys, wrong-scope writes and "
        "out-of-range values are rejected with `422`.",
        "",
    ]
    for domain, specs in REGISTRY.items():
        lines += [f"## {DOMAIN_TITLES.get(domain, domain)} (`{domain}`)", ""]
        lines += [
            "| Setting | Scope | Type / values | Default | Description |",
            "| :-- | :-- | :-- | :-- | :-- |",
        ]
        for spec in specs.values():
            lines.append(
                f"| `{spec.key}` | {SCOPE_LABEL[spec.scope]} | {_constraint(spec)} | "
                f"{_default(spec)} | {_mdx_safe(spec.description)} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def main() -> int:
    content = render()
    if "--check" in sys.argv:
        return 0 if OUTPUT.exists() and OUTPUT.read_text() == content else 1
    OUTPUT.write_text(content)
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
