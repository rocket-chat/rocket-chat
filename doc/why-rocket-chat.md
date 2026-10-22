---
title: Why Rocket Chat
description: What problem it solves, what it is best at, and when something else is the better tool.
---

# Why Rocket Chat

## The gap

Coding agents have become good enough to hand real work to. What changed less is how they reach a **team**. Today they mostly come in three shapes, and each leaves something for you to build yourself:

| Shape | Examples | What it is great at | What a team is left to solve |
| :--- | :--- | :--- | :--- |
| **A tool on one person's machine** | Terminal and editor agents such as Claude Code, Codex CLI, OpenCode, Cursor | Speed and flow for one developer, close to the code and the editor | It runs as that person, with their credentials. Sharing a session, one audit trail, spend limits, "ask it from Slack" and a consistent environment are yours to assemble. |
| **A vendor's hosted agent** | Devin, Cursor's cloud agents, GitHub Copilot's coding agent, the cloud modes of Claude Code and Codex | Turnkey: no servers, a managed environment | Your code, prompts and credentials run on the vendor's infrastructure, with the vendor's models and billing. |
| **An open-source agent platform** | OpenHands | You can host it | The team features (multiple users and roles, Slack and Jira, audit) can sit in a separate commercial tier. |

Rocket Chat exists for the team that wants the fourth option: **a platform of agents your whole team shares, that you run yourself, with everything a team needs in the open-source edition.**

## What it is

A server you run on a Docker host or a Kubernetes cluster. People talk to your agents in a web **cockpit** or in **Slack**. Every conversation gets its own **sandbox**, a disposable machine with your tools already installed, where the agent reads, edits, builds, tests and runs things, while you watch and decide how much supervision it needs.

It is not a chat window in front of a model. The agent has a workspace that persists, processes that keep running, a checklist it keeps up to date, memory, skills, subagents, and a durable record of every turn.

## Where it is strongest

### 1. A sandbox of its own, ready when the agent arrives

This is the heart of it. Each conversation gets its **own isolated environment**, created when the agent first needs it, and you decide what is in it.

* **Customized.** Build an image with your languages, compilers and CLIs; set environment variables and encrypted secrets; have each repository prepared (dependencies installed, code generated) right after it is cloned. The agent starts with its tools available instead of spending its first twenty steps installing them. [Build your agents' sandbox](/administer/custom-sandboxes).
* **Powerful.** Size CPU, memory and disk per sandbox type or per agent; pin heavy work to a node pool with placement profiles; give every sandbox a stronger isolation boundary with a runtime class such as gVisor or Kata Containers. Parallel subagents each work in their own git worktree.
* **Ephemeral.** It starts with the first command that needs it, sleeps when idle, and releases its volume when the work is long over, so nothing piles up.
* **Autoscalable.** On Kubernetes a sandbox is a pod with its own volume, so capacity follows your cluster. Pods are marked so Cluster Autoscaler and Karpenter never evict one that is running a command or keeping a dev server awake, and the first call waits for a new node when one has to be added. The control plane scales with the Helm chart's autoscaling too.
* **Observable.** The agent's files, changes, running servers (with a live **Preview** of a web app), logs and an interactive shell are all one click away.

### 2. A team of agents, shaped to your work

Rocket Chat ships with a general software engineer, a backend specialist and a read-only security auditor, and each can be edited for your organization. Define your own for a narrow job: a reviewer limited to read-only tools, a migration specialist, a release manager. An agent is its own instructions, model, allowed tools, skills, knowledge bases, reasoning effort, step budget and sandbox. Agents hand focused work to parallel subagents. Members pick the agent for a conversation, and each Slack bot can be mapped to one. [Agents](/use/extend#agents-personas).

### 3. A team's platform, not a personal tool

Sign in through your identity provider ([SSO](/administer/sso)), assign roles, share sessions so colleagues can read along and send messages, set budgets per person and per organization, and keep an [audit trail](/administer/security) with redaction and retention you control. All of it ships in the one open-source edition: there is no enterprise tier to buy to turn it on.

### 4. You run it

On your machine, in your cluster, behind your network policies. The agent's code, commands and files stay on infrastructure you operate. Prompts go only to the model you configure, and with a self-hosted model they go nowhere else ([air-gapped installs](/install/air-gapped)).

### 5. Slack as a first-class interface

Not a notification bot: your agents, in your channels.

* **Several bots**, each mapped to an agent or a router (`@RocketDev`, `@RocketOps`, `@RocketTriage`), each with its own slash command.
* **Thread mode** shows the step it is on, then a one-line summary and the answer, with a link to open the session in the browser; **private mode** keeps the work to you and lets you share the result when you choose.
* **Questions and approvals are buttons** in the thread, answerable only by the person who asked, and they survive a restart.
* **Slack-aware.** The agent can read the thread or channel it was asked in, find people and user groups, link a message and, under your policy, post, message or react. Logs, screenshots and CSVs you attach are read. Long answers and diffs are attached as files.
* Every thread is also a session you can open in the browser, and the other way round.

[Slack](/use/slack) · [Connect Slack](/administer/slack)

### 6. GitHub, end to end, with signed commits

* **Two layers of identity.** Your organization's GitHub App for the platform's own work, and each member's linked account so the agent acts as them where that matters. The sandbox holds neither credential: it clones through a read-only gateway.
* **Publishing is a platform step.** Commits leave through `git_push` and `git_open_pr`, which apply your signing policy: **`github_app`** re-creates the commits through GitHub's API so they earn GitHub's **Verified** badge; **`gpg_key`** signs them with a member's own key, which is stored encrypted and never enters a sandbox; **`none`** pushes as is. `Co-authored-by` trailers are kept.
* **Pull requests, reviews and CI** are tools, not copy-paste: read diffs and review threads, reply and resolve, read a failed job's log, re-run a check, and follow a pull request through a long task so the agent wakes when CI finishes or a review arrives.
* **Webhooks** can start work: label an issue and an agent reproduces it, fixes it on a branch and opens the pull request.

[GitHub](/administer/github)

### 7. Your internal tools, safely: MCP, including command-line servers

Connect the systems your agents need through the Model Context Protocol: an issue tracker, a database, an internal API. Remote servers work over HTTP/SSE with OAuth; about twenty popular ones are one click to start.

**Command-line (stdio) servers are a strength, not an afterthought.** A command server (`npx`, `uvx`, or a binary you install) is dangerous to run anywhere an agent can reach, so Rocket Chat runs it on a **dedicated MCP runner**: a separate, locked-down pod, never next to the API (which holds the database credentials) and never inside the agent's sandbox (where its code could read the server's credentials). Administrators can give a server an **install script** for programs that are not packages, set its environment, and let each member supply **their own** API key so no one borrows someone else's. A server starts when a turn needs it and stops when the turn ends. [MCP servers](/use/extend#mcp-servers).

### 8. You decide how much to trust it

From *Read-only* (it can only investigate and plan) through *Ask first* (every edit, command and publish waits for you) to *Auto*. Questions and approvals are cards in the browser or in Slack; they wait as long as you need, hours included, without holding up anyone else. Before a turn first changes your workspace the platform takes a checkpoint, so you can rewind. Organization policy can approve or deny by risk and lock the mode.

### 9. Any model, no lock-in

Bring your own provider keys (OpenRouter, Anthropic, OpenAI, Gemini, DeepSeek), let members bring theirs, or point it at a self-hosted model. Route by task, set fallbacks, and cap spend. You pay your provider directly: there is no per-seat price for the platform.

### 10. Safe by construction

Provider keys and secrets are encrypted at rest and never shown to the model or the logs. Tenants are isolated in the database itself with row-level security, not only in application code. Sandboxes run as non-root with restricted privileges, with egress you control. See [Security & compliance](/administer/security).

### 11. Made for work that takes hours

A turn can run for a long time and survive a restart. The agent can wait durably for CI or a review and be woken when it arrives, steer mid-run when you send a message, keep a long conversation coherent through compaction that is checked rather than trusted, and run subagents in parallel in separate worktrees.

### 12. Built in, and open to extension

A large built-in toolset: files, search and patches, a shell, tests, lint and builds, git and GitHub, the web, a browser for checking the apps it builds, background processes, memory, a checklist, durable waits and subagents. Add [skills](/use/extend), MCP servers and knowledge bases, and build on the [REST and WebSocket API](/api/index).

## Who it is for

* An **engineering team or platform group** that wants agents for everyone, with governance, on its own infrastructure.
* **Companies with code-privacy or data-residency requirements** that rule out sending repositories to a vendor's cloud.
* Teams that **live in Slack** and want the agents where the conversation already happens.
* People running agents on **long, unattended work**: migrations, dependency upgrades, reviews, scheduled maintenance.

## When something else is the better tool

Being clear about this is part of the pitch.

* **You want inline completions and agent help inside your editor.** Rocket Chat is a cockpit and a Slack bot, not an IDE plugin. Cursor, Copilot and the editor modes of the terminal agents are built for that.
* **You are one developer working on your own machine.** A local terminal agent is lighter: nothing to host, and it sees your working tree directly. Rocket Chat is worth it when more than one person needs it, or when you want its sandbox and governance.
* **You do not want to operate anything.** Rocket Chat needs a database and a container host, and someone to keep them healthy. A hosted agent trades control for convenience.
* **You need a certified managed service.** Rocket Chat is software you run; compliance of your deployment is yours to establish.

One security note belongs here, not in a footnote: the Docker sandbox driver needs the host's Docker socket, which is equivalent to root on that machine for whoever controls the backend. Use Docker Compose for a single trusted team, and the Kubernetes driver for multi-user or internet-facing installs.

## Next

* [Quickstart](/quickstart): try it in five minutes.
* [How it compares](/compare) with Claude Code, Codex, OpenCode, Cursor, GitHub Copilot's agent, Devin and OpenHands.
* [Install](/install/index): pick how to run it.
