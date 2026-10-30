# `config-engine`

Settings registry, policy resolver and credential cipher for the Rocket Chat platform.

## Overview
- **Typed settings registry (`registry.py`):** every setting declares its kind, default, **policy** (`instance`: the operator, through the environment variables named by `env`; `org`: administrators; `org_default`: an organization default members, sessions and presets may change unless it is locked; `member`: each member), an optional `ceiling` (lower levels may only lower a number), bounds, the file that consumes it, and optionally a `label` and `deprecated` flag. `validate` rejects unknown keys, keys the writer may not set and malformed values; `validate_locked_keys` accepts only `org_default` keys.
- **One resolver (`policy.py`):** `resolve(spec, ResolveContext)` decides a value from the platform default, the operator's value, the organization's, the member's and the session-level presets (agents, Slack bots, channel profiles: `Preset`), honouring the policy, the lock and the ceiling, and returns it with its provenance (`Resolved`: `source`, `locked_by`, `refused`). `CascadingSettingsResolver` (`cascading.py`) resolves a whole domain with it (`ResolvedSettings.provenance`).
- **Retired keys (`LEGACY_KEYS`):** keys a release merged into others (`routing.*`, `governance.provider_allowlist`/`zero_retention`/`region`, `budgets.byok_policy`, `github.pr_creation_policy`, `github.commit_signing_mode`) with the translation of their values, read from rows a migration did not reach for one release.
- **Constraint enforcement:** forbids restricted models or images (with wildcard pattern support) and raises `PolicyViolationError`.
- **Credential cipher (`crypto.py`):** `CredentialCipher` encrypts the organization's and members' model credentials at rest using AES-256-GCM.
- **Environment variables with retired aliases:** `environment.env_value(canonical, *aliases)` reads the documented name, still honours the aliases and logs each alias once.

## Installation
```bash
uv sync
```

## Running Tests
```bash
uv run pytest tests/unit/test_config_engine.py tests/unit/test_policy_resolver.py tests/unit/test_settings_registry.py -v
```
