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

**Servers that sign you in (OAuth).** Paste the server's URL: when you leave the field, Rocket Chat asks the server how it signs people in, switches the form to OAuth and tells you whether there is anything to set up (usually nothing). For an organization server, choose who signs in: *each member with their own account*, or *one shared account* that you sign in now. **Add & sign in** saves the server and opens the provider's login in a new tab; when you approve, the tab closes and the settings page shows that you are signed in. Behind it, Rocket Chat reads the server's OAuth metadata, registers itself with the provider (or, where the provider supports it, identifies itself by the public URL of its client metadata), and requests a token for that server only. Only a provider that allows neither needs an OAuth app created by hand: the form says so and shows the callback URL to give it, under *Advanced*.

**Staying signed in.** Tokens are refreshed before they expire, and once more if the server refuses one. When the account no longer works (revoked, expired without a refresh token) the card shows *sign-in required*, and the agent tells you which server needs signing in, with a link, instead of working without it.

**Common servers.** In the registration dialog, *Start from a common server* fills in the name, address, authentication type and a link to the vendor's setup guide for about twenty popular servers (GitHub, Linear, Notion, Atlassian, Slack, Sentry, Stripe, Supabase, PostgreSQL, Brave Search and more). It only prefills the form: you register the server the usual way. For servers that use OAuth there is nothing to fill in: Rocket Chat discovers the provider's endpoints and registers itself, then you sign in. Only if a provider does not allow that, create an OAuth app there (the dialog shows the callback URL under *Advanced*) and enter its client ID and secret.

**Editing a server.** Every server has a pencil on its card: administrators edit organization servers, and you edit your own. The form opens with what is stored; secrets (an API key, an OAuth client secret, a secret header) are never shown, so leave one alone to keep it or type to replace it. The transport and scope cannot change. If you point a remote server at another address or another OAuth client, the connected account is disconnected (members must connect again) and its secrets must be typed again, so a credential is never sent to a host that did not receive it from you. *Test* uses the stored secrets too.

**Command servers.** A *Command* server is a program such as `npx -y @modelcontextprotocol/server-filesystem /workspace`, `uvx mcp-grafana` or a downloaded binary. Only administrators add them (members see the option with a note to ask one); they run on the platform's dedicated **MCP runner**, never in an agent's sandbox and never next to the API, so a command server's credentials are not visible to the agent's code. A server starts when a turn first uses it and stops when that turn ends: three people using it at once means three short-lived processes, each with its own environment, and nothing keeps running while it is unused.

* **Environment variables** are the server's configuration (a base URL, an API key). Names use letters, digits and underscores; a *Secret* value is stored encrypted and never shown again.
* **Members can set their own.** Tick it on a variable (an API key, say) and each member can run the server with their own value from the server's card (*Your own values*): your sessions use yours, everyone else's keep the organization's, and no one sees anyone's value. Leave the organization's value empty to require it: members who have not set theirs do not get the server at all, rather than borrowing someone else's credentials.
* **Install script** (optional) for a program that is not an `npx`/`uvx` package, for example `curl -fsSL https://github.com/acme/tool/releases/latest/download/tool_linux_amd64.tar.gz | tar xz`. It runs once on the runner (again only if you change it), without the server's variables, and the program is then on the command's `PATH`, wherever the archive put it: at the top, in `bin/`, or in the folder it unpacks to (`tool_linux_amd64/bin/`). Pick the build with `uname -m` if the runner may be amd64 or arm64.
* **Editing.** Administrators change a command server with the pencil on its card: the command, install script, variables, which variables members may override, and the agent guidance. A *Secret* is never shown, so its field says *Unchanged*: leave it alone to keep the stored value (including the default of a variable members may override), type to replace it, or press *Clear* to remove it. A variable you add needs a value (or *Members can set their own*). Members' own values are kept; a value for a variable that is no longer overridable is ignored.
* **Testing.** *Test & validate connection* starts the command on the runner and lists its tools, including any install script, and shows the program's own error when it fails.
* Packages are cached on the runner's volume, so they are not downloaded again after a restart.
* If the installation has no runner (or it is not answering), command servers are disabled in the form and flagged on existing servers; remote servers keep working. See [Kubernetes](/install/kubernetes) and [Docker Compose](/install/docker-compose) for enabling it.

The agent can list the tools it has with `search_tools` (optionally with a keyword). If it calls a name that does not exist, it is told which real tools are closest and to look the tool up, rather than retrying.

## Knowledge bases

Upload documents (architecture notes, runbooks, API docs) and the agent searches them when relevant. This is also the best place to say where things are: "the billing service is `acme/billing-api`" lets the agent pick the right repository without asking.

## Agents (personas)

An agent is a system prompt, a model and an optional list of allowed tools, skills, knowledge bases, and resource limits. The platform ships with three built-in agents:

* **General Software Engineer (`persona-general`)**: versatile full-stack autonomous engineer with access to all runtime tools.
* **Backend Developer (`persona-backend`)**: optimized for complex, long-running backend engineering tasks (APIs, microservices, databases, durable waits, and subagent worktrees) with `on_demand` tool loading, `medium` reasoning effort, and a 100-step turn budget.
* **Security & Penetration Auditor (`persona-security`)**: focused read-only code auditor checking for OWASP vulnerabilities, RLS leaks, and secret exposure.

Administrators can edit the built-in agents for the whole organization (prompt, model, tools, reasoning effort, temperature, step limit, and sandbox sizing) and use **Reset to default** to restore the shipped version; members who want a variation save a copy. Define narrower ones for specific jobs (a reviewer limited to read-only tools, a database migration specialist). A persona can also name a sandbox image and placement profile, subject to your organization's catalogue.

### Reasoning effort and temperature

Under **Settings → Agents**, you can configure fine-grained model execution parameters for each persona:

* **Reasoning effort**: for reasoning models (Anthropic Claude 3.7 Sonnet with thinking, OpenAI o1/o3-mini, Gemini 2.5 Pro with thinking). Choose `low` for fast, cost-conscious responses, `medium` for balanced engineering, or `high` for deep architectural refactors and complex migrations.
* **Sampling temperature**: overrides the model's default sampling temperature (`0.0` is deterministic; `1.0+` is creative). Leave empty to inherit the model default. (Note: reasoning models typically enforce temperature `1.0` or ignore this parameter).

### Max steps per turn

Each agent can set **Max steps per turn**, from 1 up to the organization's ceiling (configured under **Settings → Organization → Turn limits**, up to 500); leave it empty to use the organization's limit. For complex missions requiring hundreds of steps across multiple files and tests, Rocket Chat automatically schedules **Goal Continuations** when a turn hits its step cap while the `.rocket/PLAN.md` checklist has pending items.

### Which tools every step carries

Every tool definition an agent can use is sent to the model at every step. An agent connected to several MCP servers can pay tens of thousands of tokens per step for tools it rarely uses, and choose worse among hundreds of them. Each allowed tool can therefore be:

* **always loaded:** its definition is sent at every step;
* **loaded on demand:** its name is listed, grouped by server, and the agent loads its definition with `load_tools` when it needs it (calling it by name loads it too). It then stays loaded for the rest of the session;
* **not allowed:** unchecked.

The agent's **Tool loading** chooses how:

| Mode | What every step carries |
| :--- | :--- |
| **Automatic** | Everything, until the agent has more tools than the organization's threshold; then its MCP servers' tools are loaded on demand, except pinned ones. |
| **On demand** | The platform's own tools (built-in and Slack) and the pinned tools; MCP tools are loaded on demand. Once you pin or list tools, only those are always loaded. |
| **Load every allowed tool** | Everything, as before. |
| **Organization default** | The organization's choice: `general.tool_loading` (`auto` by default) and `general.tool_loading_threshold` (60), in **Settings > Organization**. |

Pin a tool (the pin on an allowed tool) or list patterns under **Always loaded** (`mcp__signoz*__*search*`) to keep it loaded.

An agent's prompt says who it is and how it should work. The platform adds the facts about where it works, whatever the prompt says: the sandbox starts blank and how to find and clone a repository; the workspace (the active repository, a fresh shell per command, clamped output); how to edit and verify; and the git and GitHub rules. Those rules cover no credentials in the sandbox, never committing to the default branch, publishing with `git_push` or `git_open_pr`, how the organization's signing mode applies, and the `github_*` tools. The platform also adds your organization's skills and the active repository's own `AGENTS.md` (or similar). The built-in agents use whatever model you or your organization choose, from any provider.

Each agent can set a **Sandbox**: the sandbox type its sessions start in when you pick none in the composer (a sandbox you pick still wins); empty uses the organization's default.

## Memory

The agent can remember durable facts about you and your projects ("we use pnpm", "tests run with `make check`") and recall them later. See and delete them in the **Memory inspector**; administrators can turn memory off or restrict what it may store. Secrets are never stored.
