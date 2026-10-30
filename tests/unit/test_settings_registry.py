"""Typed settings registry and the contract that keeps settings honest."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from config_engine import REGISTRY, validate, validate_locked_keys
from config_engine.registry import system_defaults, writable_keys

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


SOURCE_ROOTS = ("apps/api/src", "packages")


def _settings_reads(tree: ast.AST) -> list[tuple[int, str, str]]:
    """``(line, domain, key)`` for every read of a mapping a function got from ``effective_settings``."""
    reads: list[tuple[int, str, str]] = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        domains: dict[str, str] = {}
        for node in ast.walk(function):
            call = node.value if isinstance(node, ast.Assign) else None
            if isinstance(call, ast.Await):
                call = call.value
            if (
                isinstance(call, ast.Call)
                and getattr(call.func, "id", getattr(call.func, "attr", "")) == "effective_settings"
                and len(call.args) >= 3
                and isinstance(call.args[2], ast.Constant)
                and isinstance(node, ast.Assign)
                and isinstance(node.targets[0], ast.Name)
            ):
                domains[node.targets[0].id] = str(call.args[2].value)
        for node in ast.walk(function):
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
            else:
                continue
            if variable in domains:
                reads.append((node.lineno, domains[variable], key))
    return reads


def test_runtime_never_reads_an_undeclared_key() -> None:
    """Every mapping returned by ``effective_settings`` is followed through its function, whatever
    the variable is called and wherever the code lives (``DeclaredSettings`` enforces the rest
    at runtime)."""
    offenders: list[str] = []
    for root in SOURCE_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            if "tests" in path.parts:
                continue
            for line, domain, key in _settings_reads(ast.parse(path.read_text())):
                if domain in REGISTRY and key not in REGISTRY[domain]:
                    offenders.append(f"{path.relative_to(ROOT)}:{line} reads {domain}.{key}")
    assert not offenders, "\n".join(offenders)


def test_defaults_are_valid_for_their_own_specs() -> None:
    for domain in REGISTRY:
        defaults = {k: v for k, v in system_defaults(domain).items() if v is not None}
        # Member-only settings are written by the member; every other one by an administrator.
        for writer in ("org", "member"):
            keys = writable_keys(domain, writer)  # type: ignore[arg-type]
            writable = {k: v for k, v in defaults.items() if k in keys}
            result = validate(domain, writable, writer=writer)  # type: ignore[arg-type]
            assert result.ok, (domain, writer, result.errors)


def test_system_defaults_are_independent_copies() -> None:
    first = system_defaults("models")
    first["fallback_models"].append("x")
    assert "x" not in system_defaults("models")["fallback_models"]


@pytest.mark.parametrize(
    ("domain", "values", "writer", "expected_error"),
    [
        ("sandboxes", {"cpu_limit": "lots"}, "org", "must be a float"),
        ("sandboxes", {"cpu_limit": 0}, "org", "at least"),
        ("general", {"max_turns": -1}, "org", "at least 0"),
        ("general", {"max_turns": 1000}, "org", "at most 500"),
        ("general", {"session_privacy_default": "everyone"}, "org", "one of"),
        ("general", {"max_turns": True}, "org", "must be a int"),
        ("models", {"fallback_models": [1]}, "org", "list of text"),
        ("github", {"app_token_permissions": "contents"}, "org", "object"),
        ("user_preferences", {"suggest_next_questions": "maybe"}, "member", "true or false"),
        ("user_preferences", {"nonsense": 1}, "member", "unknown setting"),
        ("github", {"bot_author_name": "Someone else"}, "member", "administrator"),
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
        "user_preferences", {"suggest_next_questions": "false"}, writer="member"
    ).values == {"suggest_next_questions": False}


def test_org_default_keys_are_writable_by_either_party_and_lockable() -> None:
    assert validate("user_preferences", {"git_branch_prefix": "x/"}, writer="org").ok
    assert validate("user_preferences", {"git_branch_prefix": "x/"}, writer="member").ok
    assert validate_locked_keys("user_preferences", ["preferred_model"]) == []
    assert validate_locked_keys("github", ["default_branch"])[0].message.startswith(
        "only organization defaults"
    )
    assert validate_locked_keys("github", ["nope"])[0].message.startswith("cannot lock")


def test_optional_integers_accept_clearing() -> None:
    assert validate("governance", {"session_retention_days": None}, writer="org").values == {
        "session_retention_days": None
    }
    assert not validate("general", {"max_turns": None}, writer="org").ok


async def test_effective_settings_refuse_a_key_the_registry_does_not_declare() -> None:
    from api.services.settings import UndeclaredSettingError, effective_settings

    models = await effective_settings(None, "org_declared", "models")

    assert models["max_tokens"] == 32_000
    assert models.get("context_target_tokens") == 200_000
    with pytest.raises(UndeclaredSettingError):
        models.get("max_wall_time_seconds")
    with pytest.raises(UndeclaredSettingError):
        models["monthly_budget_usd"]


async def test_undeclared_reads_only_log_outside_the_test_suite(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from api.services.settings import effective_settings

    monkeypatch.delenv("ROCKET_STRICT_SETTINGS")
    sandboxes = await effective_settings(None, "org_declared", "sandboxes")

    assert sandboxes.get("default_profile") is None
    assert "sandboxes.default_profile is not declared" in caplog.text


def test_a_setting_label_is_set_only_when_the_key_would_mislead() -> None:
    labelled = {
        f"{d}.{k}": s.label for d, specs in REGISTRY.items() for k, s in specs.items() if s.label
    }
    assert labelled["general.max_turns"] == "Max steps per turn"
