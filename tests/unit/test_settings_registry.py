"""Typed settings registry and the contract that keeps settings honest."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from config_engine import REGISTRY, validate, validate_locked_keys
from config_engine.registry import system_defaults

ROOT = Path(__file__).resolve().parents[2]


def test_every_setting_declares_an_existing_consumer_that_reads_it() -> None:
    """A key nobody reads must not exist: the consumer file has to mention the key."""
    missing: list[str] = []
    for domain, specs in REGISTRY.items():
        for key, spec in specs.items():
            path = ROOT / spec.consumer
            if not path.is_file():
                missing.append(f"{domain}.{key}: consumer {spec.consumer} does not exist")
                continue
            text = path.read_text()
            if f'"{key}"' not in text and f"'{key}'" not in text:
                missing.append(f"{domain}.{key}: not referenced in {spec.consumer}")
    assert not missing, "\n".join(missing)


# Local variable names that hold a resolved settings mapping, and the domain they hold.
_SETTINGS_VARIABLES = {
    "models_org": "models",
    "routing_org": "routing",
    "org_general": "general",
    "org_github": "github",
    "member_prefs": "user_preferences",
    "sandbox_cfg": "sandboxes",
}
_READERS = [
    "apps/api/src/api/turns/prompt.py",
    "apps/api/src/api/turns/finalise.py",
    "apps/api/src/api/turns/routing.py",
    "apps/api/src/api/turns/provisioning.py",
    "apps/api/src/api/turns/callbacks.py",
    "apps/api/src/api/services/sandbox_catalogue.py",
    "apps/api/src/api/routers/session_crud.py",
    "apps/api/src/api/routers/mcp_servers.py",
    "apps/api/src/api/routers/models_catalog.py",
    "apps/api/src/api/services/mcp_turn.py",
    "apps/api/src/api/main.py",
    "apps/api/src/api/services/turn_service.py",
    "apps/api/src/api/services/git_credentials.py",
]


def test_runtime_never_reads_an_undeclared_key() -> None:
    offenders: list[str] = []
    for relative in _READERS:
        tree = ast.parse((ROOT / relative).read_text())
        for node in ast.walk(tree):
            key: str | None = None
            variable: str | None = None
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                variable, key = node.func.value.id, str(node.args[0].value)
            elif (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and isinstance(node.slice, ast.Constant)
            ):
                variable, key = node.value.id, str(node.slice.value)
            if variable in _SETTINGS_VARIABLES and key is not None:
                domain = _SETTINGS_VARIABLES[variable]
                if key not in REGISTRY[domain]:
                    offenders.append(f"{relative}:{node.lineno} reads {domain}.{key}")
    assert not offenders, "\n".join(offenders)


def test_defaults_are_valid_for_their_own_specs() -> None:
    for domain in REGISTRY:
        defaults = {k: v for k, v in system_defaults(domain).items() if v is not None}
        result = validate(domain, defaults, writer="org")
        assert result.ok, (domain, result.errors)


def test_system_defaults_are_independent_copies() -> None:
    first = system_defaults("models")
    first["fallback_models"].append("x")
    assert "x" not in system_defaults("models")["fallback_models"]


@pytest.mark.parametrize(
    ("domain", "values", "writer", "expected_error"),
    [
        ("sandboxes", {"cpu_limit": "lots"}, "org", "must be a float"),
        ("sandboxes", {"cpu_limit": 0}, "org", "at least"),
        ("general", {"max_turns": 0}, "org", "at least 1"),
        ("general", {"max_turns": 500}, "org", "at most 200"),
        ("general", {"session_privacy_default": "everyone"}, "org", "one of"),
        ("general", {"max_turns": True}, "org", "must be a int"),
        ("models", {"fallback_models": [1]}, "org", "list of text"),
        ("github", {"app_token_permissions": "contents"}, "org", "object"),
        ("user_preferences", {"suggest_next_questions": "maybe"}, "user", "true or false"),
        ("user_preferences", {"nonsense": 1}, "user", "unknown setting"),
        ("github", {"commit_authorship_policy": "bot_only"}, "user", "administrator"),
        ("nope", {"a": 1}, "org", "unknown settings domain"),
    ],
)
def test_validation_rejects_bad_input(
    domain: str, values: dict[str, object], writer: str, expected_error: str
) -> None:
    result = validate(domain, values, writer=writer)  # type: ignore[arg-type]
    assert not result.ok
    assert expected_error in result.errors[0].message


def test_validation_coerces_and_normalises() -> None:
    result = validate(
        "mcp",
        {"allowed_transports": "sse, http ,", "max_user_servers": "5"},
        writer="org",
    )
    assert result.ok
    assert result.values == {"allowed_transports": ["sse", "http"], "max_user_servers": 5}
    assert validate(
        "user_preferences", {"suggest_next_questions": "false"}, writer="user"
    ).values == {"suggest_next_questions": False}


def test_both_scope_keys_are_writable_by_either_party_and_lockable() -> None:
    assert validate("user_preferences", {"git_branch_prefix": "x/"}, writer="org").ok
    assert validate("user_preferences", {"git_branch_prefix": "x/"}, writer="user").ok
    assert validate_locked_keys("user_preferences", ["preferred_model"]) == []
    assert validate_locked_keys("github", ["default_branch"])[0].message.startswith("only member")
    assert validate_locked_keys("github", ["nope"])[0].message.startswith("cannot lock")


def test_optional_integers_accept_clearing() -> None:
    assert validate("governance", {"session_retention_days": None}, writer="org").values == {
        "session_retention_days": None
    }
    assert not validate("general", {"max_turns": None}, writer="org").ok
