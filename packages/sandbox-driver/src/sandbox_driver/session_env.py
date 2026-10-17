"""Per-session execution environment, held only in the control plane's memory.

Credentials (a GitHub token) used to be written into the sandbox - `/root/.bashrc`,
`/etc/environment`, a git `insteadOf` URL, the container/pod spec - where they persisted on
the workspace volume, in `docker inspect` / the Pod object, and in every later process. They
are now supplied to each command at execution time instead: nothing secret is stored in the
sandbox, rotating a token takes effect on the next command, and a stopped sandbox holds none.
"""

from __future__ import annotations

# Persistent language caches residing on the workspace volume (Item C4).
DEFAULT_SANDBOX_CACHE_ENV: dict[str, str] = {
    "GOMODCACHE": "/workspace/.rocket/cache/go/pkg/mod",
    "GOCACHE": "/workspace/.rocket/cache/go/build",
    "UV_CACHE_DIR": "/workspace/.rocket/cache/uv",
    "PIP_CACHE_DIR": "/workspace/.rocket/cache/pip",
    "PNPM_HOME": "/workspace/.rocket/cache/pnpm",
    "CARGO_HOME": "/workspace/.rocket/cache/cargo",
}


class SessionEnvMixin:
    """Mixin for drivers: remember an env per session and merge it into every exec."""

    _session_env: dict[str, dict[str, str]]

    def _env_store(self) -> dict[str, dict[str, str]]:
        if not hasattr(self, "_session_env"):
            self._session_env = {}
        return self._session_env

    def set_session_env(self, session_id: str, env: dict[str, str]) -> None:
        """Replace the environment applied to every command of ``session_id``."""
        self._env_store()[session_id] = dict(env)

    def clear_session_env(self, session_id: str) -> None:
        self._env_store().pop(session_id, None)

    def merged_env(self, session_id: str, env: dict[str, str] | None) -> dict[str, str]:
        """Session env overlaid with the call's own env (the call wins)."""
        return {
            **DEFAULT_SANDBOX_CACHE_ENV,
            **self._env_store().get(session_id, {}),
            **(env or {}),
        }
