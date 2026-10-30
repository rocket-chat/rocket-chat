---
title: Slack
description: "Work with Rocket Chat from Slack: mentions, direct messages, slash commands and streamed replies."
---

# Slack

If your administrator has [connected Slack](/administer/slack), you can use the same agent from Slack. Every Slack thread is also a [session](/use/sessions) in the web app, so work can move between the two.

## What you see

**Response modes: Thread vs Private session.** Your administrator can configure bots and routes to respond in one of two modes:
- **Thread mode (public):** while Slack's status line shows the bot is working, its reply in the thread shows the step it is on and the agent's checklist (each item with its status), then the answer in the same message, closed by a summary line (Done, Waiting or Failed · time · what changed · cost · pull request) and an **Open in Rocket Chat** button. A turn that only answers posts the answer directly. To stop a turn you asked for, click Slack's stop button under the thread, or use the bot's command: `/<command> stop`. In the web app, a session that came from Slack shows the Slack icon in the sidebar, a banner above the conversation saying it is bound to a Slack thread, and **Open in Slack** links to the thread.

**Who can open a thread's session.** Anyone in your organization who has the **Open in Rocket Chat** link can read a public thread's session, without it filling everyone's sidebar. It appears in your own list, and you can write in it from the web app, once you take part in the thread with a linked account. Direct messages and private requests stay with the person who asked.
- **Private mode:** When invoked, the bot marks your message with an acknowledgement reaction (default :white_check_mark:) and sends an ephemeral message in the thread visible *only to you*. It includes an **Open session** button linking to your private cockpit session. The work happens privately; when finished, an ephemeral summary is posted to you in the thread alongside a **Share in thread** button that allows you to post the final answer into the public thread when you choose. Follow-up messages you type in the thread continue your private session.

**Follow-ups need no mention.** Once the bot works in a thread, a reply addressed to it continues the conversation without `@`-mentioning it (small talk such as "thanks" is ignored). In private mode only the person who asked continues their own session. Editing your latest request runs it again with the new text; deleting it while the turn runs stops the turn.

**Pull requests get a card.** When the agent opens a pull request, a card shows its link, branch, the files changed and the CI state, and is updated in place when CI passes or fails and when the pull request is merged or closed.

**Waits are said.** A turn that waits ("Waiting for CI on acme/api#123; I'll post here.") says so in its summary, and the result is posted in the thread when it arrives. After an answer, the bot may offer a **suggested next step** as a button; clicking it sends it as your message.

**A failed turn offers Retry.** In a shared thread the bot says that something went wrong without the raw error (open the session for the details); in your direct messages it says what failed. **Retry** runs your request again; only you can click it. A turn stopped from the web app says so.

**Every turn reaches the thread.** Work that continues without a new Slack message (a CI result or a review the agent was waiting for, a long task's next step, a restart, a message someone sent from the web app on this session) is posted in the same thread, as its own answer.

**A message sent while the agent works is queued.** The bot replies "Queued — I'll take this next." and takes it as soon as the current turn ends; its answer follows in the thread. A message with files starts its own turn.

**Questions and approvals are buttons in the thread.** When the agent needs a decision, or wants to run something your organization requires [approval](/administer/security#b-per-org-approval-policy) for, it posts buttons named after the action ("Approve git push"). A question with several possible answers has checkboxes and **Submit**, and every question has **Other…** for an answer of your own. Once answered, the message shows the decision and its buttons go away. In thread mode they are posted to the thread; in private mode they are posted as ephemeral buttons visible only to you. Only the person who started the request can answer. This keeps working after a restart: a question that was waiting when the server restarted is picked up again from the stored question, and the work continues in the same thread.

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

An agent whose tool list includes Slack tools has them in every session, not only in the ones a Slack bot started: from the web cockpit or a trigger it reads, finds people and posts through your organization's Slack bot (the first by id if you run several), under the same policy. Outside a Slack thread there is no current thread, so it names the channel or thread it means. If no bot of your organization is running, the tools say Slack is not connected.

## Ways to start

Your organization may connect multiple bots (e.g. `@RocketDev`, `@RocketOps`, `@RocketTriage`), each mapped to its own agent or to a router that picks one. Mentioning a bot routes your request directly to it.

| You do | What happens |
| :--- | :--- |
| `@<bot> <task>` in a channel | Answers in that message's thread (or privately, if configured). Follow-ups in the thread continue the same conversation. |
| Message the bot directly | A private conversation under your own Rocket Chat account (once linked). A new message within an hour of the last continues the same session (your administrator sets the window). |
| `/<command> new <task>` | Posts "you asked *the bot*: …" (by its name) and works in a new thread. Each bot has its own command, named after it (`/triage` for `@triage`); `help` lists them. |
| `/<command> status` | Lists your recent Slack threads and which are running (only you see it). |
| `/<command> stop` | Stops what you have running with that bot in this channel. |
| **Ask Rocket** in a message's ⋮ menu | Works on that message, in its thread. |
| Open the app's **Home** tab | Who you are linked as, your recent threads and these tips. |

**Shortcuts** in a message are taken out of it and applied to that request: `!fast` (less reasoning) and `!deep` (more), `!agent <name>` (answer with that agent, if you may use it), `!repo <owner/name>` or `!repo <owner/name>@<branch>` (work on that repository). They go through the same rules as the web app, so a locked setting stays locked. A message that starts with `(aside)` is not for the bot.

**Channel repositories.** A channel can be given a repository and branch in the bot's routing table; the agent is told to clone it before it starts, so you don't have to name it each time.

---

## Link your account

**Publishing from a shared channel.** In a channel thread, the agent cannot push, open pull requests or revert for an unlinked person. For a linked member of the organization, it can, under that member's own GitHub identity, after the member approves each publication with the button the agent posts (unless the administrator turned on *restrict linked members*).

Without a link, your direct messages belong to a **guest** member made for your Slack account (it cannot sign in, has no credentials of its own, and an administrator can see its sessions), and a channel conversation belongs to the workspace's guest (no one's personal credentials are ever used in a shared thread). Linking connects you to your own Rocket Chat account for direct messages: your GitHub access, your model keys, your spend limits and the agents you may use. When you link, the sessions your guest started become yours.

1. In Slack run the bot's command with `pair` (for example `/rocky pair`). Rocket replies privately with an 8-character code, valid for 10 minutes and usable once.
2. In Rocket Chat, signed in, open **Settings → Slack** (see [your settings](/use/personal-settings#slack)) and enter the code.

Administrators can also turn on **auto link by verified e-mail** (see [Administrator settings](/administer/slack#3-administrator-settings-settings-slack)), which matches the Slack profile e-mail against members of the workspace's own organization.

An administrator binds the connected workspace to the organization under **Settings → Slack → Workspace**. Unbound workspaces use `SLACK_DEFAULT_ORG_ID` (see the [configuration reference](/install/configuration)).

## Scheduled missions

A [scheduled mission](/use/automation) can post each run's result to a Slack channel, with a link to the session.

---
