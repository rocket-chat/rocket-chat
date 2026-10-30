---
title: Templates, schedules and GitHub automation
description: Reusable starts, scheduled missions and work triggered from GitHub.
---

# Templates, schedules and GitHub automation

## Templates

A template is a reusable way to start a session: a name, an opening prompt, optionally an agent, a repository and a permission mode. Members keep personal templates; administrators publish organization-wide ones. They appear on the empty conversation; starting one is exactly the same as typing the prompt yourself, so every permission check applies. A repository on a template is added to the prompt ("Repository for this task: ... Clone it first").

## Scheduled missions

A **mission** runs a prompt on a schedule as an ordinary session owned by you, with your permissions, keys, spend limits and permission mode. Create one under **Missions**:

* **Schedule**: pick a preset or write a five-field cron (for example `0 9 * * 1-5`) and a time zone. The minimum interval is five minutes.
* **Supervision**: a mission has nobody watching, so a run that needs an approval waits for you in its session. Use a permission mode that fits unattended work.
* **Report to Slack**: give a channel id and each run's result is posted there with a link to the session (the channel must be one your organization lets the agent post in).
* **Run now** starts a run without moving the schedule; each run is a session you can open, review and continue.
* **Agent access**: a mission runs with an agent you may use; if an administrator withdraws your access to it, the mission is turned off with the status *blocked: agent access revoked*; pick another agent and turn it back on.

**Start from a template** offers a weekly dependency audit, flaky-test triage and a stale-branch report; they are created paused, in read-only mode, so you review the prompt and schedule before switching them on.

Limits: 20 missions per member and 200 per organization.

## GitHub automation

When your administrator has installed the GitHub App, adding the trigger label (`ai-fix` by default) to an issue starts a session for that repository: the agent clones it, works on a branch (`agent/issue-<n>-fix` unless the event names one) and can open a signed pull request. Further comments on the same issue continue the same session. See [GitHub](/administer/github) for setup.
