---
title: Skills, tools, knowledge and agents
description: Teach the agent your conventions and connect it to your systems.
---

# Skills, tools, knowledge and agents

Everything here lives under **Settings**. Items can be personal (only you) or, for administrators, shared with the organization.

## Skills

A skill is a named instruction you invoke with a slash: type `/review src/` and the composer offers your skills; the agent receives the skill's text with your arguments filled in (`$ARGUMENTS`). A skill can also be a short Python function the agent runs in its sandbox. You can import skills from a repository.

## MCP servers

Connect tools from other systems through the [Model Context Protocol](https://modelcontextprotocol.io): an issue tracker, a database, an internal API. Add a server (remote over HTTP/SSE, or a command that runs inside the sandbox), give it credentials (stored encrypted, never shown again) and its tools join the agent's. An organization connector can require each member to authorize their own account. Descriptions you add under *guidance* tell the agent when to use a tool.

**Common servers.** In the registration dialog, *Start from a common server* fills in the name, address, authentication type and a link to the vendor's setup guide for about twenty popular servers (GitHub, Linear, Notion, Atlassian, Slack, Sentry, Stripe, Supabase, PostgreSQL, Brave Search and more). It only prefills the form: you register the server the usual way. Servers that use OAuth need an OAuth app registered with the vendor (the dialog shows the redirect URL to give it) and its client ID and secret.

**Command servers (administrators).** A *Command* server is a program such as `npx -y @modelcontextprotocol/server-filesystem /workspace` or `uvx mcp-server-fetch`. It starts inside the session's sandbox, so it needs the program (`npx`, `uvx`, ...) the sandbox image provides, and its tools appear once the sandbox is running. It cannot be tested from the settings page. Set the environment variables it reads under *Environment variables*; names use letters, digits and underscores, and a value marked secret is stored encrypted. Packages are downloaded in the sandbox, so they are fetched again when its pod is recreated.

## Knowledge bases

Upload documents (architecture notes, runbooks, API docs) and the agent searches them when relevant. This is also the best place to say where things are: "the billing service is `acme/billing-api`" lets the agent pick the right repository without asking.

## Agents (personas)

An agent is a system prompt, a model and an optional list of allowed tools, skills and knowledge bases. The default, **General Software Engineer**, has every tool. Define narrower ones for specific jobs (a reviewer limited to read-only tools, a security auditor). A persona can also name a sandbox image, subject to your organization's catalogue.

## Memory

The agent can remember durable facts about you and your projects ("we use pnpm", "tests run with `make check`") and recall them later. See and delete them in the **Memory inspector**; administrators can turn memory off or restrict what it may store. Secrets are never stored.
