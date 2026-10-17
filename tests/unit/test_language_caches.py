from sandbox_driver.session_env import DEFAULT_SANDBOX_CACHE_ENV, SessionEnvMixin


class DummyDriver(SessionEnvMixin):
    pass


def test_language_caches_on_workspace_volume() -> None:
    driver = DummyDriver()
    env = driver.merged_env("sess_1", None)

    assert env["GOMODCACHE"] == "/workspace/.rocket/cache/go/pkg/mod"
    assert env["GOCACHE"] == "/workspace/.rocket/cache/go/build"
    assert env["UV_CACHE_DIR"] == "/workspace/.rocket/cache/uv"
    assert env["PIP_CACHE_DIR"] == "/workspace/.rocket/cache/pip"
    assert env["PNPM_HOME"] == "/workspace/.rocket/cache/pnpm"
    assert env["CARGO_HOME"] == "/workspace/.rocket/cache/cargo"


def test_language_caches_override_per_call() -> None:
    driver = DummyDriver()
    env = driver.merged_env("sess_1", {"GOCACHE": "/tmp/custom_cache"})

    assert env["GOCACHE"] == "/tmp/custom_cache"
    assert env["GOMODCACHE"] == DEFAULT_SANDBOX_CACHE_ENV["GOMODCACHE"]
