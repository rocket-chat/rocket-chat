---
title: Skills, tools, knowledge and agents
description: Teach the agent your conventions and connect it to your systems.
---

# Skills, tools, knowledge and agents

Everything here lives under **Settings**. Items can be personal (only you) or, for administrators, shared with the organization.

## Skills

A skill is a named instruction you invoke with a slash: type `/review src/` and the composer offers your skills; the agent receives the skill's text with your arguments filled in (`$ARGUMENTS`). A skill can also be a short Python function the agent runs in its sandbox. You can import skills from a repository.

The agent sees the name and summary of every skill it may use and loads one with `execute_skill` when a task matches, so write a clear `description` that says when the skill applies. A persona that lists skills only sees those.

## MCP servers

Connect tools from other systems through the [Model Context Protocol](https://modelcontextprotocol.io): an issue tracker, a database, an internal API. Add a server (remote over HTTP/SSE, or a command that runs inside the sandbox), give it credentials (stored encrypted, never shown again) and its tools join the agent's. An organization connector can require each member to authorize their own account. Descriptions you add under *guidance* tell the agent when to use a tool.

**Common servers.** In the registration dialog, *Start from a common server* fills in the name, address, authentication type and a link to the vendor's setup guide for about twenty popular servers (GitHub, Linear, Notion, Atlassian, Slack, Sentry, Stripe, Supabase, PostgreSQL, Brave Search and more). It only prefills the form: you register the server the usual way. For servers that use OAuth, leave the OAuth fields empty: Rocket Chat discovers the provider's endpoints and registers itself, then you sign in. Only if a provider does not allow that, create an OAuth app there (the dialog shows the redirect URL) and enter its client ID and secret.

**Command servers.** A *Command* server is a program such as `npx -y @modelcontextprotocol/server-filesystem /workspace`, `uvx mcp-grafana` or a downloaded binary. Only administrators add them (members see the option with a note to ask one); they run on the platform's dedicated **MCP runner**, never in an agent's sandbox and never next to the API, so a command server's credentials are not visible to the agent's code. A server starts when a turn first uses it and stops when that turn ends: three people using it at once means three short-lived processes, each with its own environment, and nothing keeps running while it is unused.

* **Environment variables** are the server's configuration (a base URL, an API key). Names use letters, digits and underscores; a *Secret* value is stored encrypted and never shown again.
* **Members can set their own.** Tick it on a variable (an API key, say) and each member can run the server with their own value from the server's card (*Your own values*): your sessions use yours, everyone else's keep the organization's, and no one sees anyone's value. Leave the organization's value empty to require it: members who have not set theirs do not get the server at all, rather than borrowing someone else's credentials.
* **Install script** (optional) for a program that is not an `npx`/`uvx` package, for example `curl -fsSL https://github.com/acme/tool/releases/latest/download/tool_linux_amd64.tar.gz | tar xz`. It runs once on the runner (again only if you change it), without the server's variables, and the program is then on the command's `PATH`, wherever the archive put it: at the top, in `bin/`, or in the folder it unpacks to (`tool_linux_amd64/bin/`). Pick the build with `uname -m` if the runner may be amd64 or arm64.
* **Testing.** *Test & validate connection* starts the command on the runner and lists its tools, including any install script, and shows the program's own error when it fails.
* Packages are cached on the runner's volume, so they are not downloaded again after a restart.
* If the installation has no runner (or it is not answering), command servers are disabled in the form and flagged on existing servers; remote servers keep working. See [Kubernetes](/install/kubernetes) and [Docker Compose](/install/docker-compose) for enabling it.

The agent can list the tools it has with `search_tools` (optionally with a keyword). If it calls a name that does not exist, it is told which real tools are closest and to look the tool up, rather than retrying.

## Knowledge bases

Upload documents (architecture notes, runbooks, API docs) and the agent searches them when relevant. This is also the best place to say where things are: "the billing service is `acme/billing-api`" lets the agent pick the right repository without asking.

## Agents (personas)

An agent is a system prompt, a model and an optional list of allowed tools, skills and knowledge bases. The default, **General Software Engineer**, has every tool. Administrators can edit the built-in agents for the whole organization (prompt, model, tools, step limit) and use **Reset to default** to restore the shipped version; members who want a variation save a copy. Define narrower ones for specific jobs (a reviewer limited to read-only tools, a security auditor). A persona can also name a sandbox image, subject to your organization's catalogue.

Each agent can set **Max steps per turn**, from 1 up to the organization's limit (Settings > Models & routing); leave it empty to use the organization's. Lower it for a quick helper, or raise it, within the limit, for a long investigation.

## Memory

The agent can remember durable facts about you and your projects ("we use pnpm", "tests run with `make check`") and recall them later. See and delete them in the **Memory inspector**; administrators can turn memory off or restrict what it may store. Secrets are never stored.
