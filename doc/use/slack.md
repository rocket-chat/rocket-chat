---
title: Slack
description: "Work with Rocket Chat from Slack: mentions, direct messages, slash commands and streamed replies."
---

# Slack

If your administrator has [connected Slack](/administer/slack), you can use the same agent from Slack. Every Slack thread is also a [session](/use/sessions) in the web app, so work can move between the two.

## What you see

**Response modes: Thread vs Private session.** Your administrator can configure bots and routes to respond in one of two modes:
- **Thread mode (public):** while Slack's status line shows the bot is working, its reply in the thread lists each step as it happens (a collapsible list of tool calls), then the answer is written into the same message, ending with **Open in Rocket Chat** and what the turn did ("Done in 41s · Edited 2 files · Ran 3 commands"). A turn that only answers posts the answer directly. To stop a turn, use the bot's slash command: `/<command> stop`.
- **Private mode:** When invoked, the bot marks your message with an acknowledgement reaction (default :white_check_mark:) and sends an ephemeral message in the thread visible *only to you*. It includes an **Open session** button linking to your private cockpit session. The work happens privately; when finished, an ephemeral summary is posted to you in the thread alongside a **Share in thread** button that allows you to post the final answer into the public thread when you choose. Follow-up messages you type in the thread continue your private session.

**Questions and approvals are buttons in the thread.** When the agent needs a decision, or wants to run something your organization requires [approval](/administer/security#b-per-org-approval-policy) for, it posts buttons. In thread mode they are posted to the thread; in private mode they are posted as ephemeral buttons visible only to you. Only the person who started the request can answer. This keeps working after a restart: a question that was waiting when the server restarted is picked up again from the stored question, and the work continues in the same thread.

**Long output is attached, not split.** An answer longer than a few thousand characters is summarised in the thread and attached in full as `answer.md`. A large change is listed in the thread and attached as `changes.diff`. Screenshots the agent takes are uploaded as images.

**Files you attach are read.** Logs, screenshots and CSVs attached to a message that mentions Rocket are downloaded into the sandbox and shown to the model.

## What the agent can do in Slack

Besides your workspace tools, the agent has Slack's own:

| Tool | What it does |
| :--- | :--- |
| `slack_read_thread`, `slack_read_channel_history` | Read the thread or channel it was asked in, or another thread or channel that is public or allowlisted. |
| `slack_list_conversations`, `slack_list_channel_members` | Find channels, and who is in one. |
| `slack_resolve_user`, `slack_get_user` | Find someone by name, handle or e-mail; see their title, timezone, status and user groups. Their e-mail is never shown. |
| `slack_list_user_groups`, `slack_get_user_group_members` | List user groups (`@backend`, `@oncall`…) and their members, and how to mention a group (`<!subteam^ID>`). |
| `slack_get_permalink` | Link to a message. |
| `slack_send_message`, `slack_send_dm`, `slack_add_reaction` | Post, message someone, react. These follow your organization's Slack policy and permission mode. |

Reading never needs approval, even in *Plan* mode. A private channel or another person's direct messages are only readable from inside that conversation.

## Ways to start

Your organization may connect multiple bots (e.g. `@RocketDev`, `@RocketOps`, `@RocketTriage`), each mapped to a distinct agent persona or message router. Mentioning a bot routes your request directly to it.

| You do | What happens |
| :--- | :--- |
| `@<bot> <task>` in a channel | Answers in that message's thread (or privately, if configured). Follow-ups in the thread continue the same conversation. |
| Message the bot directly | A private conversation under your own Rocket Chat account (once linked). |
| `/<command> new <task>` | Posts "you asked Rocket: …" and works in a new thread. Each bot has its own command, named after it (`/triage` for `@triage`); `help` lists them. |
| `/<command> status` | Lists your recent Slack threads and which are running (only you see it). |
| `/<command> stop` | Stops what you have running with that bot in this channel. |
| **Ask Rocket** in a message's ⋮ menu | Works on that message, in its thread. |
| Open the app's **Home** tab | Your link status, recent threads and these tips. |

---

## Link your account

Without a link, your direct messages belong to a **guest** member made for your Slack account (it cannot sign in, has no credentials of its own, and an administrator can see its sessions), and a channel conversation belongs to the workspace's guest (no one's personal credentials are ever used in a shared thread). Linking connects you to your own Rocket Chat account for direct messages: your GitHub access, your model keys, your budget. When you link, the sessions your guest started become yours.

1. In Slack run the bot's command with `pair` (for example `/rocky pair`). Rocket replies privately with an 8-character code, valid for 10 minutes and usable once.
2. In Rocket Chat, signed in, open **Settings → Slack** (see [your settings](/use/personal-settings#slack)) and enter the code.

Administrators can also turn on **auto link by verified e-mail** (see [Administrator settings](/administer/slack#3-administrator-settings-settings-slack)), which matches the Slack profile e-mail against members of the workspace's own organization.

An administrator binds the connected workspace to the organization under **Settings → Slack → Workspace**. Unbound workspaces use `SLACK_DEFAULT_ORG_ID` (see the [configuration reference](/install/configuration)).

## Scheduled missions

A [scheduled mission](/use/automation) can post each run's result to a Slack channel, with a link to the session.

---
