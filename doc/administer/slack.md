# Connect Slack

Rocket Chat works inside Slack: mention it in a channel, message it directly, or start work with `/rocket`. This page connects it and sets the policy; what people see and do is described in [Slack for users](/use/slack).

The connection is **Socket Mode**: Rocket Chat opens an outbound WebSocket to Slack. You need no public URL, no inbound firewall rule and no request signing secret.

---

## 1. Create the Slack app (two minutes, from a manifest)

1. Open [api.slack.com/apps](https://api.slack.com/apps) and choose **Create New App → From an app manifest**, then pick your workspace.
2. Paste this manifest (YAML) and create the app:

```yaml
display_information:
  name: Rocket Chat
  description: A coding agent with its own sandbox, in your Slack
features:
  app_home:
    home_tab_enabled: true
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
  bot_user:
    display_name: Rocket
    always_online: true
  shortcuts:
    - name: Ask Rocket
      type: message
      callback_id: ask_rocket
      description: Work on this message with Rocket
  slash_commands:
    - command: /rocket
      description: Start, check or stop work with Rocket
      usage_hint: "new <task> | status | stop | pair | help"
      should_escape: false
    - command: /rocket-pair
      description: Link your Slack account to Rocket Chat
      should_escape: false
oauth_config:
  scopes:
    bot:
      - app_mentions:read
      - assistant:write
      - channels:history
      - channels:read
      - chat:write
      - commands
      - files:read
      - files:write
      - groups:history
      - groups:read
      - im:history
      - im:read
      - im:write
      - mpim:history
      - mpim:read
      - reactions:write
      - usergroups:read
      - users:read
      - users:read.email
settings:
  event_subscriptions:
    bot_events:
      - app_home_opened
      - app_mention
      - assistant_thread_started
      - message.channels
      - message.groups
      - message.im
      - message.mpim
  interactivity:
    is_enabled: true
  org_deploy_enabled: false
  socket_mode_enabled: true
  token_rotation_enabled: false
```

3. **Basic Information → App-Level Tokens → Generate Token and Scopes**: name it `rocket-socket`, add the `connections:write` scope and copy the `xapp-…` token. This is `SLACK_APP_TOKEN`.
4. **Install App → Install to Workspace**, then copy the **Bot User OAuth Token** (`xoxb-…`). This is `SLACK_BOT_TOKEN`.

### What each permission is for

| Permission | Why Rocket needs it |
| :--- | :--- |
| `app_mentions:read`, `message.*` events, `*:history` | Answer when mentioned, in direct messages, and read the thread it was asked in (full context, not just the triggering message). |
| `chat:write` | Post answers, the live progress message and approval buttons. |
| `assistant:write` | Show the "working on…" status and suggested prompts in Slack's assistant panel. |
| `commands` | `/rocket` and `/rocket-pair`. |
| `files:read` / `files:write` | Read files you attach (logs, screenshots); attach long answers, large diffs and screenshots. |
| `users:read`, `users:read.email` | Know **who** is speaking; link Slack accounts to Rocket Chat accounts; let the agent look people up (name, title, timezone, status; never their e-mail). |
| `usergroups:read` | Let the agent list user groups (`@backend`, `@oncall`), their members, and mention them. |
| `channels:read`, `groups:read`, `im:read`, `mpim:read`, `im:write` | List conversations and their members, and open DMs, for the agent's Slack tools (limited by your policy, below). |
| `reactions:write` | Let the agent react to a message when asked to. |
| `app_home_opened` | Draw the **Home** tab. |

> If you add scopes later, reinstall the app so Slack issues a new token.

---

## 2. Configure bots in Rocket Chat (Settings → Slack)

Bots are configured in the cockpit under **Settings → Slack**:

1. Click **New bot**.
2. Enter a display name, paste the **Bot User OAuth Token** (`xoxb-…`) and **App-Level Token** (`xapp-…`). Tokens enter write-only and are sealed with `CredentialCipher` in the database.
3. Click **Test connection** (`auth.test`) to verify the credentials and discover the bot handle and workspace.
4. Download or copy the generated **Slack app manifest** from the Connection tab if you want Rocket Chat to compute scopes and events automatically based on your triggers.

> **Upgrading from 0.3.0:** the `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` deployment variables are ignored (start-up logs a warning while they are set). Add the same Slack app as a bot here: paste its two tokens, and its existing threads start new sessions under the bot. The first bot binds its Slack workspace to your organization; a workspace bound to another organization is refused.

### Multiple bots in one workspace

An organization can run several bots in the same Slack workspace (e.g. `@RocketDev`, `@RocketSecurity`). Each bot is an independent Slack app with its own tokens and connection. Two bots mentioned in the same thread maintain separate sessions and contexts.

### Response modes per bot

* **Thread mode:** The bot responds publicly in the Slack thread, posting its live progress message and answers where everyone in the channel can see them.
* **Private session mode:** The bot marks the request with a reaction (default `white_check_mark`) and posts an ephemeral reply visible only to the requester containing an **Open session** link. The turn runs privately in Rocket Chat. On completion, an ephemeral summary is sent with a **Share in thread** button that allows the requester to post the final answer publicly.

### Brain: One Agent or Router

* **One Agent:** All messages to this bot run with the selected agent persona.
* **Router:** An AI classifier evaluates each incoming request against configured routes (each with its own description, examples, target agent persona, and response mode). Out-of-scope messages can be dismissed without creating a session. Follow-ups in a thread remain sticky to the chosen route. Classification decisions are logged under the **Activity** tab and kept 30 days.
  * **Classifier model:** the router's own model, else its classifier agent's model, else the organization's default; it must be allowed by the organization's model policy. Any model that returns structured output works; a small, fast one is enough. Classifications are billed to the organization like any model call and count against its spending caps.
  * **OpenRouter System One models** (`typesafe/…`, `inception/mercury-decide`) are called through OpenRouter's decision endpoint and return a probability per route; if that call fails the router falls back to structured output with the same model.

---

## 3. Administrator settings (Settings → Slack)

| Setting | What it does |
| :--- | :--- |
| `default_agent_persona` | The agent persona Slack sessions use. |
| `allowed_channel_types` | Where Rocket answers: public channels, private channels, direct messages, group DMs. |
| `dm_policy` | Who the agent may direct-message: nobody, linked members only, or everyone. |
| `outbound_channels` | Channels the agent may post in or react in. Empty limits it to the conversation it was invoked from. Scheduled missions may only report to these. |
| `allowed_tools` | Which tools the Slack path may use. `*` means every tool, including the tools of the MCP servers the agent can reach, except the deployment's blocked list (`SLACK_BLOCKED_TOOLS`). A list names the tools exactly, so MCP tools must be listed by name (`mcp__<server>__<tool>`). |
| `auto_link_by_verified_email` | Link Slack users to members by their verified e-mail. |
| `web_base_url` | The public address of Rocket Chat, for "Open in Rocket Chat" links (falls back to `ROCKET_PUBLIC_URL`). |
| `live_progress` | The progress message with recent steps and a Stop button while a turn runs. It is removed when the turn finishes and kept, saying why, when it is stopped or fails. Switched off, only Slack's own status line shows progress. |
| `long_output_to_file`, `long_output_chars` | Attach long answers and large diffs as files, and the length that counts as long. |
| `channel_profiles` | Per-channel behaviour (below). |

### Per-channel profiles

A channel can have its own persona, house rules, tool scope and sandbox image. Set `channel_profiles` to a JSON object keyed by channel id:

```json
{
  "C0123ABCD": {
    "agent_persona": "persona-security",
    "instructions": "Answer in French. Never run commands against production.",
    "allowed_tools": ["file_read", "grep_search", "web_search"],
    "sandbox_image": "registry.example.com/team/security-box:2"
  }
}
```

`allowed_tools` can only narrow what the organization allows. `sandbox_image` must be an image the organization permits (**Settings → Organization → Sandboxes**). Direct messages never use a channel profile.

### Who owns Slack sessions

A direct message from a linked Slack user belongs to that member. Everything else belongs to a **guest member** the Slack bridge creates on first contact, in the workspace's organization:

* `slack:<team>:<user>` for an unlinked Slack user's direct messages;
* `slack:<team>` for shared channel threads.

Guests have the `readonly` role, no password and no linked accounts, so they cannot sign in. They appear in **Settings → Organization → Users & roles**. When a Slack user links their account, their guest's sessions move to them. Sessions are never created for a user that does not exist: a turn whose session is missing fails instead.

### Safety limits (deployment-wide)

Environment variables bound what an unlinked Slack user can cost or break: `SLACK_USER_TURNS_PER_MINUTE`, `SLACK_CHANNEL_TURNS_PER_MINUTE`, `SLACK_MAX_ATTACHMENTS_PER_TURN`, `SLACK_MAX_CONSECUTIVE_FAILURES` / `SLACK_FAILURE_COOLDOWN_SECONDS` (a circuit breaker), `SLACK_ALLOWED_TOOLS` / `SLACK_BLOCKED_TOOLS` (by default `git_revert`, `git_open_pr` and `git_push` are blocked on the unauthenticated path), `SLACK_PROACTIVE_NOTIFY_AFTER_SECONDS` (a completion notice for long turns, which members can switch off) and `SLACK_HEALTH_CHECK_SECONDS` (below). The settings reference lists every key.

---

## 4. Scheduled missions that report to Slack

A scheduled mission (**Missions**) can name a **Slack channel**. When a run finishes, Rocket posts its result there with a link to the session (and the full report as a file when it is long). If the organization restricts `outbound_channels`, the channel must be one of them.

---

## 5. Reliability

* **Redelivered events are dropped.** Slack resends an event whose acknowledgement came late; Rocket remembers recent event ids and handles each once.
* **A silent socket is repaired.** A watchdog checks the connection every `SLACK_HEALTH_CHECK_SECONDS` (30) and reconnects after two failed checks in a row.
* **Observability.** `GET /v1/slack/status` reports connection state, rate-limit and quota counters, reconnects, dropped redeliveries and the age of the last event.

## 6. Troubleshooting

| Symptom | Likely cause |
| :--- | :--- |
| No reply at all | The bot is not in the channel (`/invite @Rocket`), or `message.channels` / `message.groups` events are missing. Check **Settings → Slack → Test socket**. |
| Replies only to the mention, ignores thread context | `channels:history` / `groups:history` scopes are missing. |
| No progress message, only a spinner | `chat:write` is missing, or **live progress** is switched off. |
| Long answers arrive as many messages | `files:write` is missing. |
| "Only the person who asked can answer" | Buttons are answered by the requester only; ask them, or repeat the request yourself. |
| `/rocket` does nothing | Reinstall the app from the manifest so the commands exist, then check Socket Mode is on. |
