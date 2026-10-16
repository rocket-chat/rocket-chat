---
title: Your settings
description: Your identity, models, GitHub account and notifications.
---

# Your settings

Open **Settings** (the gear in the top bar). The *Personal* group at the top of the list is yours; the *Organization* group is for administrators (see [Administer](/administer/index)). When an administrator **locks** a setting you see it as *locked by organization* and cannot change it.

![The settings console](../assets/screenshots/settings.png)

## Profile and preferences

* **Display name.** Shown on your messages in shared sessions.
* **Suggested next message.** The ghost text in the composer that you accept with **Tab**; turn it off if it distracts you.
* **Default permission mode.** How closely the agent is supervised when you do not pick a mode for a session (*Plan*, *Ask*, *Auto-edit*, *Auto*). See [How sessions work](/use/sessions#supervision).
* **Preferred model.** The model your sessions use unless the agent or the organization decides otherwise.

## Git and GitHub

* **Linked GitHub account.** Link it through OAuth so the agent can clone your private repositories and push branches as you; re-link or unlink at any time. Without a link, the organization's GitHub App installation is used, with read access to what it was granted.
* **Personal access token.** An alternative to linking: paste a classic or fine-grained token; the page tests what it can do.
* **Committer identity.** The name and e-mail on commits the agent makes for you.
* **Branch prefix.** New branches the agent creates start with it (for example `alex/rocket-`).
* **Your git policy.** Commit authorship, who opens pull requests and how commits are signed. Each follows your organization's choice (**Org Default**) until you change it; the reset button next to a field you changed makes it follow the organization again. A field your administrator locked cannot be changed.

The page shows which credential the sandbox will actually use (yours, or the app's read-only token), so a failed push is never a mystery.

## Models and keys

If your organization allows it you can add **your own provider key** (OpenRouter, Anthropic, OpenAI, Gemini, DeepSeek): it is stored encrypted, used only for your sessions, never shown again, and your usage is billed to you. Your usage and spend are listed under *Token usage*.

## Slack

* **Link your Slack account** with the bot's `pair` command in Slack (for example `/rocky pair`) and the code it gives you (see [Slack](/use/slack)).
* **Notifications.** Receive a Slack message when a long-running request finishes.

## Your own tools

Personal [skills, connectors, knowledge bases and agents](/use/extend) have their own pages in the same list. Anything you create there is private to you unless you are an administrator sharing it with the organization.
