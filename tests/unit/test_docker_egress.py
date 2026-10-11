"""Docker sandboxes with an egress policy join an internal network behind a proxy."""

from __future__ import annotations

from unittest.mock import MagicMock

import docker
import docker.errors
import pytest
from sandbox_driver import docker_egress
from sandbox_driver.docker_driver import DockerSandboxDriver
from sandbox_driver.docker_egress import (
    INTERNAL_NETWORK,
    POLICY_LABEL,
    PROXY_LABEL,
    ensure_egress,
    is_restricted,
    policy_id,
    proxy_environment,
    remove_unused_proxies,
)

from specifications.interfaces.sandbox import WorkspaceSpec


def _spec(
    allow: list[str] | None = None, block: bool = False, session: str = "s1"
) -> WorkspaceSpec:
    return WorkspaceSpec(
        session_id=session,
        tenant_org_id="o",
        tenant_user_id="u",
        container_image="img:1",
        egress_allowlist=allow or [],
        block_internal_subnets=block,
    )


def test_only_a_governed_sandbox_is_restricted() -> None:
    assert not is_restricted(_spec())
    assert is_restricted(_spec(["github.com"]))
    assert is_restricted(_spec(block=True))


def test_the_same_policy_shares_one_proxy_whatever_the_order_or_case() -> None:
    a = policy_id(_spec(["GitHub.com", "pypi.org"], True))
    b = policy_id(_spec(["pypi.org", "github.com "], True))
    assert a == b
    assert a != policy_id(_spec(["pypi.org"], True))
    assert a != policy_id(_spec(["github.com", "pypi.org"], False))


def test_the_sandbox_is_told_to_use_the_proxy_for_everything_but_itself() -> None:
    env = proxy_environment("rocket-egress-abc")
    assert env["HTTPS_PROXY"] == env["https_proxy"] == "http://rocket-egress-abc:3128"
    assert "127.0.0.1" in env["NO_PROXY"]


def _client(*, network: bool = False, proxy: bool = False) -> MagicMock:
    client = MagicMock()
    client.networks.get.side_effect = (
        (lambda name: MagicMock(name=name)) if network else docker.errors.NotFound("x")
    )
    if proxy:
        existing = MagicMock(status="running")
        existing.attrs = {"NetworkSettings": {"Networks": {INTERNAL_NETWORK: {}}}}
        client.containers.get.return_value = existing
    else:
        client.containers.get.side_effect = docker.errors.NotFound("x")
    client.images.get.return_value = MagicMock()
    return client


def test_first_use_creates_an_internal_network_and_a_locked_down_proxy() -> None:
    client = _client()
    spec = _spec(["github.com", "10.0.0.0/8"], True)
    settings = ensure_egress(client, spec)

    created = client.networks.create.call_args
    assert created.args[0] == INTERNAL_NETWORK and created.kwargs["internal"] is True

    run = client.containers.run.call_args.kwargs
    assert run["name"] == settings.proxy_name
    assert run["environment"]["EGRESS_ALLOW"] == "github.com,10.0.0.0/8"
    assert run["environment"]["EGRESS_BLOCK_INTERNAL"] == "1"
    assert run["cap_drop"] == ["ALL"] and run["read_only"] is True
    assert run["user"] == "65534:65534" and "no-new-privileges:true" in run["security_opt"]
    assert run["labels"][PROXY_LABEL] == settings.policy_id
    assert run["command"][:2] == ["python", "-c"] and "class EgressProxy" in run["command"][2]
    client.networks.get.return_value.connect.assert_not_called()  # created lazily below
    assert settings.network == INTERNAL_NETWORK


def test_an_existing_proxy_and_network_are_reused() -> None:
    client = _client(network=True, proxy=True)
    ensure_egress(client, _spec(["github.com"]))
    client.networks.create.assert_not_called()
    client.containers.run.assert_not_called()


def test_proxies_nobody_uses_are_removed_and_used_ones_kept() -> None:
    used = MagicMock(labels={PROXY_LABEL: "aaa"})
    unused = MagicMock(labels={PROXY_LABEL: "bbb"})
    unused.name = "rocket-egress-bbb"
    sandbox = MagicMock(labels={POLICY_LABEL: "aaa"})
    client = MagicMock()
    client.containers.list.side_effect = lambda **kw: (
        [sandbox] if kw["filters"]["label"] == POLICY_LABEL else [used, unused]
    )
    assert remove_unused_proxies(client) == ["rocket-egress-bbb"]
    unused.remove.assert_called_once_with(force=True)
    used.remove.assert_not_called()


def _driver(client: MagicMock) -> DockerSandboxDriver:
    return DockerSandboxDriver(client=client, default_image="img:1")


async def test_a_restricted_sandbox_starts_on_the_internal_network_with_proxy_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.containers.get.side_effect = docker.errors.NotFound("x")
    client.images.get.return_value = MagicMock()
    driver = _driver(client)
    monkeypatch.setattr(
        docker_egress,
        "ensure_egress",
        lambda c, s: docker_egress.EgressSettings(
            "p123", INTERNAL_NETWORK, "rocket-egress-p123", proxy_environment("rocket-egress-p123")
        ),
    )
    monkeypatch.setattr("sandbox_driver.docker_driver.ensure_egress", docker_egress.ensure_egress)
    spec = _spec(["github.com"])
    await driver.ensure_workspace(spec)
    await driver.start_sandbox("s1")

    run = client.containers.run.call_args.kwargs
    assert run["network"] == INTERNAL_NETWORK
    assert run["environment"]["HTTPS_PROXY"] == "http://rocket-egress-p123:3128"
    assert run["labels"][POLICY_LABEL] == "p123"


async def test_an_ungoverned_sandbox_keeps_the_default_network() -> None:
    client = MagicMock()
    client.containers.get.side_effect = docker.errors.NotFound("x")
    client.images.get.return_value = MagicMock()
    driver = _driver(client)
    await driver.ensure_workspace(_spec())
    await driver.start_sandbox("s1")
    run = client.containers.run.call_args.kwargs
    assert run["network"] is None and "HTTPS_PROXY" not in run["environment"]
    assert POLICY_LABEL not in run["labels"]
