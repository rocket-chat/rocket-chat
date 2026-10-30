---
title: Organization settings
description: Members, roles and capabilities, model credentials, spend limits, agents and governance.
---

# Organization settings

Open **Settings → Organization** (administrators only: these pages are hidden from other members). Each page shows a few main settings and folds the rest under *Advanced*; the complete list, with defaults and who decides each one, is the [settings reference](/administer/settings-reference).

## Who decides a setting

Every setting has a **policy**, and each field shows where its value comes from:

| Badge | Meaning |
| :--- | :--- |
| **Default** | The platform's default; nobody changed it. |
| **Set by operator** | An environment variable of the deployment sets it (for example `AGENT_MAX_TURNS`, `GIT_BOT_NAME`). It is read-only in the product, or the default under your own value when the setting says *operator default*. |
| **From organization** | Your organization's value. |
| **Yours** | A member's own value (with a reset button back to the organization's). |
| **Locked by admin** | An organization default an administrator locked: members, sessions and agents keep the organization's value. |
| **From agent "X"** / **From Slack bot "X"** | Chosen at session level, by the agent or the bot, within the organization's policy. |

Agents, Slack bots and channel profiles are *presets*: they offer values for a session, which apply only where the organization's policy allows (an agent cannot get around a locked permission mode). A **ceiling** (the steps per turn) lets an agent lower the organization's value, never raise it.

## Setup health

The top of *General* lists what the organization is missing, each with where to fix it: a provider key behind the default model, a provider that offers embeddings (OpenAI or Gemini; without one, memory and knowledge recall match keywords only), a web search provider other than DuckDuckGo scraping (SearXNG, Brave or Tavily, set by the operator), the GitHub App, and whether the sandbox images are already on the host (Docker; Kubernetes pulls them on each node).

## General

Organization name, how visible new sessions are by default (private to the creator, or shared with the organization), sign-in domains, steps per turn, and the defaults of members' personal settings (each one lockable).

## Members & roles

People can belong to several organizations and switch between them from the organization menu at the top of the sidebar. In **Members & roles**:

* **Roles**: `owner`, `admin`, `member` and `viewer`. A viewer can open what is shared with them but change nothing. Only an owner can make someone an owner, and the last administrator cannot be demoted. A role change applies to the person's open sessions within 15 seconds. (`developer` and `readonly` from earlier versions are read as `member` and `viewer`.)
* **Invite people**: enter an e-mail address, a role (only an owner can invite an owner) and a lifetime (7 days by default, 30 at most), then copy the link, which is shown once, and send it to the person: Rocket Chat does not send e-mail. The link works once, only for that address. The person signs in with an account that has that address (with SSO, the first sign-in creates it), accepts, and the organization is added to theirs. Pending invitations are listed, and can be withdrawn, on the same page.
* **Capabilities**: what each role may do beyond its own work. Owners hold every capability.

| Capability | Default | What it allows |
| :--- | :--- | :--- |
| `create_personal_agents` | admin, member | Agents of their own (*Settings → My agents*). |
| `bring_own_keys` | admin, member | Provider keys of their own (*Settings → My keys*). |
| `register_mcp` | admin, member | Personal remote MCP servers. |
| `publish_as_bot` | admin | Choosing to publish as the GitHub App. |
| `use_bypass_mode` | nobody | Sessions where nothing waits for approval. A request for bypass without it runs in *ask* mode. |
| `manage_spend_limits` | admin | Creating and changing spend limits. |

With SSO, members are created at first sign-in in the organization the sign-in maps to (see [SSO](/administer/sso)). Someone who already belongs to another organization becomes a member of this one too; their role elsewhere never applies here.

## Models & credentials

**Organization credentials** are the provider keys the organization pays for, each with a label. An organization may hold several keys for one provider: one is the default, and a key marked **only for the agents I bind it to** is skipped for everything else, so an expensive key can back one task-specific agent. Keys are stored encrypted and shown only by a fingerprint; rotating a key keeps every agent bound to it. The deployment's own keys (`OPENROUTER_API_KEY` and the like) are listed as *Set by operator*; an organization key for the same provider replaces it for this organization.

Members with `bring_own_keys` add keys of their own. **Members' own keys** decides how they are used: `preferred` (theirs before the organization's), `fallback` (only for a provider the organization has no key for) or `off`. A member may share one of their keys with organization agents, so an administrator can bind it.

**Model policy**: allowed and forbidden models (they apply to every choice, including fail-over and routing), the default model, fall-back models, *routing* (a fast model for titles, summaries and suggestions; an economy mode), and, under Advanced, provider preferences, the working context size, and the data rules: which providers may receive your data, zero retention and region pinning.

Spend is what the provider billed when it reports it (OpenRouter does, for every call), otherwise the model's published price; through OpenRouter the figures therefore match its activity page.

## Spend

**Spend limits** cap what is spent, by subject and period:

* subjects: the organization; each member (or one member); an agent; an agent for each member (or for one); a Slack workspace;
* periods: per day, week or month (UTC), rolling 24 hours or 7 days, per session, per turn;
* modes: **block** refuses the model call that would cross the limit, before anything is bought; **warn** tells the member in the session when the limit is nearly used (at its warning percentage).

A limit for one member replaces the *each member* limit; when that default is a **ceiling**, a limit for one member can only be lower. Calls on a member's own key count against no limit; calls on a key bound to an agent count against that agent's limits. Before each call the platform sets the call's worst-case cost aside on every limit that applies and corrects it when the call ends, so parallel calls cannot overshoot a block limit together.

When a limit stops a turn, the conversation shows which limit, how much was spent, when it resets, and that an administrator can raise it. The session header shows the tightest limit left ("$3.10 left today").

Below the limits, **this month's spend** groups the organization's spend by agent, credential, member or model (calls from before attribution existed are grouped as *unattributed*), and **turn and task limits** bound one turn's cost and automatic continuations.

## Agents

**Settings → Organization → Agents** lists the organization's agents and the **subagent roles** they delegate to, in one editor: identity and prompt, model and credentials, tools and knowledge, sandbox type, access (organization agents) and limits. **Export** saves an agent as `.rocket/agents/<id>.md` (front matter and the prompt, never credentials); **Import** reads one back.

* **Model & credentials**: the model, one **Reasoning** level (the thinking budget follows from it), and the agent's own **credentials**: a call on a bound provider uses the bound key. With **strict**, a call on any other provider is refused instead of billed elsewhere. Administrators bind organization agents (organization keys, or members' keys their owners shared with that agent); a personal agent binds its owner's keys only. If a bound key is deleted or no longer shared, the agent's next call fails with "credential withdrawn".
* **Sandbox**: the sandbox type its sessions start in; the type holds the image, CPU and memory (see [Sandboxes](/administer/sandboxes)).
* **Access**: everyone (the default), or only some people and roles, and whether guests (Slack users who have not paired, guest API tokens) may use it. Administrators can always use every agent. Someone who loses access finishes the turn already running; their next one fails with a prompt to pick another agent, and their scheduled missions with that agent are paused. A Slack bot's agent must allow guests.
* **Limits**: steps per turn (at most the organization's), the default permission mode (shown *Locked by admin* when the organization locked it), and the agent's spend limits.

The built-in agents are **General**, **Backend**, **Reviewer** (read-only plus pull request tools, reviews a change), **Architect** (the frontier model, starts in plan mode) and **Security Auditor**. The built-in subagent roles are explorer, researcher, reviewer, qa_verifier, security_auditor and implementer.

A **subagent role** also sets: its **tier** when it names no model (*fast*: the organization's fast model; *default*: what routing gives subagents; *frontier*: the session's model), its **isolation** (the shared tree, a worktree of its own, or a sandbox of its own; roles that edit or run commands should not share the tree), a **spend cap** per run (3 USD by default; 0 for none) and an optional **time cap**, whether it **may ask the person** (otherwise its approvals and questions are refused and it reports instead), what it **starts with** (the repository's AGENTS.md, relevant memories, the agent's checklist, skills the agent names) and an optional **output schema** its report's data must follow. Its tool calls go through the session's approvals and limits, and its cost counts toward the turn's spend limit. When it reaches a limit it still reports what it found. A subagent can never delegate further. A repository may add its own roles in `.rocket/agents/<id>.md` (the same front matter as an export; never with a sandbox of its own or a right to ask); an organization role with the same id wins.

## Git & GitHub

The GitHub App installations, **Publish as** (`member`: pull requests opened with the member's linked account; `bot`: the GitHub App, which needs `publish_as_bot`; members may choose unless you lock it), **Commit signing** (`github`: GitHub signs commits made through its API; `member_gpg`: a member who imported a GPG key commits as themselves, signed with it, and everyone else gets `github`; `none`), the bot's name and e-mail (the deployment's `GIT_BOT_NAME` / `GIT_BOT_EMAIL` are their defaults), and the repositories the App acts on. See [GitHub](/administer/github).

## Security & data

* **Approval policy**: for each risk level, require a human, approve automatically, or deny. This sits on top of each session's permission mode.
* **Hooks**: commands run in the sandbox before or after a tool call (a failing pre-tool command blocks the call).
* **Data handling**: redact secrets from tool output and traces, and set retention windows for sessions, traces, memories, attachments and audit events.
* **Memory**: turn the agent's long-term memory on or off and limit what it may store.

## Sandboxes

See [Sandboxes](/administer/sandboxes).

## Audit

Approvals, tool calls, credential changes (organization and member keys: created, rotated, deleted, bound to an agent, unbound), settings changes, invitations, access changes and logins are recorded with the actor, the session and a redacted summary. Read them in **Settings → Organization → Audit log**, or at `GET /v1/admin/audit`; retention is a governance setting.

## Starter content

Every organization starts with six instruction skills members run with `/name` in the composer: `review-diff`, `write-tests`, `fix-ci-failure`, `write-pr-description`, `investigate-bug` and `upgrade-dependency`. Switch any off in *Skills* (or with `general.starter_skills`); a skill of your own with the same name replaces it. *Missions* offers three templates, created paused: a weekly dependency audit, flaky-test triage and a stale-branch report.

## Answer feedback

Members rate answers with a thumbs up or down and an optional comment. `GET /v1/admin/feedback?since=<epoch>` exports the organization's ratings with the prompt and answer excerpts, newest first, to build evaluation sets.
