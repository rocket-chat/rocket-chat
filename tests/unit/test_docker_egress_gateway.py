"""Docker sandboxes reach the platform's git gateway on the Docker host, policy or not."""

from __future__ import annotations

from sandbox_driver.docker_egress import HOST_GATEWAY, policy_id

from specifications.interfaces.sandbox import WorkspaceSpec


def _spec(**kwargs: object) -> WorkspaceSpec:
    return WorkspaceSpec(
        session_id="s",
        tenant_org_id="o",
        tenant_user_id="u",
        container_image="i",
        block_internal_subnets=True,
        **kwargs,  # type: ignore[arg-type]
    )


def test_the_gateway_endpoint_is_part_of_the_proxy_policy() -> None:
    plain = policy_id(_spec())
    with_gateway = policy_id(_spec(platform_endpoints=["host.docker.internal:8000"]))

    assert plain != with_gateway, "a proxy started without the exception is not reused"


def test_the_docker_host_has_the_same_name_on_every_engine() -> None:
    assert HOST_GATEWAY == {"host.docker.internal": "host-gateway"}
