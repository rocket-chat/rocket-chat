"""Model-facing tool definitions derived from the FastMCP handlers.

The handler signatures are the single source of truth for a tool's name, description and
parameters. FastMCP emits a faithful but verbose JSON schema (titles, ``additionalProperties``,
``anyOf: [X, null]`` for every optional); providers read a flatter schema more reliably and
spend fewer prompt tokens on it, so the schema is normalised here rather than hand-written
per tool.
"""

from __future__ import annotations

from typing import Any

from fastmcp.tools.function_tool import FunctionTool

from specifications.interfaces.llm import ToolDefinition

# Defaults are stated in each parameter's description ("default: 5"), where the model reads
# them; repeating them as a ``default`` keyword costs tokens and some providers reject it.
_DROPPED_KEYWORDS = frozenset({"title", "additionalProperties", "default", "$defs"})


def _is_null(schema: object) -> bool:
    return isinstance(schema, dict) and schema.get("type") == "null"


def _collapse_optional(schema: dict[str, Any]) -> dict[str, Any]:
    """``anyOf: [X, {type: null}]`` -> ``X``; optionality is already expressed by ``required``."""
    variants = schema.get("anyOf")
    if not isinstance(variants, list):
        return schema
    concrete = [variant for variant in variants if not _is_null(variant)]
    if len(concrete) != 1:
        return schema
    rest = {key: value for key, value in schema.items() if key != "anyOf"}
    return {**concrete[0], **rest}


def _inline_refs(schema: dict[str, Any], definitions: dict[str, Any]) -> dict[str, Any]:
    """Replace ``$ref: #/$defs/X`` with the definition, as providers differ on ref support."""
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        resolved = definitions[reference.removeprefix("#/$defs/")]
        return _inline_refs(
            {**resolved, **{k: v for k, v in schema.items() if k != "$ref"}}, definitions
        )
    return {
        key: (
            {name: _inline_refs(sub, definitions) for name, sub in value.items()}
            if key == "properties" and isinstance(value, dict)
            else _inline_refs(value, definitions)
            if key == "items" and isinstance(value, dict)
            else value
        )
        for key, value in schema.items()
    }


def normalise_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip schema noise from a FastMCP-generated parameter schema."""
    return _normalise(_inline_refs(schema, schema.get("$defs", {})))


def _normalise(schema: dict[str, Any]) -> dict[str, Any]:
    schema = _collapse_optional(schema)
    cleaned: dict[str, Any] = {}
    for keyword, value in schema.items():
        if keyword in _DROPPED_KEYWORDS:
            continue
        if keyword == "properties" and isinstance(value, dict):
            cleaned[keyword] = {name: _normalise(sub) for name, sub in value.items()}
        elif keyword == "items" and isinstance(value, dict):
            cleaned[keyword] = _normalise(value)
        else:
            cleaned[keyword] = value
    return cleaned


def definition_from_tool(tool: FunctionTool) -> ToolDefinition:
    return ToolDefinition(
        name=tool.name,
        description=tool.description or "",
        parameters_json_schema=normalise_schema(tool.parameters),
    )
