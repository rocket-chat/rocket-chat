"""A dependency-free MCP stdio server (stdlib only) for tests that must run inside a sandbox."""

import json
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "Echo the given text back.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
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
    print("server saw", method, file=sys.stderr, flush=True)
    if method == "initialize":
        reply(
            request_id,
            {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "minimal", "version": "1"},
            },
        )
    elif method == "tools/list":
        reply(request_id, {"tools": TOOLS})
    elif method == "tools/call":
        text = message["params"]["arguments"].get("text", "")
        reply(
            request_id, {"content": [{"type": "text", "text": f"echo: {text}"}], "isError": False}
        )
    elif request_id is not None:
        reply(request_id, {})
