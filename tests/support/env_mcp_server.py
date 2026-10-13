"""A stdlib MCP stdio server whose one tool reports the environment it was started with."""

import json
import os
import sys

TOOLS = [
    {
        "name": "whoami",
        "description": "Report the configured base URL and API key.",
        "inputSchema": {"type": "object", "properties": {}},
    }
]


def reply(request_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    message = json.loads(raw)
    method, request_id = message.get("method"), message.get("id")
    if method == "initialize":
        reply(
            request_id,
            {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "env-server", "version": "1"},
            },
        )
    elif method == "tools/list":
        reply(request_id, {"tools": TOOLS})
    elif method == "tools/call":
        text = (
            f"base={os.environ.get('BASE_URL')} key={os.environ.get('API_KEY')} pid={os.getpid()}"
        )
        reply(request_id, {"content": [{"type": "text", "text": text}], "isError": False})
    elif request_id is not None:
        reply(request_id, {})
