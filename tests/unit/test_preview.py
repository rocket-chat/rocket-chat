"""App preview: capability tokens, path-prefix rewriting and a sandboxed proxy."""

from __future__ import annotations

import asyncio
import base64
import re
import time
from typing import Any

import jwt
import pytest
from api.auth import preview_token
from api.auth.session_manager import get_session_secret
from api.main import app
from api.services.preview_rewrite import rewrite_css, rewrite_html, rewrite_location
from fastapi.testclient import TestClient
from sandbox_driver.blobs import CHUNK_BYTES
from sandbox_driver.fake_driver import FakeSandboxDriver
from sandbox_driver.loopback_http import LoopbackError, request_loopback

from specifications.interfaces.sandbox import ExecResult, WorkspaceSpec

PREFIX = "/v1/preview/tok/"

# --- rewriting ----------------------------------------------------------------------------


def test_root_relative_attributes_gain_the_prefix_and_others_are_left_alone() -> None:
    html = (
        "<html><head><title>x</title></head><body>"
        '<script src="/assets/app.js"></script><a href="/about">a</a>'
        '<img src="//cdn.example/x.png"><a href="page.html">r</a><a href="https://e.com/">e</a>'
        '<form action="/submit"></form></body></html>'
    )
    out = rewrite_html(html, PREFIX)
    assert f'src="{PREFIX}assets/app.js"' in out
    assert f'href="{PREFIX}about"' in out
    assert f'action="{PREFIX}submit"' in out
    assert 'src="//cdn.example/x.png"' in out
    assert 'href="page.html"' in out and 'href="https://e.com/"' in out
    assert f'<base href="{PREFIX}">' in out
    assert out.index("<base") < out.index("<title>"), "the base and shim come first in <head>"


def test_a_page_without_head_still_gets_the_base() -> None:
    assert rewrite_html("<p>hi</p>", PREFIX).startswith(f'<base href="{PREFIX}">')


def test_css_urls_are_prefixed() -> None:
    css = "body{background:url(/img/a.png)} .b{background:url('/img/b.png')} .c{background:url(data:x)}"
    out = rewrite_css(css, PREFIX)
    assert f"url({PREFIX}img/a.png)" in out and f"url('{PREFIX}img/b.png')" in out
    assert "url(data:x)" in out


@pytest.mark.parametrize(
    "location, expected",
    [
        ("/login", f"{PREFIX}login"),
        ("http://127.0.0.1:3000/next", f"{PREFIX}next"),
        ("http://localhost:3000", PREFIX),
        ("https://other.example/x", "https://other.example/x"),
        ("//other.example/x", "//other.example/x"),
        ("relative/path", "relative/path"),
    ],
)
def test_redirects_stay_inside_the_preview(location: str, expected: str) -> None:
    assert rewrite_location(location, PREFIX, 3000) == expected


def test_the_prefix_cannot_break_out_of_the_injected_script() -> None:
    out = rewrite_html("<head></head>", '/v1/preview/"</script><script>alert(1)//')
    assert "</script><script>alert(1)" not in out


# --- tokens -------------------------------------------------------------------------------


def test_a_token_grants_exactly_one_session_and_port() -> None:
    token, expires_at = preview_token.mint("s1", "org1", "u1", 3000)
    grant = preview_token.verify(token)
    assert (grant.session_id, grant.org_id, grant.user_id, grant.port) == ("s1", "org1", "u1", 3000)
    assert expires_at - time.time() <= preview_token.PREVIEW_TTL_SECONDS


def test_forged_expired_and_foreign_tokens_are_rejected() -> None:
    token, _ = preview_token.mint("s1", "org1", "u1", 3000)
    with pytest.raises(ValueError):
        preview_token.verify(token + "x")
    expired = jwt.encode(
        {
            "typ": "preview",
            "sid": "s",
            "org": "o",
            "uid": "u",
            "port": 3000,
            "exp": int(time.time()) - 5,
        },
        get_session_secret(),
        algorithm="HS256",
    )
    with pytest.raises(ValueError):
        preview_token.verify(expired)
    other_kind = jwt.encode(
        {
            "typ": "oauth_state",
            "sid": "s",
            "org": "o",
            "uid": "u",
            "port": 1,
            "exp": int(time.time()) + 60,
        },
        get_session_secret(),
        algorithm="HS256",
    )
    with pytest.raises(ValueError, match="Not a preview token"):
        preview_token.verify(other_kind)
    wrong_key = jwt.encode(
        {
            "typ": "preview",
            "sid": "s",
            "org": "o",
            "uid": "u",
            "port": 3000,
            "exp": int(time.time()) + 60,
        },
        "not-the-secret",
        algorithm="HS256",
    )
    with pytest.raises(ValueError):
        preview_token.verify(wrong_key)


# --- the loopback request and the proxy ---------------------------------------------------


class _AppDriver(FakeSandboxDriver):
    """A sandbox with a tiny web app on one port, reachable only through ``exec``."""

    def __init__(self, port: int = 3000) -> None:
        super().__init__()
        self.port = port
        self.routes: dict[str, tuple[int, dict[str, str], bytes]] = {}
        self.files: dict[str, bytes] = {}
        self.seen: list[dict[str, Any]] = []

    async def exec_command(self, session_id: str, command: str, *a: Any, **k: Any) -> ExecResult:
        if command.startswith("rm -f /tmp/rocket-http"):
            return ExecResult(0, "", "", 1)
        if "curl" in command:
            return self._curl(command)
        if command.startswith("stat -c %s "):
            path = command.split(" ", 3)[3].strip("'")
            return (
                ExecResult(0, f"{len(self.files[path])}\n", "", 1)
                if path in self.files
                else ExecResult(1, "", "no", 1)
            )
        match = re.match(r"dd if='?(\S+?)'? bs=(\d+) skip=(\d+) count=1", command)
        if match:
            data = self.files[match.group(1)]
            size, skip = int(match.group(2)), int(match.group(3))
            return ExecResult(
                0, base64.b64encode(data[skip * size : (skip + 1) * size]).decode(), "", 1
            )
        return await super().exec_command(session_id, command, *a, **k)

    def _curl(self, command: str) -> ExecResult:
        url = re.search(r"http://127\.0\.0\.1:(\d+)(\S*)'?", command)
        assert url is not None
        port, path = int(url.group(1)), url.group(2).rstrip("'")
        body_file = re.search(r"-o (\S+)", command).group(1)  # type: ignore[union-attr]
        self.seen.append({"port": port, "path": path, "command": command})
        if port != self.port:
            return ExecResult(7, "", f"curl: (7) Failed to connect to 127.0.0.1 port {port}", 1)
        status, headers, body = self.routes.get(
            path.split("?")[0], (404, {"Content-Type": "text/plain"}, b"nope")
        )
        raw = (
            f"HTTP/1.1 {status} X\r\n"
            + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
            + "\r\n"
        )
        self.files[body_file] = body
        return ExecResult(0, base64.b64encode(raw.encode()).decode(), "", 1)


async def _running(driver: _AppDriver, session_id: str) -> _AppDriver:
    await driver.ensure_workspace(
        WorkspaceSpec(
            session_id=session_id, tenant_org_id="o", tenant_user_id="u", container_image="i"
        )
    )
    await driver.start_sandbox(session_id)
    return driver


async def test_a_response_larger_than_one_chunk_arrives_intact() -> None:
    driver = await _running(_AppDriver(), "s1")
    payload = bytes(range(256)) * (CHUNK_BYTES * 2 // 256 + 40)
    driver.routes["/big"] = (200, {"Content-Type": "application/octet-stream"}, payload)
    response = await request_loopback(driver, "s1", 3000, "/big")
    assert response.status == 200 and response.body == payload


@pytest.mark.parametrize(
    "port, method, path",
    [
        (80, "GET", "/"),
        (70000, "GET", "/"),
        (3000, "TRACE", "/"),
        (3000, "GET", "/a/../etc"),
        (3000, "GET", "x"),
        (3000, "GET", "/a b"),
    ],
)
async def test_requests_that_could_target_something_else_are_refused(
    port: int, method: str, path: str
) -> None:
    driver = await _running(_AppDriver(), "s1")
    with pytest.raises(LoopbackError):
        await request_loopback(driver, "s1", port, path, method=method)
    assert driver.seen == []


async def test_only_loopback_is_ever_addressed_and_headers_cannot_inject_commands() -> None:
    driver = await _running(_AppDriver(), "s1")
    driver.routes["/"] = (200, {"Content-Type": "text/plain"}, b"ok")
    await request_loopback(driver, "s1", 3000, "/", headers={"Accept": "text/html; rm -rf /"})
    command = driver.seen[0]["command"]
    assert "http://127.0.0.1:3000/" in command
    assert "'Accept: text/html; rm -rf /'" in command, "the value is quoted, not executed"
    with pytest.raises(LoopbackError):
        await request_loopback(driver, "s1", 3000, "/", headers={"X-Bad\nHeader": "v"})


ALICE = {"X-Tenant-Org-Id": "test_org", "X-Tenant-User-Id": "pv_alice", "X-User-Role": "developer"}
BOB = {**ALICE, "X-Tenant-User-Id": "pv_bob"}


@pytest.fixture
def served(client: TestClient) -> tuple[TestClient, str, _AppDriver]:
    session_id = str(
        client.post("/v1/sessions", json={"title": "pv"}, headers=ALICE).json()["session_id"]
    )
    driver = _AppDriver()
    asyncio.run(_running(driver, session_id))
    driver.routes["/"] = (
        200,
        {
            "Content-Type": "text/html; charset=utf-8",
            "Set-Cookie": "sid=secret",
            "Content-Security-Policy": "default-src *",
        },
        b'<html><head></head><body><script src="/app.js"></script>hello</body></html>',
    )
    driver.routes["/app.js"] = (200, {"Content-Type": "application/javascript"}, b"console.log(1)")
    driver.routes["/go"] = (302, {"Location": "/landing", "Content-Type": "text/plain"}, b"")
    app.state.sandbox_driver = driver
    return client, session_id, driver


def _link(
    client: TestClient, session_id: str, port: int = 3000, headers: dict[str, str] = ALICE
) -> str:
    return str(
        client.post(
            f"/v1/sessions/{session_id}/preview", json={"port": port}, headers=headers
        ).json()["url"]
    )


def test_the_preview_is_served_sandboxed_and_rewritten_without_any_cookie(
    served: tuple[TestClient, str, _AppDriver],
) -> None:
    client, session_id, _ = served
    url = _link(client, session_id)

    page = client.get(url)  # no identity headers at all: the token is the capability
    assert page.status_code == 200
    assert f'src="{url}app.js"' in page.text and f'<base href="{url}">' in page.text
    assert page.headers["content-security-policy"].startswith("sandbox allow-scripts")
    assert "set-cookie" not in page.headers
    assert page.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"{url}app.js").text == "console.log(1)"
    assert client.get(f"{url}missing").status_code == 404


def test_redirects_are_kept_inside_the_preview(served: tuple[TestClient, str, _AppDriver]) -> None:
    client, session_id, _ = served
    url = _link(client, session_id)
    response = client.get(f"{url}go", follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"] == f"{url}landing"


def test_a_link_is_for_one_port_and_a_session_you_can_see(
    served: tuple[TestClient, str, _AppDriver],
) -> None:
    client, session_id, driver = served
    assert (
        client.post(
            f"/v1/sessions/{session_id}/preview", json={"port": 80}, headers=ALICE
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/v1/sessions/{session_id}/preview", json={"port": 3000}, headers=BOB
        ).status_code
        == 403
    )

    elsewhere = _link(client, session_id, port=4000)
    assert client.get(elsewhere).status_code == 502, "nothing listens on 4000 in the sandbox"
    assert all(call["port"] in (3000, 4000) for call in driver.seen)


def test_forged_or_expired_links_and_stopped_sandboxes_are_refused(
    served: tuple[TestClient, str, _AppDriver],
) -> None:
    client, session_id, _ = served
    assert client.get("/v1/preview/not-a-token/").status_code == 401
    url = _link(client, session_id)
    app.state.sandbox_driver = FakeSandboxDriver()
    assert client.get(url).status_code == 409
