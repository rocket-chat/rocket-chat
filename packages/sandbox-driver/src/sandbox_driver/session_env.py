"""Per-session execution environment, held only in the control plane's memory.

Credentials (a GitHub token) used to be written into the sandbox - `/root/.bashrc`,
`/etc/environment`, a git `insteadOf` URL, the container/pod spec - where they persisted on
the workspace volume, in `docker inspect` / the Pod object, and in every later process. They
are now supplied to each command at execution time instead: nothing secret is stored in the
sandbox, rotating a token takes effect on the next command, and a stopped sandbox holds none.
"""

from __future__ import annotations

from collections.abc import Callable

from specifications.interfaces.sandbox import SESSION_ENV_LAYERS

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
    _session_env_layers: dict[str, dict[str, dict[str, str]]]
    _session_env_refreshers: dict[str, Callable[[], dict[str, str] | None]]

    def _env_store(self) -> dict[str, dict[str, str]]:
        if not hasattr(self, "_session_env"):
            self._session_env = {}
        return self._session_env

    def _layer_store(self) -> dict[str, dict[str, dict[str, str]]]:
        if not hasattr(self, "_session_env_layers"):
            self._session_env_layers = {}
        return self._session_env_layers

    def set_session_env_layer(self, session_id: str, layer: str, env: dict[str, str]) -> None:
        """Replace one layer (the sandbox type's or the agent's variables) of the session."""
        if layer not in SESSION_ENV_LAYERS:
            raise ValueError(f"Unknown session environment layer: {layer}")
        self._layer_store().setdefault(session_id, {})[layer] = dict(env)

    def set_session_env(self, session_id: str, env: dict[str, str]) -> None:
        """Replace the environment applied to every command of ``session_id``."""
        self._env_store()[session_id] = dict(env)

    def clear_session_env(self, session_id: str) -> None:
        self._env_store().pop(session_id, None)
        self._layer_store().pop(session_id, None)
        self._refresher_store().pop(session_id, None)

    def _refresher_store(self) -> dict[str, Callable[[], dict[str, str] | None]]:
        if not hasattr(self, "_session_env_refreshers"):
            self._session_env_refreshers = {}
        return self._session_env_refreshers

    def set_session_env_refresher(
        self, session_id: str, refresher: Callable[[], dict[str, str] | None]
    ) -> None:
        """Ask ``refresher`` for a new session env before every command; ``None`` keeps the old.

        For credentials that expire during a turn: renewing them only when a turn starts leaves
        every command after the expiry with a dead one.
        """
        self._refresher_store()[session_id] = refresher

    def merged_env(self, session_id: str, env: dict[str, str] | None) -> dict[str, str]:
        """Caches, then the session's layers, then the platform's env, then the call's own."""
        refresher = self._refresher_store().get(session_id)
        if refresher is not None:
            renewed = refresher()
            if renewed is not None:
                self.set_session_env(session_id, renewed)
        layers = self._layer_store().get(session_id, {})
        merged = dict(DEFAULT_SANDBOX_CACHE_ENV)
        for layer in SESSION_ENV_LAYERS:
            merged.update(layers.get(layer, {}))
        merged.update(self._env_store().get(session_id, {}))
        merged.update(env or {})
        return merged
