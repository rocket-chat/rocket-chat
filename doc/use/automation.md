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

## Triggers

Administrators decide what starts work on its own under **Settings → Organization → Automation**. A trigger names:

- **when**: a GitHub or GitLab issue that gets a label, a comment that addresses the agent (`@rocketchat`, `/agent`, `/fix`), a pull or merge request opened or updated, a review comment, a failed workflow run or pipeline; a Jira or Linear issue; a schedule (cron, UTC); or a call to `POST /v1/triggers/{id}/run`;
- **which ones**: repository patterns (`acme/*`), labels and branches;
- **how**: the agent, permission mode, model tier and sandbox type, and instructions placed before the event's text.

Every delivery starts at most one session, however many times it is delivered. An organization without triggers keeps the old behaviour: the deployment's label (`ai-fix` by default) on an issue, and comments that address the agent, start a session.

**Review mode.** A pull or merge request trigger in review mode runs the reviewer (unless it names another agent): it reads the change and posts one review with inline comments on the lines that need a change, requesting changes when one must be fixed before merging. When new commits arrive, it reviews only what changed since its last review.

**Fixing CI and reviews after the pull request.** With *fix failed checks and clear review comments*, the session that opened a pull request wakes when a check fails or a reviewer comments: it fixes what is clear, pushes, replies to the threads it addressed, and asks in the thread when a comment is ambiguous. Without it, those events wait for the session's next turn. The number of automatic rounds stays within the session's continuation limits.

**Jira and Linear.** A session started from a ticket comments its outcome on the ticket when the operator gave the platform an API token for the tracker.

## Outbound webhooks

The same page adds URLs that receive a signed JSON report when a turn completes or fails, a pull request is opened, or a spend limit blocks a turn. Each request carries `X-Rocket-Event`, `X-Rocket-Delivery`, `X-Rocket-Timestamp` and `X-Rocket-Signature: sha256=<hex>`, the HMAC-SHA256 of `<timestamp>.<body>` with the webhook's secret (shown once, when it is created). Reject a request whose timestamp is more than a few minutes old. A failed delivery is retried with growing delays for about three hours. Reports carry a summary (a session id, title, cost, the answer cut to 2,000 characters), never the transcript.

## GitLab

Repositories on GitLab (gitlab.com or your organization's own, set by the operator) work like GitHub ones: the agent clones them through the platform, publishes branches and opens merge requests with **your** GitLab token (Settings → Git & version control → GitLab), and the session follows the merge request's pipeline and comments. GitLab has no App to sign commits: commits are pushed as they are, or signed with your GPG key.
