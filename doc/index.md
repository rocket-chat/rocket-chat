---
title: Rocket Chat
description: "Agents that engineer for your team, in the browser and in Slack, each in its own ready-made sandbox, on your infrastructure."
---

# Rocket Chat

Rocket Chat is a platform of **software-engineering agents** your team talks to in the browser or in Slack. Every conversation gets its own **sandbox**, a disposable machine that already has the tools the work needs, where the agent reads code, runs commands and tests, edits files, starts the apps it builds and publishes signed commits and pull requests, while you watch, steer and approve. You run it on your own Docker host or Kubernetes cluster, with your own models.

![Rocket Chat working on a request](assets/demo.gif)

## Your first task in three steps

<Steps>
  <Step title="Start it">
    With Docker and one model key: `docker compose up -d` (the [Quickstart](/quickstart) has the two lines before it).
  </Step>
  <Step title="Sign in">
    Open `http://localhost:3000` and use the developer login. You are the administrator of a fresh organization.
  </Step>
  <Step title="Ask for something">
    *Clone acme/billing-api, find why `test_refund_rounding` fails and fix it.* Watch it work, answer its questions, review the diff.
  </Step>
</Steps>

## Where to go next

<CardGroup cols={2}>
  <Card title="Try it" icon="rocket" href="/quickstart">
    Your first finished task in about five minutes, on your own machine.
  </Card>
  <Card title="Use it" icon="message" href="/use/index">
    Ask for work, review changes, steer a long task, work from Slack.
  </Card>
  <Card title="Set it up for a team" icon="users" href="/administer/index">
    Sign-in, members and roles, models and keys, GitHub, Slack, sandboxes, spend.
  </Card>
  <Card title="Install it for real" icon="server" href="/install/index">
    One server with Docker Compose, or Kubernetes with the Helm chart.
  </Card>
  <Card title="Keep it running" icon="gauge" href="/operate/index">
    Upgrades, backups, monitoring and troubleshooting.
  </Card>
  <Card title="Build on it" icon="code" href="/api/index">
    The REST API and the live WebSocket protocol.
  </Card>
</CardGroup>

Not sure it fits? Read [why it exists](/why-rocket-chat) and [how it compares](/compare) with Claude Code, Codex, Cursor, Devin and others.

## Key ideas in one minute

| Idea | What it means for you |
| :--- | :--- |
| **Session** | One conversation with an agent, in the browser or a Slack thread. It keeps its history, its files and its sandbox, and you can share it. [Your first session](/use/index) |
| **Sandbox** | The disposable machine a session works in, built from an image your organization controls. It sleeps when idle and never holds your GitHub credentials. [Sandboxes](/administer/sandboxes) |
| **Agent** | Instructions, a model, tools, skills and limits. Use the built-in ones or shape your own. [Agents and extensions](/use/extend) |
| **Permission mode** | How closely you supervise: read-only *Plan*, *Ask* before every edit and command, or *Auto*. [Supervision](/use/sessions#supervision) |
| **Questions and approvals** | When the agent needs you, it shows a card with options instead of guessing, in the browser or in Slack. |

## What you get

* **A team of agents, shaped to your work.** Ship with built-in agents (a general engineer, a backend specialist, a security auditor), edit them, or define your own: each has its own instructions, model, tools, skills, knowledge, step budget and sandbox. Agents can delegate to parallel subagents.
* **A sandbox that is ready when the agent arrives.** Each conversation starts in its own isolated environment, built from an image you control, with your toolchains installed, your variables set and your repositories prepared. It sleeps when idle, scales with your cluster, and is released when the work is done. [Build your agents' sandbox](/administer/custom-sandboxes).
* **You stay in control.** Choose how closely an agent is supervised, from read-only planning to approving every edit and command. Questions and approvals appear as cards you can answer in the browser or in Slack.
* **See the work.** Changes as diffs, the workspace as files, running servers in a Preview tab, background processes with their logs, an interactive shell, and a trace of every turn.
* **Slack, for real.** Several bots, each mapped to an agent. Mention one in a thread and watch it work, answer its questions with buttons, work privately or in the open, read threads, channels and user groups, and move to the browser at any point.
* **GitHub, end to end.** Clone with your own identity or your organization's GitHub App, read pull requests, review threads and CI logs, follow a pull request through a long task, and publish **signed commits** (GitHub's Verified badge, or a member's own key that never enters the sandbox).
* **Your internal tools, safely.** Connect MCP servers, remote or command-line (stdio). Command servers run on a dedicated, locked-down runner, away from the API and from the agent's sandbox, with install scripts and per-member credentials.
* **Your models, your rules.** Bring your own provider keys or a self-hosted model; organization policy controls models, tools, spend, data handling and sandboxes.

Developers extending Rocket Chat itself should read the [developer documentation](https://github.com/rocket-chat/rocket-chat/tree/main/dev) in the repository; this site is for people who install, use and run it.
