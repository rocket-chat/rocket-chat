---
title: Rocket Chat
description: "Agents that engineer for your team, in the browser and in Slack, each in its own ready-made sandbox, on your infrastructure."
---

# Rocket Chat

Rocket Chat is a platform of **software-engineering agents** your team talks to in the browser or in Slack. Every conversation gets its own **sandbox**, a disposable machine that already has the tools the work needs, where the agent reads code, runs commands and tests, edits files, starts the apps it builds and publishes signed commits and pull requests, while you watch, steer and approve. You run it on your own Docker host or Kubernetes cluster, with your own models.

![Rocket Chat working on a request](assets/demo.gif)

## Start here

| I want to... | Go to |
| :--- | :--- |
| **Try it** in five minutes | [Quickstart](/quickstart) |
| **Know why it exists**, and when to pick something else | [Why Rocket Chat](/why-rocket-chat) and [How it compares](/compare) |
| **Install it** on a laptop, a server or Kubernetes | [Install](/install/index) |
| **Use it**: ask for work, review changes, work from Slack | [Use Rocket Chat](/use/index) |
| **Set it up for my team**: sign-in, models, GitHub, Slack, sandboxes, policies | [Administer](/administer/index) |
| **Keep it running**: upgrades, backups, monitoring, incidents | [Operate](/operate/index) |
| **Build on it**: REST API and the live WebSocket protocol | [API](/api/index) |

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
