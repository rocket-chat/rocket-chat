"""The read-only git gateway sandboxes clone and fetch through (see ``services/git_gateway.py``).

Not part of the public API: git in a sandbox is its only client, and it authenticates with the
session's gateway capability rather than a user session.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse, StreamingResponse

from api.auth import git_gateway_token
from api.services.git_gateway import (
    EXPIRED_MESSAGE,
    UPLOAD_PACK,
    GatewayRefusal,
    GitGateway,
    parse_repository_path,
    response_headers,
)
from api.services.git_gateway_env import CAPABILITY_HEADER, ROUTE_PREFIX

router = APIRouter(prefix=ROUTE_PREFIX, include_in_schema=False)

_PUSH_REFUSED = (
    "Pushing is not available from the sandbox. Commit, then call the git_push tool "
    "(or git_open_pr): it publishes with the organization's policy."
)


# A tool that sees git fail can tell an expired capability from a missing branch by this header.
_ERROR_HEADER_EXPIRED = {"X-Rocket-Git-Error": "capability_expired"}


def _gateway(request: Request) -> GitGateway:
    gateway: GitGateway = request.app.state.git_gateway
    return gateway


async def _proxy(request: Request, repository_path: str, suffix: str) -> Response:
    expired = False
    try:
        grant = git_gateway_token.verify(request.headers.get(CAPABILITY_HEADER, ""))
    except git_gateway_token.GatewayCredentialExpired as err:
        grant, expired = err.grant, True
    except ValueError as err:
        return PlainTextResponse(str(err), status_code=401, headers=_ERROR_HEADER_EXPIRED)
    try:
        repository = parse_repository_path(repository_path)
        gateway = _gateway(request)
        await gateway.authorize(grant, expired=expired)
        headers = {name.lower(): value for name, value in request.headers.items()}
        body = request.stream() if request.method == "POST" else None
        upstream, stream = await gateway.forward(
            grant, repository, request.method, suffix, request.url.query, headers, body
        )
    except GatewayRefusal as refusal:
        refusal_headers = _ERROR_HEADER_EXPIRED if str(refusal) == EXPIRED_MESSAGE else None
        return PlainTextResponse(str(refusal), status_code=refusal.status, headers=refusal_headers)
    return StreamingResponse(
        stream, status_code=upstream.status_code, headers=response_headers(upstream)
    )


@router.get("/{repository_path:path}/info/refs")
async def advertise_refs(repository_path: str, request: Request) -> Response:
    if request.query_params.get("service") != UPLOAD_PACK:
        return PlainTextResponse(_PUSH_REFUSED, status_code=403)
    return await _proxy(request, repository_path, "info/refs")


@router.post("/{repository_path:path}/git-upload-pack")
async def upload_pack(repository_path: str, request: Request) -> Response:
    return await _proxy(request, repository_path, UPLOAD_PACK)


@router.post("/{repository_path:path}/git-receive-pack")
async def receive_pack(repository_path: str) -> Response:
    return PlainTextResponse(_PUSH_REFUSED, status_code=403)
