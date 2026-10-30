---
title: Your settings
description: Your identity, preferences, keys, agents, GitHub account and notifications.
---

# Your settings

Open **Settings** from the sidebar (or the **View** menu in a session). The list has three groups: *Personal* (only affects you), *Integrations* (MCP servers, skills, knowledge bases) and *Organization* (shown to administrators only; see [Administer](/administer/index)). A page you need a capability for (*My keys*, *My agents*) shows when your role holds it. Each setting shows where its value comes from: **Default**, **From organization**, **Yours** (with a reset back to the organization's), **Locked by admin** (you cannot change it), or **Set by operator**. The organization menu at the top of the list (and of the sidebar) shows which organization you work in and switches to another one you belong to. The foot of the list shows the version of the running server (`v0.5.0`), a development build shows `dev` and, for a published dev image, the date and time (UTC) it was built, such as `dev-21102026-1036`.

**Saving.** A page never has a Save button per section. Edit as many fields as you like and a bar at the bottom shows how many changes are unsaved, with one **Save changes** and one **Discard**. Rarely changed settings are folded under **Advanced**, a number shows what it counts (minutes, USD, tokens), and **Reset to default** puts a changed value back.

**Finding a setting.** Press **⌘K** (Ctrl+K) in Settings, or click **Search settings**, and type what you want to change ("branch prefix", "notifications"): the page opens scrolled to that setting.

![The settings console](../assets/screenshots/settings.png)

## Profile

* **Display name.** Shown on your messages in shared sessions, and the commit author name when you set no git name.
* **Password** (password accounts).

## Organizations and invitations

You can belong to several organizations, with a role in each. The organization menu at the top of the sidebar (and of Settings) shows the one you work in; pick another to switch. Your sessions, agents, keys and settings belong to one organization and are not shown in the others.

An administrator invites you with a link for your e-mail address. Open it, sign in with an account that has that address, and choose **Accept the invitation**: the organization is added to yours and **Open** switches to it. A link works once and expires; ask for a new one if it no longer works.

## Preferences

* **Preferred model.** The model your sessions use unless the agent or the organization decides otherwise.
* **Default permission mode.** How closely the agent is supervised when you do not pick a mode for a session (*Read-only*, *Ask first*, *Auto-edit*, *Auto*, *No approvals*). *No approvals* needs the `use_bypass_mode` capability. See [How sessions work](/use/sessions#supervision).
* **Suggest next prompts.** The ghost text in the composer that you accept with **Tab**; turn it off if it distracts you.
* **Branch prefix.** New branches the agent creates start with it (for example `alex/rocket-`).
* **Notifications.** A browser notification or a sound when a turn ends or waits for you while its tab is in the background.
* **Appearance.** Light, dark or the system's theme.

## Git identity

* **Linked GitHub account.** Link it through OAuth so the agent can clone your private repositories and push branches as you; re-link or unlink at any time. Without a link, the organization's GitHub App installation is used, with read access to what it was granted.
* **Personal access token.** An alternative to linking: paste a classic or fine-grained token; the page tests what it can do.
* **Committer identity.** The name and e-mail on commits the agent makes for you.
* **Publishing.** Who opens your pull requests: you (your linked account) or the GitHub App (needs the `publish_as_bot` capability). It follows your organization until you change it, unless an administrator locked it.
* **Commit signing.** When your organization signs with members' keys (`member_gpg`), import your GPG key here: your commits are then made as you and signed with it.

The page shows which credential the sandbox will actually use (yours, or the app's read-only token), so a failed push is never a mystery.

## My keys

If your role holds `bring_own_keys` you can add **your own provider keys** (Anthropic, OpenRouter, OpenAI, Gemini, DeepSeek, Ollama), each with a label: they are stored encrypted, never shown again, and what they spend is yours (it counts against no spend limit of yours). Your organization decides when they are used (before its own key, only where it has none, or never). You can **share** a key with an organization agent, so an administrator can bind it to that agent; stop sharing it at any time, and the agent's next call on it fails as *credential withdrawn*. Calls an agent makes on a key you shared count against that agent's limits.

**Sign in with OpenRouter** adds an OpenRouter key without copying one: approve on OpenRouter, and the key it issues is stored like the others (marked *Signed in*). Administrators can do the same for an organization key. Consumer subscriptions such as Claude Pro or ChatGPT Plus cannot be used: their providers do not allow third-party applications to use them.

## My agents

If your role holds `create_personal_agents`, **My agents** edits agents only you can use, in the same editor as the organization's agents (see [Organization settings](/administer/organization#agents)). A personal agent can be bound to your own keys only.

## Usage

**Usage** shows what your sessions spent this month, in total and grouped by model, agent or credential, with what you spent on your own keys shown apart.

## API tokens

**Settings → API tokens** creates the tokens that let a script or another tool call the API as you, without signing in through the browser.

* **Create.** Give the token a name and a lifetime (30 days, 90 days or one year). The token is shown once, right after you create it: copy it then, because only a hash is stored. You can hold up to 20.
* **Use.** Send `Authorization: Bearer rcpat_…` with any REST request, or `?token=rcpat_…` when opening the WebSocket. A token acts as you with the role you hold when it is used, and cannot create another token. The [API reference](/api/rest) lists every endpoint.
* **Revoke.** The trash button stops a token at once. The table shows when each token was created, expires and was last used.
* **Guest tokens (administrators).** For a client that has no account here, such as a bot. Each guest token acts as its own guest member, which can hold conversations and nothing else. They are listed and revoked in the second section of the same page.

## Slack

* **Link your Slack account** with the bot's `pair` command in Slack (for example `/rocky pair`) and the code it gives you (see [Slack](/use/slack)).

## Your own tools

Personal [skills, connectors, knowledge bases and agents](/use/extend) have their own pages in the same list. Anything you create there is private to you unless you are an administrator sharing it with the organization.
