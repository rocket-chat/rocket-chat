"""Code search: tree-sitter symbol extraction with a ripgrep fallback.

Symbol extraction prefers **tree-sitter** (the ecosystem standard, cf. Aider's
repo-map via ``grep-ast``) when the grammar pack is installed, and degrades to a
definition-oriented ripgrep scan otherwise so the tool always works without
adding a hard build dependency.
"""

from __future__ import annotations

import importlib.util
import re
from typing import Any

from agent_core.tools.clamp import clamp_output
from agent_core.tools.tier2 import _sh_quote
from specifications.interfaces.sandbox import SandboxDriverProtocol

# Declaration keywords per language family, used by the ripgrep fallback.
_DEFINITION_PATTERNS: tuple[str, ...] = (
    r"^\s*(async\s+)?def\s+",
    r"^\s*class\s+",
    r"^\s*(export\s+)?(default\s+)?(async\s+)?function\s+",
    r"^\s*(export\s+)?(const|let|var)\s+\w+\s*=\s*(async\s*)?\(",
    r"^\s*(pub\s+)?(async\s+)?fn\s+",
    r"^\s*func\s+",
    r"^\s*(pub\s+)?(struct|enum|trait|interface|type)\s+",
)

_EXTENSION_LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
}


def tree_sitter_available() -> bool:
    """Whether the optional tree-sitter grammar pack is importable."""
    return (
        importlib.util.find_spec("tree_sitter") is not None
        and importlib.util.find_spec("tree_sitter_language_pack") is not None
    )


def language_for_path(path: str) -> str | None:
    """Map a file path to a tree-sitter language name, if known."""
    lowered = path.lower()
    for extension, language in _EXTENSION_LANGUAGES.items():
        if lowered.endswith(extension):
            return language
    return None


def build_symbol_query(terms: str, kind_any: bool = True) -> str:
    """Build a definition-oriented search pattern for the given terms.

    ``kind_any`` broadens the pattern to any declaration; otherwise the terms are
    treated as the target symbol name.
    """
    escaped = re.escape(terms.strip())
    keywords = "|".join(
        pattern.removeprefix(r"^\s*").removesuffix(r"\s+") for pattern in _DEFINITION_PATTERNS
    )
    return f"^\\s*({keywords})[^\\n]*{escaped}" if kind_any else f"^\\s*[^\\n]*{escaped}"


async def search_code(
    driver: SandboxDriverProtocol,
    session_id: str,
    query: str,
    path: str = ".",
    file_glob: str | None = None,
    max_results: int = 100,
) -> str:
    """Search for symbols matching a free-text query across the workspace."""
    pattern = build_symbol_query(query, kind_any=False)
    limit = max(1, min(max_results, 500))

    rg_glob = f"-g {_sh_quote(file_glob)} " if file_glob else ""
    grep_include = f"--include={_sh_quote(file_glob)} " if file_glob else ""
    command = (
        "if command -v rg >/dev/null 2>&1; then "
        f"rg --line-number --no-heading --color=never {rg_glob}"
        f"-e {_sh_quote(pattern)} {_sh_quote(path)} 2>/dev/null | head -n {limit}; "
        "else "
        f"grep -rnI {grep_include}-E -e {_sh_quote(pattern)} "
        f"{_sh_quote(path)} 2>/dev/null | head -n {limit}; "
        "fi"
    )
    result = await driver.exec_command(session_id=session_id, command=command, timeout_seconds=60)
    output = (result.stdout or "").strip()
    if not output:
        return f"No symbols found matching {query!r} under {path!r}."
    return clamp_output(output)


def extract_symbols(source: str, language: str) -> list[str]:
    """Extract top-level symbol names from source text using tree-sitter.

    Returns an empty list when the grammar pack is unavailable. Kept separate so
    it can be unit-tested with a stubbed tree-sitter and reused by the indexing
    pipeline.
    """
    if not tree_sitter_available():
        return []
    try:
        from tree_sitter_language_pack import get_parser  # type: ignore[import-not-found]
    except Exception:
        return []

    try:
        parser = get_parser(language)
    except Exception:
        return []

    tree = parser.parse(source.encode("utf-8"))
    names: list[str] = []
    _walk_symbols(tree.root_node, source.encode("utf-8"), names)
    return names


def _walk_symbols(node: Any, source: bytes, names: list[str]) -> None:
    """Depth-first walk collecting declaration identifiers."""
    if node.type in {
        "function_definition",
        "class_definition",
        "function_declaration",
        "function_item",
        "method_definition",
        "class_declaration",
        "struct_item",
        "enum_item",
        "interface_declaration",
        "type_declaration",
        "lexical_declaration",
    }:
        identifier = node.child_by_field_name("name")
        if identifier is not None:
            names.append(
                source[identifier.start_byte : identifier.end_byte].decode("utf-8", "ignore")
            )
    for child in getattr(node, "children", []):
        _walk_symbols(child, source, names)
