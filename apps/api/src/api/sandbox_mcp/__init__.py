"""The sandbox MCP: the platform's sandboxes as tools for agents that run elsewhere.

The contract is ``specifications/interfaces/sandbox_mcp.py``. This package is kept apart from
the agent turn on purpose: a call here runs one platform tool in a session's sandbox and never
plans, prompts, titles, summarises or continues anything. Nothing in it reaches a model.

* ``server`` - the MCP endpoint (streamable HTTP) and the caller's identity.
* ``service`` - sandboxes as sessions: types, open, list, close, and the access checks.
* ``calls`` - one tool call: its execution context, the sandbox kept awake, the dispatch.
* ``callbacks`` - the model-free collaborators the proxied tools use.
* ``activity`` - what the idle reaper learns about calls made on any replica.
"""
