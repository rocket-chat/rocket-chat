---
title: Organization settings
description: Models, budgets, governance, privacy and approvals.
---

# Organization settings

Open **Settings → Organization**. Each page edits one group of settings; the complete list, with defaults and descriptions, is the [settings reference](/administer/settings-reference).

## General

Organization name, how visible new sessions are by default (private to the creator, or shared with the organization), whether the composer suggests the next message, and the compliance tier that other defaults follow.

## Models & budgets

Which models members may use and which are forbidden, the default model, fallbacks, and *task-aware routing* (a cheaper model for titles, summaries and suggestions than for the main loop). Provider keys are stored encrypted, per organization or per member (**bring your own key**); a member's key is used for their sessions and never shown again. Self-hosted models (Ollama, vLLM, any OpenAI-compatible server) work the same way; see [air-gapped installs](/install/air-gapped).

## Budgets and usage

**Usage & spend** shows spend by member, model and session. Under *Budgets* set a monthly limit for the organization, one per member and one per session, and the percentage at which to warn; a turn that would exceed a limit is stopped with a clear message instead of billing past it. A policy decides whether members may use their own provider key (**bring your own key**). **Organization provider keys** (under *Models & budgets*) also lists the providers whose key the deployment holds in its environment (`OPENROUTER_API_KEY` and the like, shown by a preview, never the key); **Override** saves a key of your own for that provider, which then replaces the deployment's for this organization only, and removing it returns to the deployment's key. Slack turns have their own share of spend and rate limits.

Spend is what the provider billed when it reports it (OpenRouter does, for every call), otherwise the model's published price; through OpenRouter the figures therefore match its activity page. Two model settings help long sessions: **working context size** (`context_target_tokens`, off by default so a model uses its whole window) keeps conversations compacted below the value you set, for example 200,000, even on models with a larger window, and **provider preferences** (`provider_preferences`) pin an OpenRouter model to a provider so its prompt cache stays warm, for example `{"openrouter/deepseek/*": {"order": ["deepseek"], "allow_fallbacks": false}}`.

## Governance & memory

* **Approval policy**: for each risk level, require a human, approve automatically, or deny. This sits on top of each session's permission mode, and an organization can forbid *bypass* mode entirely.
* **Hooks**: commands run in the sandbox before or after a tool call (a failing pre-tool command blocks the call).
* **Data handling**: restrict which model providers may see your data, require zero-retention endpoints, redact secrets from tool output and traces, and set retention windows for sessions, traces and audit events.
* **Memory**: turn the agent's long-term memory on or off and limit what it may store.

## Subagents

The agent can hand a focused task to a subagent: a delegate with its own conversation that reports back. **Settings → Subagents** lists the roles and lets an administrator edit them or add new ones: instructions, model, step limit (1 to 60) and tools. The built-in roles are:

| Role | Tools | Use |
| :--- | :--- | :--- |
| Researcher | read, search (`grep_search`, `glob_files`, `list_dir`, `code_search`), web | Find where something is and how it works |
| Security auditor | the same read-only tools | Review code for vulnerabilities |
| QA verifier | `run_tests`, `bash_exec`, `file_read`, `grep_search`, `list_dir` | Run the tests and explain failures |

Each built-in role has 12 steps. A subagent works in the session's sandbox and active repository, under the same permission mode and approvals as the agent, and its tool calls share the session's limits. When it runs out of steps it still reports what it found and what it could not check. A subagent can never delegate further. A role the agent names that nobody configured gets the read-only tools.

## Users & roles

Create password members (with a one-time temporary password), change roles, and see who is active. With SSO, members are created at first sign-in and roles can come from a `roles` claim in the token (see [SSO](/administer/sso)).

## Sandboxes

See [Sandboxes](/administer/sandboxes).

## Audit

Approvals, tool calls, credential changes, settings changes and logins are recorded with the actor, the session and a redacted summary. Read them in **Settings → Organization → Audit log**, or at `GET /v1/admin/audit`; retention is a governance setting.
