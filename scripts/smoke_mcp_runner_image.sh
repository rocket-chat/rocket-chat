#!/bin/sh
# Prove the MCP runner image works as deployed: unprivileged, the toolchain MCP servers rely on is
# present, and a real MCP server can be started through its WebSocket and used by the client.
set -eu

IMAGE="${1:-rocket-chat-mcp-runner:dev}"
PORT="${2:-18100}"
TOKEN="smoke-token-0123456789abcdef"
NAME="mcp-runner-smoke-$$"

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -d --name "$NAME" -p "$PORT:8100" -e MCP_RUNNER_TOKEN="$TOKEN" --read-only \
    --tmpfs /tmp --tmpfs /cache:uid=1000 --cap-drop ALL --security-opt no-new-privileges "$IMAGE" >/dev/null

for _ in $(seq 1 30); do
    curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
    sleep 1
done
curl -fsS "http://127.0.0.1:$PORT/health"; echo

[ "$(docker exec "$NAME" id -u)" = "1000" ] || { echo "the runner must not run as root" >&2; exit 1; }
for tool in "node --version" "npx --version" "uvx --version" "git --version" "curl --version"; do
    docker exec "$NAME" sh -c "$tool" >/dev/null || { echo "missing in the image: $tool" >&2; exit 1; }
done
SERVER_B64="$(base64 < tests/support/minimal_mcp_server.py | tr -d '\n')"
PORT="$PORT" TOKEN="$TOKEN" SERVER_B64="$SERVER_B64" uv run python - <<'PY'
import asyncio, os

from mcp import Client
from mcp_runner.client import runner_stdio_transport


async def main() -> None:
    command = f"echo {os.environ['SERVER_B64']} | base64 -d > server.py && exec python3 server.py"
    transport = runner_stdio_transport(
        f"http://127.0.0.1:{os.environ['PORT']}",
        os.environ["TOKEN"],
        command,
        {},
        org_id="smoke",
        server_id="minimal",
    )
    async with asyncio.timeout(60):
        async with Client(transport) as client:
            tools = [t.name for t in (await client.list_tools()).tools]
            assert tools == ["echo"], tools
            result = await client.call_tool("echo", {"text": "hi"})
            assert result.content[0].text == "echo: hi", result
    print("MCP server ran through the runner")


asyncio.run(main())
PY
echo "mcp-runner image OK"
