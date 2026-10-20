# `config-engine`

Hierarchical Configuration & Policy Engine for Rocket Chat platform.

## Overview
This package implements `ConfigEngineProtocol` as defined in `specifications/interfaces/config.py`:
- **Hierarchical Inheritance Cascade:** Resolves configuration through System Defaults -> Org Policy -> Team Policy -> User Override.
- **Constraint & Policy Enforcement:** Forbids restricted models or images (with wildcard pattern support) and raises `PolicyViolationError`.
- **Encrypted BYOK Credentials:** Encrypts user and organization API keys at rest using AES-256-GCM.
- **Typed settings registry:** every setting declares its scope, consumer, bounds, and optionally a `label` and `deprecated` flag.
- **Environment variables with retired aliases:** `environment.env_value(canonical, *aliases)` reads the documented name, still honours the aliases and logs each alias once.

## Installation
```bash
uv sync
```

## Running Tests
```bash
uv run pytest tests/unit/test_config_engine.py -v
```
