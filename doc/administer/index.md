---
title: Administer
description: Set Rocket Chat up for your organization.
---

# Administer

Administrators shape what the agent may do for the whole organization. Everything below is under **Settings** in the app; most of it takes effect on the next turn.

| Task | Guide |
| :--- | :--- |
| Let people sign in with your identity provider | [SSO](/administer/sso) |
| Connect GitHub (clone, pull requests, issue automation) | [GitHub](/administer/github) |
| Connect Slack | [Connect Slack](/administer/slack) |
| Offer sandboxes and decide what each can do | [Sandboxes](/administer/sandboxes) |
| Choose models, set budgets, set data-handling and approval policy | [Organization settings](/administer/organization) |
| Understand the security model and rotate secrets | [Security](/administer/security) |
| Look up any setting | [Settings reference](/administer/settings-reference) |

## How settings are layered

Each setting has a platform default, can be overridden by the organization, and (for personal ones) by the member. An administrator can **lock** a setting so members cannot change it; the member then sees it as *locked by organization*. Settings are validated, and a change is recorded in the audit log.

## Roles

| Role | Can |
| :--- | :--- |
| **Owner / admin** | Everything, including members, settings, GitHub and Slack connections, the sandbox catalogue. |
| **Member (developer)** | Use the agent, own sessions, personal settings, personal skills and connectors. |
| **Read-only** | Read sessions they can see; cannot start or steer work. |

Members, roles and temporary passwords (for password sign-in) are managed under **Settings → Users & roles**.
