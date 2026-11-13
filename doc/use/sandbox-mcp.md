---
title: Sandboxes for your own agents (MCP)
description: Use the organization's sandboxes, git and GitHub tools from Claude Code, Cursor or any MCP client.
---

# Sandboxes for your own agents (MCP)

The sandbox MCP lets an agent that runs elsewhere (Claude Code, Cursor, Codex, an agent of your own) work in your organization's sandboxes. It gets the same image, variables, network rules and git access as the platform's own agent, and the same pause and resume.

It is a set of tools, not an agent: no model of the platform's is ever called, so it uses none of your organization's model budget. Your agent does the thinking; Rocket Chat runs its commands.

An administrator turns it on under **Settings → Sandboxes** (`mcp_enabled`, off by default).

## Connect

1. Create a personal API token: **Settings → API tokens** in the cockpit (or the [API](/api/index#personal-api-tokens)). Copy it when it is shown; it is not shown again.
2. Add a remote (streamable HTTP) MCP server to your client:
   - URL: `https://<host>/v1/sandbox-mcp/`
   - Header: `Authorization: Bearer <token>`

The token acts as you: the sandbox types you may open, and the GitHub account your pushes use, are yours. Keep it in an environment variable rather than in a file you share. In the examples below it is `ROCKET_TOKEN`.

**Claude Code**

```bash
claude mcp add --transport http rocket-sandboxes https://<host>/v1/sandbox-mcp/ \
  --header "Authorization: Bearer $ROCKET_TOKEN"
```

**Codex** (`~/.codex/config.toml`; Codex reads the token from the named variable when it starts)

```toml
[mcp_servers.rocket-sandboxes]
url = "https://<host>/v1/sandbox-mcp/"
bearer_token_env_var = "ROCKET_TOKEN"
```

**Pi** (`pi mcp add`, or `url` and `headers` in `~/.pi/agent/mcp.json`)

```bash
pi mcp add rocket-sandboxes --url https://<host>/v1/sandbox-mcp/ --bearer-token-env-var ROCKET_TOKEN
```

**OpenCode**

```bash
opencode mcp add rocket-sandboxes --url https://<host>/v1/sandbox-mcp/ \
  --header "Authorization=Bearer $ROCKET_TOKEN"
```

**Cursor, VS Code and other clients:** a remote server with `url` and a `headers` entry for `Authorization`.

A client that connects without the header, or with a token that was revoked or has expired, is refused (`401`). Some clients then try to sign in with OAuth and report that it failed. The sandbox MCP does not offer an OAuth sign-in yet, so clients that only connect that way (web chat apps' connectors) cannot use it; set the header instead.

## Work in a sandbox

| Tool | What it does |
| :--- | :--- |
| `sandbox_types` | The types you may open: image, capabilities, and the names of the variables every command gets (never their values). |
| `sandbox_open` | Opens a sandbox of a type (the default type if you name none) and returns its `sandbox_id`. |
| `sandbox_list` | Your open sandboxes. |
| `sandbox_close` | Deletes a sandbox and its workspace. Push first: what was not pushed is lost. |

Every other tool takes the `sandbox_id` and runs in that sandbox:

* **Repositories:** `clone_repository`, `set_active_repository`, `workspace_status`, `git_status_all`.
* **Files:** `file_read`, `file_write`, `file_edit`, `apply_patch`, `list_dir`, `grep_search`, `glob_files`.
* **Commands:** `bash_exec`, `package_manager`, `run_tests`, `lint`, `typecheck`, `build`; variables with `env_set`, `env_unset`, `env_list`; background processes with `process_start`, `process_list`, `process_output`, `process_wait`, `process_stop`.
* **Git:** `git_status`, `git_diff`, `git_log`, `git_commit`, `git_branch`, `git_checkpoint`, `git_revert`, `git_push`, `git_open_pr`.
* **GitHub:** `github_find_repositories`, `github_read_file`, `github_issues`, `github_pull_request`, `github_actions`, `github_releases`, `github_search`, `github_api`.

A typical session: `sandbox_open`, `clone_repository`, edit and test, `git_commit`, `git_open_pr`, then `sandbox_close`.

* **The sandbox starts on the first call that needs it**, blank: clone what you need. It pauses after the idle period (`sandbox_list` then says `hibernated`) and resumes on the next call with its workspace and variables as they were. After `workspace_release_days` without a call, the workspace is deleted, as for any session ([Sleeping and cleanup](/administer/sandboxes#sleeping-and-cleanup)); the next call's result starts by saying so. A background process keeps the sandbox awake for `process_keepalive_minutes`, until a process tool sees that it ended.
* **Publish with `git_push` or `git_open_pr`.** A plain `git push` is refused inside the sandbox: the platform publishes the branch with your organization's signing and publishing rules. Clones and fetches go through the platform's git gateway; the sandbox holds no GitHub credential.
* **Long commands:** start them with `process_start` and follow them with `process_output` or `process_wait`, rather than holding one call open for many minutes.
* **Results are bounded** like the agent's: a long output is cut, and the full text is saved in the sandbox at a path the result names.

## What is the same, and what is not

A sandbox opened here is a session of yours with the source *MCP*. It appears in your session list, and its workspace browser and terminal work in the cockpit. The tools behave as they do for the platform's agent: your organization's permission mode, hooks, redaction and audit trail apply to every call.

* **Nothing waits for a person.** A call that your organization's permission mode would put in front of a person for approval is refused with the reason, since there is nobody to ask.
* **Only you can act in it.** Nobody else's MCP calls reach your sandbox, and the MCP only acts in sandboxes it opened.
* **One worker at a time.** While the platform's agent runs a turn in the session (if you started one from the cockpit), MCP calls are refused until it ends.
* **The cockpit does not stream the calls.** The session shows the workspace, not a transcript of what your agent did.
* **A member keeps at most `mcp_max_open_sandboxes` sandboxes open** (10 by default, set by an administrator). Your organization's limit on running sandboxes also applies.

A sandbox type's variables are in every command's environment, as they are for the platform's agent, so an agent that reads its environment can see their values. Administrators give a type that carries secrets only to the teams allowed to hold them.
