---
title: Missions, templates and automation
description: Agent runs that start on their own, where their results go, reusable starts, and outbound reports.
---

# Missions, templates and automation

## Missions

A **mission** is an agent run the platform starts on its own. Open **Missions** in the sidebar. The editor shows a mission as a flow of four steps, **When → Agent → Task → Deliver**: each step's card says what it holds in plain words ("Every weekday at 09:00 (UTC)", "The reviewer · reviews the pull request"), and choosing a card opens that step below, with **Back** and **Next** to walk through them in order. Each card is marked done or to finish, and a list under the steps says what is still missing (each item opens its step): **Create mission** stays unavailable until the mission has a name and every step is complete. Triggers, the work or review mode and the deliveries are picked as tiles.

The editor opens in **Simple**, which shows what most missions need. **Advanced** adds the rest: a schedule's raw cron and any time zone, the repository, label and branch filters of an event, the less common event sources, the permission mode, model tier and sandbox type, the branch, and a webhook's result schema. A mission that already uses one of these opens in Advanced, so nothing it holds is hidden.

* **When**: one to three triggers, or **Only when run by hand** (a new mission asks for one or the other).
  * a **schedule**: picked in plain words (every weekday, every day or every Monday at a time, every month on the first, second, third or fourth weekday of your choice at a time, or every hour) in a time zone, or, in Advanced, as a five-field cron such as `15 */6 * * *` (at most every five minutes; the monthly choice is written `0 9 * * 1#1`, the first Monday);
  * a GitHub or GitLab event: an issue that gets a label, a comment that addresses the agent (`@rocketchat`, `/agent`, `/fix`), a pull or merge request opened or updated, a review comment, a failed workflow run or pipeline; a Jira or Linear issue. Narrow each with repository patterns (`acme/*`), labels and branches;
  * the mission's own **webhook** (see below);
  * and always **Run now**, by hand, with an optional note on what this run is about.

  An event or webhook trigger can **start the run later**: right away (the default), 15 or 30 minutes, 1, 2, 6 or 24 hours after the event, or, in Advanced, any number of minutes up to 1,440. Use it when the run needs to see what followed the event, such as an hour of logs after a deploy. The event is kept as it arrived until then (a restart loses none), and the run starts within half a minute of its time. Its card reads "When its webhook is called, 1 h later", and the waiting run is listed among the mission's runs as *scheduled* with the time it starts. A run whose mission was switched off or deleted meanwhile does not start. When several triggers take the same event, the shortest wait applies. A mission keeps at most 50 runs waiting; past that, new events are ignored until some start.
* **Agent**: the agent, whether it **does the work** or **reviews the pull request**, whether it follows up its pull request, and (Advanced) its permission mode, a model tier and a sandbox type.
* **Task**: the instructions (optional for a review), the repositories to clone (up to ten `owner/name`; an event's own repository comes first, and the agent clones the ones the task needs), (Advanced) a branch for the first one, and **Allow GitHub writes**. Without it, every run only reads GitHub, whoever it runs as: pushing a branch, opening a pull request or writing through the GitHub tools is refused. It is ticked, and cannot be unticked, while the mission opens a pull request or follows one up. Missions created before this option keep their writes.
* **Deliver**: where the result goes. Every run is a session you can open, review and continue; choose **Its session only** when that is enough, or add any of:
  * **Pull request**: the agent opens a pull request with its change (work missions; needs GitHub writes). The run's delivery is marked delivered with the pull request's link, or failed when it opened none;
  * **Chat**: a message in a Slack channel, through one of your organization's bots (the channel must be one your organization lets it post in). The agent is told it writes a Slack message: short Markdown, which the platform converts to Slack's format, under the mission's name, at most 1,500 characters, written for a reader who did not see the run. The pull requests the run opened are linked under the message when it does not link them itself;
  * **Webhook**: a signed JSON request to an https URL, shaped by a **JSON Schema** you give (the result is checked against it before it is sent);
  * **GitHub review**: one review with inline comments on the pull request the event is about;
  * **GitHub comment**: a comment on the issue or pull request the event is about.

  Mark a delivery **Optional** when a run may have nothing for it: no pull request when there is nothing to change, no message when there is nothing to report. The agent may then leave it out, the delivery is shown as *skipped* and the run still succeeds.

**How a result is delivered.** The agent does not post anything itself. It ends its run by handing the result to the platform (the `deliver` tool), which checks it against what each delivery expects and refuses, with the reason, a result that does not fit, so the agent corrects it. When the run ends, the platform posts it with the mission's own rights: the organization's Slack bot, the webhook's signing secret, the organization's GitHub App. So a review mission can run read-only and still post its review. Every delivery is made only from what the agent hands to `deliver`, never from its final answer: a Slack message, a webhook, a review or a comment the agent gave nothing for is reported as not delivered, or *skipped* when it is optional. A delivery that fails is retried with growing delays, and the mission's **runs** show each run, its session and how each delivery went.

**Who owns a mission, and who it runs as.** A mission is owned like any object: you, one of your teams, or the organization (see [Teams](/administer/organization#teams)). A personal mission runs as you, with your permissions, keys and spend limits. A team's or the organization's runs as the mission itself, a service member that is an ordinary member of the organization (and of the team): its runs use the team's or the organization's keys and limits, never those of the person whose comment or label started them, and their sessions are shared with the team or the organization. Changing the owner moves the mission; the dialog says who will see its runs and whether its agent would still be usable.

**Starting from a preset.** **New mission** shows the presets as tiles under **Start from**; choosing one fills the whole mission: *Pull request review* (reviews each pull request and its new commits, read-only, posts one review), *CI failure fixer*, *Issue labelled `ai-fix` → pull request* (opens a pull request and comments on the issue with what it did), *Weekly dependency audit → Slack*, *Daily engineering digest → Slack* and *Alert triage → webhook* (a structured verdict for your incident tool). Pick the repository, channel or URL and save.

**Review missions.** A mission in review mode (it needs a pull or merge request trigger) runs the reviewer unless it names another agent, and reviews only what changed since its last review when new commits arrive.

**Fixing CI and reviews after the pull request.** With *fix failed checks and clear review comments*, the session that opened a pull request wakes when a check fails or a reviewer comments: it fixes what is clear, pushes, replies to the threads it addressed, and asks in the thread when a comment is ambiguous. The number of automatic rounds stays within the session's continuation limits.

**The mission's webhook.** A mission with a *webhook* trigger has its own URL, shown on its card, and a secret shown once when it is made (rotate it from the card). Sign each call like the outbound webhooks below: `X-Rocket-Timestamp: <unix seconds>` and `X-Rocket-Signature: sha256=<hex>`, the HMAC-SHA256 of `<timestamp>.<body>` with the secret; a call more than five minutes old is refused. The JSON body (up to 64 KB) is what the run is about. When the webhook trigger waits, the call answers `{"status": "scheduled", "starts_at": <unix seconds>}` instead of the started run's session.

```bash
body='{"alert":"p95 latency over 2s","service":"checkout"}'
ts=$(date +%s)
sig=$(printf '%s.%s' "$ts" "$body" | openssl dgst -sha256 -hmac "$SECRET" | cut -d' ' -f2)
curl -X POST "https://rocket.example.com/v1/webhooks/missions/<org>/<mission>" \
  -H "X-Rocket-Timestamp: $ts" -H "X-Rocket-Signature: sha256=$sig" -d "$body"
```

A webhook delivery is signed the same way, with its own secret (shown once), and carries `X-Rocket-Event: mission.result` and a body with the mission, the run, the item it is about and the result.

**Supervision and access.** A mission has nobody watching, so a run that needs an approval waits in its session: use a permission mode that fits unattended work. If the agent a mission runs with stops being usable by its runs (access withdrawn), the mission is turned off with the status *blocked: agent access revoked*; pick another agent and turn it back on.

**Built-in missions.** An organization with no mission on GitHub events keeps the deployment's built-in one: the label `ai-fix` (set by the operator) on an issue, and comments that address the agent, start a session run as the comment's author.

**Jira and Linear.** A run started from a ticket comments its outcome on the ticket when the operator gave the platform an API token for the tracker.

Limits: three triggers, one delivery of each kind and ten repositories per mission; 20 personal missions per member and 200 missions per organization.

## Templates

A template is a reusable way to start a session: a name, an opening prompt, optionally an agent, a repository and a permission mode. Like other objects it belongs to you, a team or the organization. Templates appear on the empty conversation; starting one is exactly the same as typing the prompt yourself, so every permission check applies. A repository on a template is added to the prompt ("Repository for this task: ... Clone it first").

## Outbound webhooks

**Settings → Automation** (administrators) adds URLs that receive a signed JSON report whenever a turn completes or fails, a pull request is opened, or a spend limit blocks a turn, whatever started it. Each request carries `X-Rocket-Event`, `X-Rocket-Delivery`, `X-Rocket-Timestamp` and `X-Rocket-Signature: sha256=<hex>`, the HMAC-SHA256 of `<timestamp>.<body>` with the webhook's secret (shown once, when it is created). Reject a request whose timestamp is more than a few minutes old. A failed delivery is retried with growing delays for about three hours. Reports carry a summary (a session id, title, cost, the answer cut to 2,000 characters), never the transcript. To send one mission's result somewhere, use that mission's **webhook delivery** instead.

## GitLab

Repositories on GitLab (gitlab.com or your organization's own, set by the operator) work like GitHub ones: the agent clones them through the platform, publishes branches and opens merge requests with **your** GitLab token (Settings → Git & version control → GitLab), and the session follows the merge request's pipeline and comments. GitLab has no App to sign commits: commits are pushed as they are, or signed with your GPG key. Review and comment deliveries post on GitHub only.
