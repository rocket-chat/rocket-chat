"""A real Docker sandbox clones through the git gateway on the Docker host, with no token.

Runs only where Docker and the sandbox image (``rocket-chat-sandbox:dev``) are available. The
gateway listens on the host; its upstream is the local fake GitHub of ``test_git_gateway``, so
nothing depends on the internet. The restricted case goes through the egress proxy, which must
let the gateway through although it blocks internal addresses.
"""

from __future__ import annotations

import asyncio
import pathlib
import shutil
import subprocess
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import uvicorn
from api.auth import git_gateway_token
from api.routers.git_gateway import router
from api.services.git_gateway import GitGateway
from api.services.git_gateway_env import gateway_endpoint, sandbox_git_environment
from api.store import InMemorySessionStore
from fastapi import FastAPI
from sandbox_driver.docker_driver import DockerSandboxDriver

from specifications.interfaces.sandbox import WorkspaceSpec
from specifications.interfaces.session import SessionRecord
from tests.integration.test_git_gateway import FakeGitHub, _free_port, _Server

IMAGE = "rocket-chat-sandbox:dev"
ORG, USER = "org_gw_docker", "u_gw_docker"


def _docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    found = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True)
    return found.returncode == 0


pytestmark = pytest.mark.skipif(not _docker_ready(), reason=f"needs Docker and {IMAGE}")


@pytest.fixture
async def gateway(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[FakeGitHub, InMemorySessionStore]]:
    port = _free_port()
    monkeypatch.setenv("ROCKET_GIT_GATEWAY_URL", f"http://host.docker.internal:{port}")
    github = FakeGitHub(tmp_path / "github")
    github.repository("acme/widgets")
    github.private.add("acme/widgets")
    store = InMemorySessionStore()
    service = GitGateway(
        None, store, None, client=httpx.AsyncClient(transport=httpx.MockTransport(github.handler))
    )

    async def credential(grant: object, repository: str) -> str:
        return "ghs_platform_side"

    monkeypatch.setattr(service, "credential", credential)
    app = FastAPI()
    app.include_router(router)
    app.state.git_gateway = service
    server = _Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    await asyncio.wait_for(server.ready.wait(), timeout=10)
    try:
        yield github, store
    finally:
        server.should_exit = True
        await serving


@pytest.mark.parametrize("restricted", [False, True])
async def test_a_docker_sandbox_clones_a_private_repository_through_the_gateway(
    gateway: tuple[FakeGitHub, InMemorySessionStore], restricted: bool
) -> None:
    github, store = gateway
    session_id = f"gw-docker-{uuid.uuid4().hex[:8]}"
    await store.create_session(
        SessionRecord(session_id=session_id, tenant_org_id=ORG, tenant_user_id=USER, title="t")
    )
    driver = DockerSandboxDriver(default_image=IMAGE)
    await driver.ensure_workspace(
        WorkspaceSpec(
            session_id=session_id,
            tenant_org_id=ORG,
            tenant_user_id=USER,
            container_image=IMAGE,
            # Restricted: no route out except the proxy, which refuses internal addresses.
            block_internal_subnets=restricted,
            platform_endpoints=[gateway_endpoint()],
        )
    )
    try:
        await driver.start_sandbox(session_id)
        capability = git_gateway_token.mint(session_id, ORG, USER)
        driver.set_session_env(
            session_id, sandbox_git_environment(capability, "https://api.github.com")
        )

        cloned = await driver.exec_command(
            session_id,
            "git clone -q --filter=blob:none https://github.com/acme/widgets /workspace/widgets "
            "&& cat /workspace/widgets/README.md && env",
            timeout_seconds=120,
        )

        assert cloned.exit_code == 0, cloned.stderr
        assert "# acme/widgets" in cloned.stdout
        assert "ghs_" not in cloned.stdout, "the sandbox never sees the GitHub credential"
        assert github.authorizations, "the clone went through the gateway"
    finally:
        await driver.destroy_workspace(session_id)
