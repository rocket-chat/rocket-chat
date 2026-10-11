---
title: How sessions work
description: Sandboxes, repositories, supervision, background processes and what survives a restart.
---

# How sessions work

## The sandbox

Every session has a **sandbox**: a container (Docker locally, a pod on Kubernetes) with a persistent `/workspace` volume. The agent runs commands and edits files there; nothing it does touches your machine or other people's sessions.

* **It starts blank.** No repository is checked out. The agent works out whether the task needs code and which repository, then clones it into `/workspace/<name>`. It finds repositories from your request, your organization's knowledge bases and skills, and read-only GitHub lookups (search, README, issues).
* **It is started when needed.** The first tool that needs a sandbox starts it; opening an old session does not.
* **It sleeps when idle** (after 10 to 30 minutes, set by the operator) and wakes on your next message. The workspace is kept: files, installed dependencies and clones survive. **Running processes do not**: a server the agent started ends when the sandbox sleeps (the Processes tab shows it as *lost*). A sandbox with a running process is kept awake for a while (your organization sets how long) as long as you use it.
* **Which image?** Your organization offers one or more sandboxes (the default is a development image with git, GitHub CLI, Python with `uv`, Node and build tools). Some can have extra abilities such as a headless browser the agent uses to check the app it built.

## Repositories and GitHub

The agent clones with your linked GitHub account, or your organization's GitHub App installation when you have not linked one, and tells you plainly when GitHub refuses access instead of hunting for tokens. If a cloned repository contains `.rocket/setup.sh` (or a dev container with `onCreateCommand` / `postCreateCommand`), that setup runs once in the clone, without your GitHub token in its environment. Branches the agent creates follow the prefix you set under *Git & version control*.

Link GitHub under **Settings → Git & version control**. Templates, schedules and GitHub-triggered work can name a repository; it then becomes the first line of the instruction ("Clone it first").

## Following a pull request

Ask the agent to wait for CI or a review ("open the PR and tell me when CI is done") and it follows the pull request: CI results, reviews, comments and the merge arrive in the session on their own, and a trusted event wakes the agent to react. A pull request the agent opens is followed automatically. See [Sessions that follow a pull request](/administer/github#sessions-that-follow-a-pull-request) for what an administrator must enable.

## Supervision

The permission mode decides what waits for you:

| Mode | Behaviour |
| :--- | :--- |
| **Plan** | Read-only. The agent investigates and proposes a plan; nothing changes. |
| **Ask** | Every edit, command and publish (commit, branch, pull request) needs your approval. |
| **Auto-edit** | Edits run freely; commands and publishing need approval. |
| **Auto** | The agent asks when it judges an action risky. |
| **Bypass** | Nothing waits, except what your organization denies. Only if your organization allows it; otherwise it runs as *Ask*. |

Your organization can also set a policy that approves or denies by risk level, and can lock the mode. Reading files, searching, cloning and looking at an image never need approval.

## Background processes

`bash` waits for its command to finish and stops it on a timeout, so servers, watchers and slow builds run as **background processes**: the agent starts them, reads their output, waits for them and stops them. You see them in the **Processes** tab with a log tail, a Stop button and, for a server, an **Open** button that jumps to **Preview**. If one ends while the agent is away, it is told at the start of its next turn. A session may run a limited number at once (the default is 5).

![The Processes tab](../assets/screenshots/processes.png)

## Images

Attach a screenshot, or ask the agent to look at an image it created (`view_image`). Whether it can *see* the image depends on the **model**, not the sandbox: a vision-capable model gets the image; a text-only model is told plainly that it cannot.

## Reliability

* The live connection resumes after a drop without losing or repeating anything, and opening a session mid-turn shows the answer as it is being written.
* A question that was waiting when the server restarted is still waiting; answering it continues the work.
* A turn the server lost mid-flight is flagged and continued automatically once, so it does not simply vanish.

## Limits you may meet

| You see | Why |
| :--- | :--- |
| "Sandbox limit reached" | Your organization caps how many sandboxes run at once; an idle one is put to sleep to make room. |
| "Blocked: ... needs a sandbox with the 'browser' capability" | That sandbox is not set up for browser checks; pick one that is, or ask an administrator. |
| The agent will not run a command | It needs approval in your permission mode, or your organization denies that kind of action. |
| "Budget exceeded" | A spend limit set by your organization or on your personal key was reached. |
