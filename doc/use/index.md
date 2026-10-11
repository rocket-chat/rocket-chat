---
title: Your first session
description: Ask for work, watch it happen, review the result.
---

# Your first session

Open the cockpit (for a local install, `http://localhost:3000`) and sign in. You land on an empty conversation.

![A new conversation](../assets/screenshots/new-conversation.png)

## 1. Ask for something

Type what you want in the composer and press **Enter**. Be specific about the outcome, and name the repository if the work needs one:

```text
Clone acme/billing-api, find why test_refund_rounding fails and fix it. Run the tests when you are done.
```

You do not need to pick a repository first: **a session always starts with a blank sandbox**, and the agent clones what the task needs (with your linked GitHub account or your organization's GitHub App). Starter prompts and your saved [templates](/use/automation) appear on the empty conversation.

Under the composer you can set, before you start:

| Control | What it does |
| :--- | :--- |
| **Attach** | Add a log, a screenshot or a CSV (you can also drag or paste). The agent reads it. |
| **Permission mode** | How closely the agent is supervised: *Plan* (read-only), *Ask* (approve every edit, command and publish), *Auto-edit*, *Auto*, or *Default* (your organization's choice). |
| **Sandbox** | Which sandbox the session starts in, when your organization offers more than one. A session keeps the sandbox it started with. |
| **Agent and model** | A persona and the model it uses. |

## 2. Watch it work

![A conversation in progress](../assets/screenshots/conversation.png)

The conversation shows what the agent did as one line ("Edited 2 files, ran 3 commands") that you can open step by step: a diff, a command with its output, a subagent's findings. While it works you can press **Stop**, or type the next instruction, which waits its turn.

When the agent needs you it shows a card instead of guessing:

* an **approval** for an action your permission mode puts behind a human (run this command, edit this file, push this branch): **Approve** or **Reject**;
* a **question** with options: pick one or type an answer. A question blocks the turn until you answer, so the agent only asks when it cannot continue safely.

## 3. Look around the workspace

Open the right-hand panel for the session's inspector:

| Tab | Shows |
| :--- | :--- |
| **Changes** | The agent's proposed edits as diffs; apply a single file or all of them. |
| **Files** | The sandbox's files; open, read and edit them yourself. |
| **Processes** | Servers, watchers and long builds the agent started in the background: state, log tail, **Stop**, and an **Open** button for a server's port. |
| **Preview** | An app running in the sandbox, in a sandboxed frame. Open a port yourself or from the Processes tab. |
| **Shell** | An interactive terminal in the same sandbox (when your organization allows it). |

The header has two more: the **Turn Inspector** (every turn's tools, files, approvals and tokens) and the **Memory inspector** (what the agent remembered about you and your projects, which you can forget).

## 4. Keep going, or undo

The ghost text in the composer is the agent's guess at your next message: press **Tab** to accept it, **Enter** to send. Before a turn first changes your workspace the platform takes a **checkpoint**; the checkpoints panel lets you rewind the workspace to an earlier point after a confirmation.

Sessions are private to you unless your administrator makes new sessions visible to the organization by default; everyone with access sees the same live conversation, and messages show who wrote them. Pin or archive sessions in the sidebar.

## Next

* [How sessions work](/use/sessions): sandboxes, repositories, supervision, hibernation.
* [Slack](/use/slack): the same agent in your channels.
* [Templates, schedules and GitHub automation](/use/automation).
* [Skills, tools, knowledge and agents](/use/extend).
* [Your personal settings](/use/personal-settings).
