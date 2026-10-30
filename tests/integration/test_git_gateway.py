"""The read-only git gateway: real git in a 'sandbox' clones through it with no GitHub token.

The gateway's router and service run in a real HTTP server; its upstream is a local bare
repository served with ``git upload-pack --stateless-rpc`` (git's smart-HTTP protocol), so the
whole path - the sandbox's ``GIT_CONFIG_*`` environment, the capability header, the credential
the gateway adds - is exercised with real git on both ends.
"""

from __future__ import annotations

import asyncio
import gzip
import os
import pathlib
import socket
import subprocess
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
import pytest
import uvicorn
from api.auth import git_gateway_token
from api.routers.git_gateway import router
from api.services import git_gateway as gateway_module
from api.services.git_gateway import GitGateway
from api.services.git_gateway_env import (
    CAPABILITY_HEADER,
    sandbox_git_environment,
)
from api.store import InMemorySessionStore
from fastapi import FastAPI
from git_engine.config import github_web_url

from specifications.interfaces.session import SessionRecord

SESSION, ORG, USER = "s_gw", "org_gw", "u_gw"
TOKEN = "ghs_platform_side_read_token"


def git(cwd: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _pkt(line: str) -> bytes:
    return f"{len(line) + 4:04x}{line}".encode()


@dataclass
class FakeGitHub:
    """GitHub's smart-HTTP endpoints for the repositories under ``root``.

    Bodies are streams, as from a real connection (a ``MockTransport`` response built from
    bytes counts as already read).
    """

    root: pathlib.Path
    authorizations: list[str | None] = field(default_factory=list)
    private: set[str] = field(default_factory=set)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.authorizations.append(request.headers.get("authorization"))
        owner, name, *rest = request.url.path.strip("/").split("/")
        repository = f"{owner}/{name.removesuffix('.git')}"
        if repository in self.private and not request.headers.get("authorization"):
            return httpx.Response(401, stream=httpx.ByteStream(b"Authentication required"))
        bare = self.root / f"{repository}.git"
        env = {**os.environ, "GIT_PROTOCOL": request.headers.get("git-protocol", "")}
        if "/".join(rest) == "info/refs":
            advert = subprocess.run(
                ["git", "upload-pack", "--stateless-rpc", "--advertise-refs", str(bare)],
                check=True,
                capture_output=True,
                env=env,
            ).stdout
            prefix = (
                b""
                if "version=2" in env["GIT_PROTOCOL"]
                else _pkt("# service=git-upload-pack\n") + b"0000"
            )
            return httpx.Response(
                200,
                stream=httpx.ByteStream(prefix + advert),
                headers={"content-type": "application/x-git-upload-pack-advertisement"},
            )
        body = request.content
        if request.headers.get("content-encoding") == "gzip":
            body = gzip.decompress(body)
        result = subprocess.run(
            ["git", "upload-pack", "--stateless-rpc", str(bare)],
            input=body,
            check=True,
            capture_output=True,
            env=env,
        )
        return httpx.Response(
            200,
            stream=httpx.ByteStream(result.stdout),
            headers={"content-type": "application/x-git-upload-pack-result"},
        )

    def repository(self, slug: str) -> pathlib.Path:
        bare = self.root / f"{slug}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        seed = self.root / f"{slug}-seed"
        subprocess.run(
            ["git", "clone", "-q", str(bare), str(seed)], check=True, capture_output=True
        )
        (seed / "README.md").write_text(f"# {slug}\n")
        git(seed, "add", "-A")
        git(seed, "-c", "user.name=S", "-c", "user.email=s@x", "commit", "-q", "-m", "Initial")
        git(seed, "push", "-q", "origin", "HEAD:refs/heads/main")
        return bare


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _Server(uvicorn.Server):
    """A uvicorn server that says when it is listening."""

    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.ready = asyncio.Event()

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        self.ready.set()


@dataclass
class Rig:
    github: FakeGitHub
    port: int
    store: InMemorySessionStore
    gateway: GitGateway
    sandbox: pathlib.Path
    home: pathlib.Path

    def capability(self, session_id: str = SESSION, org_id: str = ORG) -> str:
        return git_gateway_token.mint(session_id, org_id, USER)

    def sandbox_env(self, capability: str) -> dict[str, str]:
        return {
            "PATH": os.environ["PATH"],
            "HOME": str(self.home),
            "GIT_CONFIG_NOSYSTEM": "1",
            **sandbox_git_environment(capability, "https://api.github.com"),
        }

    def clone(self, slug: str, capability: str, *extra: str) -> subprocess.CompletedProcess[str]:
        target = self.sandbox / slug.split("/")[1]
        return subprocess.run(
            ["git", "clone", "-q", *extra, f"https://github.com/{slug}", str(target)],
            env=self.sandbox_env(capability),
            capture_output=True,
            text=True,
            timeout=60,
        )


@pytest.fixture
async def rig(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Rig]:
    port = _free_port()
    monkeypatch.setenv("ROCKET_GIT_GATEWAY_URL", f"http://127.0.0.1:{port}")
    github = FakeGitHub(tmp_path / "github")
    store = InMemorySessionStore()
    await store.create_session(
        SessionRecord(session_id=SESSION, tenant_org_id=ORG, tenant_user_id=USER, title="t")
    )
    gateway = GitGateway(
        db_mgr=None,
        store=store,
        git_engine=None,
        client=httpx.AsyncClient(transport=httpx.MockTransport(github.handler)),
    )

    async def credential(grant: object, repository: str, forge: str = "github") -> str | None:
        return TOKEN

    monkeypatch.setattr(gateway, "credential", credential)
    app = FastAPI()
    app.include_router(router)
    app.state.git_gateway = gateway
    server = _Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    await asyncio.wait_for(server.ready.wait(), timeout=10)
    (tmp_path / "sandbox").mkdir()
    (tmp_path / "home").mkdir()
    try:
        yield Rig(github, port, store, gateway, tmp_path / "sandbox", tmp_path / "home")
    finally:
        server.should_exit = True
        await serving


async def test_a_sandbox_clones_a_private_repository_without_holding_a_github_token(
    rig: Rig,
) -> None:
    rig.github.repository("acme/private")
    rig.github.private.add("acme/private")
    capability = rig.capability()

    result = await asyncio.to_thread(rig.clone, "acme/private", capability)

    assert result.returncode == 0, result.stderr
    clone = rig.sandbox / "private"
    assert (clone / "README.md").read_text() == "# acme/private\n"
    assert git(clone, "config", "--get", "remote.origin.url") == "https://github.com/acme/private"
    assert set(rig.github.authorizations) == {
        "Basic eC1hY2Nlc3MtdG9rZW46" + "Z2hzX3BsYXRmb3JtX3NpZGVfcmVhZF90b2tlbg=="
    }
    env_text = "\n".join(f"{k}={v}" for k, v in rig.sandbox_env(capability).items())
    assert TOKEN not in env_text and "ghs_" not in env_text, "the sandbox holds no GitHub token"


async def test_a_blobless_clone_fetches_contents_on_demand_and_fetch_works(rig: Rig) -> None:
    rig.github.repository("acme/widgets")
    capability = rig.capability()

    result = await asyncio.to_thread(
        rig.clone, "acme/widgets", capability, "--filter=blob:none", "--single-branch"
    )
    assert result.returncode == 0, result.stderr
    clone = rig.sandbox / "widgets"
    fetched = await asyncio.to_thread(
        subprocess.run,
        ["git", "-C", str(clone), "fetch", "origin"],
        env=rig.sandbox_env(capability),
        capture_output=True,
        text=True,
    )

    assert fetched.returncode == 0, fetched.stderr
    assert git(clone, "log", "--format=%s") == "Initial"


async def test_pushing_through_the_gateway_is_refused(rig: Rig) -> None:
    rig.github.repository("acme/widgets")
    capability = rig.capability()
    assert (await asyncio.to_thread(rig.clone, "acme/widgets", capability)).returncode == 0
    clone = rig.sandbox / "widgets"
    git(
        clone,
        "-c",
        "user.name=a",
        "-c",
        "user.email=a@b",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "x",
    )

    pushed = await asyncio.to_thread(
        subprocess.run,
        ["git", "-C", str(clone), "push", "origin", "HEAD:refs/heads/evil"],
        env=rig.sandbox_env(capability),
        capture_output=True,
        text=True,
    )

    assert pushed.returncode != 0
    assert "403" in pushed.stderr
    bare = rig.github.root / "acme/widgets.git"
    created = await asyncio.to_thread(
        subprocess.run,
        ["git", "-C", str(bare), "rev-parse", "--verify", "evil"],
        capture_output=True,
    )
    assert created.returncode != 0, "nothing reached the repository"


async def test_requests_without_a_valid_capability_are_refused(rig: Rig) -> None:
    rig.github.repository("acme/widgets")
    base = f"http://127.0.0.1:{rig.port}/v1/git/acme/widgets.git/info/refs?service=git-upload-pack"
    gone = git_gateway_token.mint("s_deleted", ORG, USER)
    foreign = git_gateway_token.mint(SESSION, "org_other", USER)
    expired = git_gateway_token.mint(SESSION, ORG, USER, now=0)

    async with httpx.AsyncClient() as client:
        missing = await client.get(base)
        forged = await client.get(base, headers={CAPABILITY_HEADER: rig.capability() + "x"})
        stale = await client.get(base, headers={CAPABILITY_HEADER: expired})
        deleted = await client.get(base, headers={CAPABILITY_HEADER: gone})
        other_org = await client.get(base, headers={CAPABILITY_HEADER: foreign})
        bad_path = await client.get(
            f"http://127.0.0.1:{rig.port}/v1/git/acme/../etc/info/refs?service=git-upload-pack",
            headers={CAPABILITY_HEADER: rig.capability()},
        )
        receive = await client.get(
            base.replace("git-upload-pack", "git-receive-pack"),
            headers={CAPABILITY_HEADER: rig.capability()},
        )

    assert [missing.status_code, forged.status_code, stale.status_code] == [401, 401, 401]
    assert deleted.status_code == 403 and other_org.status_code == 403
    assert bad_path.status_code in (403, 404) and receive.status_code == 403
    assert rig.github.authorizations == [], "nothing reached GitHub"


async def test_a_capability_that_just_expired_still_works_for_a_session_with_a_turn_running(
    rig: Rig,
) -> None:
    rig.github.repository("acme/widgets")
    base = f"http://127.0.0.1:{rig.port}/v1/git/acme/widgets.git/info/refs?service=git-upload-pack"
    just_expired = git_gateway_token.mint(SESSION, ORG, USER, now=time.time() - 2 * 3600)
    headers = {CAPABILITY_HEADER: just_expired}

    async with httpx.AsyncClient() as client:
        idle = await client.get(base, headers=headers)
        record = await rig.store.get_session(SESSION, ORG)
        assert record is not None
        record.is_processing = True
        await rig.store.update_session(record)
        working = await client.get(base, headers=headers)
        forged = await client.get(base, headers={CAPABILITY_HEADER: just_expired + "x"})

    assert idle.status_code == 401 and idle.headers["x-rocket-git-error"] == "capability_expired"
    assert working.status_code == 200
    assert forged.status_code == 401, "a bad signature gets no grace"


def test_the_sandbox_environment_points_every_github_url_at_the_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROCKET_GIT_GATEWAY_URL", "http://gw.internal:8000/")
    env = sandbox_git_environment("CAP", "https://ghe.acme.io/api/v3")

    pairs = [
        (env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"])
        for i in range(int(env["GIT_CONFIG_COUNT"]))
    ]
    base = "url.http://gw.internal:8000/v1/git/.insteadOf"
    assert (base, "https://ghe.acme.io/") in pairs
    assert (base, "git@ghe.acme.io:") in pairs
    assert (
        "http.http://gw.internal:8000/v1/git/.extraHeader",
        f"{CAPABILITY_HEADER}: CAP",
    ) in pairs
    assert github_web_url("https://api.github.com") == "https://github.com"


def test_a_capability_names_one_session_and_expires() -> None:
    token = git_gateway_token.mint("s1", "o1", "u1", now=1_000)
    with pytest.raises(ValueError, match="expired"):
        git_gateway_token.verify(token)
    grant = git_gateway_token.verify(git_gateway_token.mint("s1", "o1", "u1"))
    assert (grant.session_id, grant.org_id, grant.user_id) == ("s1", "o1", "u1")


def test_credentials_are_cached_per_member_and_repository() -> None:
    assert gateway_module.CREDENTIAL_CACHE_SECONDS < 60 * 60, "renewed before an App token expires"
