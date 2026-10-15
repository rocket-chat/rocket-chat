# Settings Reference

> Generated from the typed registry in `packages/config-engine/src/config_engine/registry.py` by `scripts/generate_settings_reference.py`. Do not edit by hand.

Every setting declares who may write it (**Scope**), its type, its default and the runtime file that reads it. Organization settings are edited by administrators at `PUT /v1/admin/settings/{domain}`; member settings at `PUT /v1/settings/{domain}`. Settings with scope *Org default, member override* can be **locked** by an administrator so members cannot change them. Unknown keys, wrong-scope writes and out-of-range values are rejected with `422`.

## General (`general`)

| Setting | Scope | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `company_name` | Organization | str | empty | Organization name given to the agent as context. |
| `session_privacy_default` | Organization | `private` / `shared_org` | `private` | Whether new sessions are private to their owner or shared with the organization. |
| `public_session_links` | Organization | bool | `False` | Let members create read-only public links to a session's conversation (anyone with the link can read it; turning this off disables links already issued). |
| `max_turns` | Organization | min 1, max 200 | `60` | Maximum reasoning steps per turn. It is the default for agents, and the ceiling for an agent that sets its own limit. |
| `sso_allowed_domains` | Organization | list | empty | E-mail domains allowed to sign in (enforced when sso_enforce_domain_match is on). |
| `sso_enforce_domain_match` | Organization | bool | `False` | Block sign-in from e-mail domains outside sso_allowed_domains. |

## Models (`models`)

| Setting | Scope | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `default_model` | Organization | str | `openrouter/deepseek/deepseek-v4.1-flash` | Organization default model (below the agent and the user's choice). |
| `context_windows` | Organization | dict | `{}` | Context-window size in tokens per model name or pattern (e.g. \{"my-provider/*": 32000\}), for models whose size is not known to the platform; drives when long conversations are compacted. |
| `fallback_models` | Organization | list | empty | Ordered fail-over models used on transient provider errors (opt-in, allowlisted). |
| `allowed_models` | Organization | list | empty | fnmatch patterns of models members may use; empty allows all. |
| `forbidden_models` | Organization | list | empty | fnmatch patterns of models that are never allowed. |
| `custom_models` | Organization | list | empty | Extra model ids offered in the picker, e.g. a private vLLM or Ollama model (`ollama/llama3.1`, `hosted_vllm/qwen2.5-coder`); pair with an organization key that sets the endpoint's base URL. |
| `temperature` | Organization | min 0, max 2 | `0.2` | Default sampling temperature (an agent persona may override it). |
| `max_tokens` | Organization | min 1, max 200000 | `8192` | Default maximum completion tokens per model call. |

## budgets (`budgets`)

| Setting | Scope | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `monthly_budget_usd` | Organization | min 0 | `0.0` | Monthly spend cap for the whole organization in USD (0 = no cap). Spend on a member's own provider key does not count. Reaching it stops new model calls. |
| `member_monthly_budget_usd` | Organization | min 0 | `0.0` | Monthly spend cap per member in USD (0 = no cap). |
| `session_budget_usd` | Organization | min 0 | `0.0` | Spend cap per session in USD (0 = no cap); keeps a runaway agent loop cheap. |
| `warn_at_percent` | Organization | min 1, max 100 | `80` | Warn the member in the session when a cap is this far used. |
| `byok_policy` | Organization | `platform_only` / `org_key` / `user_keys` | `user_keys` | Whose provider keys may be used: platform keys only, the organization's own key, or members' own keys (falling back to the organization's, then the platform's). |

## Cost routing (`routing`)

| Setting | Scope | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `mode` | Organization | `frontier` / `balanced` / `economy` | `frontier` | frontier = one model for all work, balanced = cheap model for auxiliary work, economy = cheapest model everywhere. |
| `fast_model` | Organization | str | empty | Model for auxiliary work (titles, suggestions, summaries) in balanced mode. |
| `economy_model` | Organization | str | empty | Model used for all work in economy mode. |

## Sandboxes (`sandboxes`)

| Setting | Scope | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `default_image` | Organization | str | `ghcr.io/rocket-chat/sandbox:latest` | Seeds the sandbox catalogue the first time the organization uses it (the default entry); afterwards manage images under Settings > Organization > Sandboxes. The default is the devbox image with git, Python, Node and common dev tools. |
| `allowed_images` | Organization | list | empty | Legacy fnmatch patterns of extra images sessions may use. Literal images seed the sandbox catalogue; prefer adding entries to the catalogue, which also declares what each image can do. |
| `cpu_limit` | Organization | min 0.1 | `2.0` | CPU cores per sandbox. |
| `memory_limit` | Organization | str | `4Gi` | Memory limit per sandbox (e.g. 4Gi). |
| `allow_terminal` | Organization | bool | `True` | Let members open an interactive shell in their session's running sandbox from the cockpit (it runs with the same limits, network rules and user as the agent). |
| `max_running_sandboxes` | Organization | min 0, max 10000 | `0` | Most sandboxes the organization may have running at once; a new session waits for an idle sandbox to be hibernated instead of starting another. 0 means no limit. |
| `max_background_processes` | Organization | min 1, max 50 | `5` | Most background processes (dev servers, watchers, long builds) one session may run at once; the agent is told to stop one before starting another. |
| `process_keepalive_minutes` | Organization | min 0, max 10080 | `120` | How long a sandbox with a running background process is kept out of idle hibernation, renewed whenever the process is used or its tab is open. 0 hibernates it on the normal idle timer (which ends the process). |
| `run_repo_setup` | Organization | bool | `True` | After the agent clones a repository into the workspace, run its setup once: .rocket/setup.sh, or the onCreateCommand / postCreateCommand of its .devcontainer/devcontainer.json. It runs in the sandbox as the sandbox user with git credentials removed from its environment. |
| `require_non_root` | Organization | bool | `False` | Run sandbox processes as an unprivileged user (uid 1000) with all Linux capabilities dropped. The image must support it (the Rocket Chat devbox does). |

## Git & GitHub (`github`)

| Setting | Scope | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `commit_authorship_policy` | Organization | `co_authored` / `user_only` / `bot_only` | `co_authored` | co_authored = bot commits with the member as Co-authored-by, user_only = commits as the member, bot_only = commits as the bot. |
| `pr_creation_policy` | Organization | `as_user` / `as_bot` | `as_user` | Open pull requests with the member's token (as_user) or the GitHub App (as_bot). |
| `commit_signing_mode` | Organization | `github_app` / `gpg_key` / `none` | `github_app` | github_app: commits are created through GitHub's API so GitHub signs them (verified, made by the GitHub App); gpg_key: members who imported a GPG key and chose to commit as themselves sign with it, everyone else gets github_app; none: unsigned. |
| `bot_author_name` | Organization | str | `RocketChat Bot` | Name of the bot identity used for bot commits. |
| `bot_author_email` | Organization | str | `bot@rocketchat.internal` | E-mail of the bot identity used for bot commits. |
| `default_branch` | Organization | str | `main` | Base branch for pull requests. |
| `app_token_permissions` | Organization | dict | `{'contents': 'read', 'pull_requests': 'read'}` | Permissions requested for the GitHub App token the sandbox receives. Always lowered to read: add reads such as actions, checks or statuses to let the agent follow CI; writing goes through git_push with a token that never enters the sandbox. |
| `app_token_target_repositories` | Organization | str | `all` | Repositories the GitHub App acts on for the agent, reading and changing: "all" or a comma-separated list. Elsewhere the member's linked account is used. |

## MCP policy (`mcp`)

| Setting | Scope | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `allowed_transports` | Organization | list | `['sse', 'http', 'stdio']` | MCP transports administrators may register. |
| `user_allowed_transports` | Organization | list | `['sse', 'http']` | MCP transports members may register for themselves. |
| `max_user_servers` | Organization | min 0, max 100 | `10` | Maximum personal MCP servers per member. |

## Slack (`slack`)

| Setting | Scope | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `default_agent_persona` | Organization | str | empty | Agent persona used by Slack sessions. |
| `allowed_channel_types` | Organization | list | `['public', 'private', 'im', 'mpim']` | Slack conversation types the assistant answers in. |
| `auto_link_by_verified_email` | Organization | bool | `False` | Link a Slack user to a Rocket account by their verified e-mail automatically. |
| `dm_policy` | Organization | `none` / `paired` / `all` | `paired` | Who the agent may direct-message. |
| `outbound_channels` | Organization | list | empty | Channel ids the agent may post or react in; empty limits it to the conversation it was invoked from. |
| `web_base_url` | Organization | str | empty | Public address of the Rocket Chat web app (for example https://rocket.example.com); every Slack thread links to its session there. Empty falls back to ROCKET_PUBLIC_URL. |
| `live_progress` | Organization | bool | `True` | Show one live progress message per turn, updated in place, with a Stop button. |
| `long_output_to_file` | Organization | bool | `True` | Attach long answers and large diffs as Slack files instead of splitting them into many messages. |
| `long_output_chars` | Organization | min 500, max 20000 | `3500` | Answers longer than this many characters are summarised in the thread and attached in full as a file. |
| `channel_profiles` | Organization | dict | `{}` | Per-channel behaviour, keyed by channel id: \{agent_persona, instructions, allowed_tools, sandbox_image\}. A channel with a profile uses its persona and house rules; allowed_tools narrows the organization's tool scope for that channel. |
| `allowed_tools` | Organization | list | `['*']` | Tools the Slack path may use ('*' = every tool except SLACK_BLOCKED_TOOLS). |

## Governance & data controls (`governance`)

| Setting | Scope | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `hooks` | Organization | dict | `{}` | Commands run in the sandbox around tool calls: \{pre_tool: [\{match, command\}], post_tool: [...]\}. A failing pre_tool command blocks the call (a policy gate); a failing post_tool command is reported to the agent. match is a tool-name pattern. |
| `allow_bypass_permissions` | Organization | bool | `False` | Let members run sessions in bypass mode, where no action waits for approval. A member or session that asks for bypass while this is off runs in ask mode. |
| `auto_resume_turns` | Organization | bool | `True` | Resume a turn automatically when the server running it restarted mid-task (the agent is told it was interrupted and continues from the saved conversation). |
| `approval_policy` | Organization | dict | `{}` | Risk levels to auto-approve or deny: \{auto_approve: [...], deny: [...]\}. |
| `provider_allowlist` | Organization | list | empty | Model providers that may receive organization data; empty allows all. |
| `zero_retention` | Organization | bool | `False` | Ask providers not to retain prompts or completions. |
| `region` | Organization | str | empty | Pin inference to a provider region. |
| `egress_allowlist` | Organization | list | empty | Sandbox egress destinations: CIDR blocks, IPs, host names or *.suffix patterns; empty means unrestricted. Docker sandboxes reach only these through an allow-listing proxy (checked after DNS resolution); Kubernetes resolves host names when the sandbox starts and enforces the addresses with a NetworkPolicy. |
| `block_internal_subnets` | Organization | bool | `False` | Block sandbox access to private and link-local ranges. |
| `redaction_enabled` | Organization | bool | `True` | Redact secrets from tool output, traces and logs. |
| `pii_redaction_enabled` | Organization | bool | `False` | Also redact personal data (e-mail addresses, phone numbers, ...). |
| `redaction_patterns` | Organization | list | empty | Additional regular expressions to redact. |
| `session_retention_days` | Organization | min 1 | unset | Delete session data older than this many days; empty keeps it. |
| `trace_retention_days` | Organization | min 1 | unset | Delete trace data older than this many days; empty keeps it. |
| `memory_retention_days` | Organization | min 1 | unset | Delete memory data older than this many days; empty keeps it. |
| `attachment_retention_days` | Organization | min 1 | unset | Delete attachment data older than this many days; empty keeps it. |
| `audit_retention_days` | Organization | min 1 | unset | Delete audit data older than this many days; empty keeps it. |

## Long-term memory (`memory`)

| Setting | Scope | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `enabled` | Organization | bool | `True` | Enable long-term memory. |
| `scope_allowlist` | Organization | list | `['user', 'org', 'session']` | Memory scopes the agent may write. |
| `kind_allowlist` | Organization | list | `['fact', 'preference', 'decision', 'convention']` | Memory kinds the agent may write. |
| `top_k` | Organization | min 1, max 50 | `5` | Memories recalled per turn. |
| `dedupe_similarity` | Organization | min 0, max 1 | `0.95` | Cosine similarity above which a new memory updates an existing one. |
| `decay_half_life_days` | Organization | min 1 | `90.0` | Half-life of a memory's ranking weight since last use. |

## Member preferences (`user_preferences`)

| Setting | Scope | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `permission_mode` | Org default, member override | `plan` / `ask` / `auto_edit` / `auto` / `bypass` | `auto` | How much of the agent's work waits for a person: plan (read-only), ask (approve every edit, command and publish), auto_edit (approve commands and publish), auto (the agent asks when it judges an action risky), bypass (approve everything). The organization sets the default and can lock it; otherwise members and sessions choose. |
| `full_name` | Org default, member override | str | empty | Display name; the commit author name when no GitHub account is linked and no git name is set (a linked account's username is used otherwise). |
| `git_author_name` | Org default, member override | str | empty | Commit author name; empty uses the linked GitHub account's username. |
| `git_author_email` | Org default, member override | str | empty | Commit author e-mail; empty uses the linked GitHub account's primary e-mail, then its noreply address. |
| `commit_as_me` | Org default, member override | bool | `False` | Commit as yourself, signed with the GPG key you imported in Git & version control. Applies only when the organization's commit signing mode is gpg_key; otherwise the organization's authorship policy decides. |
| `git_branch_prefix` | Org default, member override | str | `feat/rocket-` | Prefix the agent uses for the branches it creates. |
| `slack_notifications_enabled` | Org default, member override | bool | `True` | Receive Slack completion notices for long-running turns. |
| `preferred_model` | Org default, member override | str | empty | Preferred model (below the agent, above the organization default); empty uses the organization default. |
| `suggest_next_questions` | Org default, member override | bool | `True` | Suggest a probable next prompt after each turn. |
