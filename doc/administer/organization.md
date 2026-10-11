---
title: Organization settings
description: Models, budgets, governance, privacy and approvals.
---

# Organization settings

Open **Settings → Organization**. Each page edits one group of settings; the complete list, with defaults and descriptions, is the [settings reference](/administer/settings-reference).

## General

Organization name, how visible new sessions are by default (private to the creator, or shared with the organization), whether the composer suggests the next message, and the compliance tier that other defaults follow.

## Models & routing

Which models members may use and which are forbidden, the default model, fallbacks, and *task-aware routing* (a cheaper model for titles, summaries and suggestions than for the main loop). Provider keys are stored encrypted, per organization or per member (**bring your own key**); a member's key is used for their sessions and never shown again. Self-hosted models (Ollama, vLLM, any OpenAI-compatible server) work the same way; see [air-gapped installs](/install/air-gapped).

## Budgets and usage

**Token Usage & Spend** shows spend by member, model and session. Under *Budgets* set a monthly limit for the organization, one per member and one per session, and the percentage at which to warn; a turn that would exceed a limit is stopped with a clear message instead of billing past it. A policy decides whether members may use their own provider key (**bring your own key**). Slack turns have their own share of spend and rate limits.

## Governance & memory

* **Approval policy**: for each risk level, require a human, approve automatically, or deny. This sits on top of each session's permission mode, and an organization can forbid *bypass* mode entirely.
* **Hooks**: commands run in the sandbox before or after a tool call (a failing pre-tool command blocks the call).
* **Data handling**: restrict which model providers may see your data, require zero-retention endpoints, redact secrets from tool output and traces, and set retention windows for sessions, traces and audit events.
* **Memory**: turn the agent's long-term memory on or off and limit what it may store.

## Users & roles

Create password members (with a one-time temporary password), change roles, and see who is active. With SSO, members are created at first sign-in and roles can come from a `roles` claim in the token (see [SSO](/administer/sso)).

## Sandboxes

See [Sandboxes](/administer/sandboxes).

## Audit

Approvals, tool calls, credential changes, settings changes and logins are recorded with the actor, the session and a redacted summary. Read them at `GET /v1/admin/audit`; retention is a governance setting.
