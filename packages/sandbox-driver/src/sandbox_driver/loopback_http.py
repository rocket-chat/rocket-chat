"""Make an HTTP request to a server listening inside a sandbox.

The platform cannot assume a route to a sandbox's network (the API may be in another
container, the sandbox in another pod), but it can always run a command in it. So the request
is made *from inside* with ``curl`` against ``127.0.0.1``, and the response is brought out
with ``sandbox_driver.blobs``. Only loopback is ever addressed; what the person can reach is
exactly what the sandbox's own processes serve.
"""

from __future__ import annotations

import base64
import re
import shlex
import uuid
from dataclasses import dataclass, field

from sandbox_driver.blobs import DEFAULT_MAX_BYTES, read_bytes
from sandbox_driver.exceptions import SandboxExecutionError
from specifications.interfaces.sandbox import SandboxDriverProtocol

MIN_PORT = 1024
MAX_PORT = 65535
# The body travels in the command line, which has a size limit.
MAX_REQUEST_BODY_BYTES = 64 * 1024
ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_PATH = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/\-?#\[\]]*$")


class LoopbackError(SandboxExecutionError):
    """The request could not be made or the server did not answer."""


@dataclass
class LoopbackResponse:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


def _validate(port: int, method: str, path: str) -> None:
    if not MIN_PORT <= port <= MAX_PORT:
        raise LoopbackError(f"Port must be between {MIN_PORT} and {MAX_PORT}.")
    if method not in ALLOWED_METHODS:
        raise LoopbackError(f"Method {method} is not allowed.")
    if not _PATH.match(path) or ".." in path.split("?", 1)[0].split("/"):
        raise LoopbackError("Invalid request path.")


def _parse_headers(raw: str) -> tuple[int, dict[str, str]]:
    """Status and headers of the *last* response in curl's dump (redirects are not followed)."""
    blocks = [block for block in re.split(r"\r?\n\r?\n", raw.strip()) if block.strip()]
    if not blocks:
        raise LoopbackError("The server sent no response.")
    lines = blocks[-1].splitlines()
    parts = lines[0].split(" ", 2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise LoopbackError("The server sent an invalid response.")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        if name and _HEADER_NAME.match(name.strip()):
            headers[name.strip().lower()] = value.strip()
    return int(parts[1]), headers


async def request_loopback(
    driver: SandboxDriverProtocol,
    session_id: str,
    port: int,
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    max_response_bytes: int = DEFAULT_MAX_BYTES,
    timeout_seconds: int = 30,
) -> LoopbackResponse:
    method = method.upper()
    _validate(port, method, path)
    if len(body) > MAX_REQUEST_BODY_BYTES:
        raise LoopbackError("The request body is too large for a preview request.")
    method_arg = "-I" if method == "HEAD" else f"-X {method}"
    tag = uuid.uuid4().hex[:12]
    head_file, body_file, in_file = (f"/tmp/rocket-http-{tag}.{x}" for x in ("head", "body", "in"))
    header_args = ""
    for name, value in (headers or {}).items():
        if not _HEADER_NAME.match(name) or "\n" in value or "\r" in value:
            raise LoopbackError(f"Invalid header {name!r}.")
        header_args += f" -H {shlex.quote(f'{name}: {value}')}"
    body_arg = ""
    if body:
        encoded = base64.b64encode(body).decode()
        body_arg = f" --data-binary @{in_file}"
        stage = f"printf %s {shlex.quote(encoded)} | base64 -d > {in_file} && "
    else:
        stage = ""
    command = (
        f"{stage}curl -sS --max-time {int(timeout_seconds)} {method_arg} "
        f"-D {head_file} -o {body_file}{header_args}{body_arg} "
        f"{shlex.quote(f'http://127.0.0.1:{port}{path}')} "
        f"&& base64 -w0 {head_file}"
    )
    result = await driver.exec_command(session_id, command, timeout_seconds=timeout_seconds + 15)
    try:
        if result.exit_code != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise LoopbackError(
                f"Nothing answered on port {port}: {detail[-1] if detail else 'connection failed'}"
            )
        head_b64 = result.stdout.splitlines()[0].strip()
        status, response_headers = _parse_headers(base64.b64decode(head_b64).decode("latin-1"))
        payload = b""
        if method != "HEAD" and status not in (204, 304):
            payload = await read_bytes(driver, session_id, body_file, max_bytes=max_response_bytes)
        return LoopbackResponse(status=status, headers=response_headers, body=payload)
    finally:
        await driver.exec_command(
            session_id, f"rm -f {head_file} {body_file} {in_file}", timeout_seconds=15
        )
