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
| Invite people, set roles and capabilities | [Organization settings](/administer/organization) |
| Add model credentials, choose models, set spend limits, data-handling and approval policy | [Organization settings](/administer/organization) |
| Understand the security model and rotate secrets | [Security](/administer/security) |
| Look up any setting | [Settings reference](/administer/settings-reference) |

## How settings are layered

Each setting has a platform default (some have an operator default from an environment variable), and a policy that says who decides it: the operator, the organization, the organization as a default members may change, or each member. An administrator can **lock** an organization default so members, sessions and agents keep the organization's value; the field then shows *Locked by admin*. Every field shows where its value comes from ([Who decides a setting](/administer/organization#who-decides-a-setting)). Settings are validated, and a change is recorded in the audit log.

## Roles

| Role | Can |
| :--- | :--- |
| **Owner** | Everything an admin can, and make other people owners. |
| **Admin** | Members, invitations, settings, credentials, agents, GitHub and Slack connections, the sandbox catalogue. |
| **Member** | Use the agents granted to them, own sessions, personal settings, skills and connectors, within the capabilities of the role. |
| **Viewer** | Read sessions they can see; cannot start or steer work. |

What a role may do beyond its own work (personal agents, own keys, bypass mode, personal MCP servers, publishing as the GitHub App, spend limits) is a **capability** an administrator grants per role. Members, invitations, roles, capabilities and temporary passwords (for password sign-in) are managed under **Settings → Organization → Members & roles**. A person can belong to several organizations and switch between them from the sidebar.
