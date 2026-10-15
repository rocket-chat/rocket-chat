---
title: Slack
description: Work with Rocket Chat from Slack: mentions, direct messages, /rocket and the live progress message.
---

# Slack

If your administrator has [connected Slack](/administer/slack), you can use the same agent from Slack. Every Slack thread is also a [session](/use/sessions) in the web app, so work can move between the two.

## What you see

**One live message per request.** Rocket posts a single message in the thread and updates it as it works: what it is doing right now, the last few steps, a summary ("Edited 2 files · Ran 3 commands"), a **Stop** button and an **Open in Rocket Chat** link. When it finishes, the message is removed and the answer ends with one line: **Open in Rocket Chat** and what the turn did ("Done in 41s · Edited 2 files · Ran 3 commands"). If the turn is stopped or fails, the message stays and says why. Only the person who asked can press Stop.

**Questions and approvals are buttons in the thread.** When the agent needs a decision, or wants to run something your organization requires [approval](/administer/security#b-per-org-approval-policy) for, it posts buttons. Only the person who started the request can answer. This keeps working after a restart: a question that was waiting when the server restarted is picked up again from the stored question, and the work continues in the same thread.

**Long output is attached, not split.** An answer longer than a few thousand characters is summarised in the thread and attached in full as `answer.md`. A large change is listed in the thread and attached as `changes.diff`. Screenshots the agent takes are uploaded as images.

**Files you attach are read.** Logs, screenshots and CSVs attached to a message that mentions Rocket are downloaded into the sandbox and shown to the model.

## Ways to start

| You do | What happens |
| :--- | :--- |
| `@Rocket <task>` in a channel | Answers in that message's thread. Follow-ups in the thread continue the same conversation. |
| Message Rocket directly | A private conversation under your own Rocket Chat account (once linked). |
| `/rocket new <task>` | Posts "you asked Rocket: …" and works in a new thread. |
| `/rocket status` | Lists your recent Slack threads and which are running (only you see it). |
| `/rocket stop` | Stops what you have running in this channel. |
| **Ask Rocket** in a message's ⋮ menu | Works on that message, in its thread. |
| Open the app's **Home** tab | Your link status, recent threads and these tips. |

---

## Link your account

Without a link, your direct messages belong to a **guest** member made for your Slack account (it cannot sign in, has no credentials of its own, and an administrator can see its sessions), and a channel conversation belongs to the workspace's guest (no one's personal credentials are ever used in a shared thread). Linking connects you to your own Rocket Chat account for direct messages: your GitHub access, your model keys, your budget. When you link, the sessions your guest started become yours.

1. In Slack run `/rocket pair`. Rocket replies privately with an 8-character code, valid for 10 minutes and usable once.
2. In Rocket Chat, signed in, open **Settings → Slack** (see [your settings](/use/personal-settings#slack)) and enter the code.

Administrators can also turn on **auto link by verified e-mail** (see [Administrator settings](/administer/slack#3-administrator-settings-settings-slack)), which matches the Slack profile e-mail against members of the workspace's own organization.

An administrator binds the connected workspace to the organization under **Settings → Slack → Workspace**. Unbound workspaces use `SLACK_DEFAULT_ORG_ID` (see the [configuration reference](/install/configuration)).

## Scheduled missions

A [scheduled mission](/use/automation) can post each run's result to a Slack channel, with a link to the session.

---
