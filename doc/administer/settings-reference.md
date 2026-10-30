# Settings Reference

> Generated from the typed registry in `packages/config-engine/src/config_engine/registry.py` by `scripts/generate_settings_reference.py`. Do not edit by hand.

Every setting declares who decides it (**Policy**), its type, its default and the runtime file that reads it. *Operator* settings come from the environment and are read-only in the product. *Organization* settings are edited by administrators at `PUT /v1/admin/settings/{domain}`; *Member* settings at `PUT /v1/settings/{domain}`. *Org default* settings may be changed by members, sessions and agents (or, where noted, a Slack bot) unless an administrator **locks** them. A *ceiling* lets those levels lower a number but never raise it, and an *operator default* is an environment variable used under the organization's value. Unknown keys, wrong-policy writes and out-of-range values are rejected with `422`.

## General (`general`)

| Setting | Policy | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `company_name` | Organization | str | empty | Organization name given to the agent as context. |
| `starter_skills` | Organization | list | `['review-diff', 'write-tests', 'fix-ci-failure', 'write-pr-description', 'investigate-bug', 'upgrade-dependency']` | The shipped instruction skills members can use (review-diff, write-tests, fix-ci-failure, write-pr-description, investigate-bug, upgrade-dependency). Remove one to turn it off; a skill of the organization's with the same name replaces it. |
| `session_privacy_default` | Organization | `private` / `shared_org` | `private` | Whether new sessions are private to their owner or shared with the organization. |
| `public_session_links` | Organization | bool | `False` | Let members create read-only public links to a session's conversation (anyone with the link can read it; turning this off disables links already issued). |
| `max_turns` | Organization (ceiling: may only be lowered); operator default `AGENT_MAX_TURNS` | min 0, max 500 | `100` | Maximum reasoning steps per turn (0: no limit, the turn runs until the agent answers, is cancelled or reaches a cost cap). It is the default for agents, and the ceiling for an agent that sets its own limit. |
| `output_offload_threshold` | Organization | min 500, max 100000 | `6000` | Character threshold above which tool execution outputs are offloaded to /workspace/.rocket/outputs with head and tail preserved in context. |
| `plan_critic` | Organization | bool | `False` | Before a person decides on a plan the agent proposes (exit_plan_mode), a 'reviewer' subagent reads it and its notes are shown on the plan card and given to the agent. Costs one subagent run per proposed plan. |
| `sso_allowed_domains` | Organization | list | empty | E-mail domains allowed to sign in (enforced when sso_enforce_domain_match is on). |
| `sso_enforce_domain_match` | Organization | bool | `False` | Block sign-in from e-mail domains outside sso_allowed_domains. |

## Models (`models`)

| Setting | Policy | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `default_model` | Organization | str | empty | Organization default model (below the agent and the user's choice). Empty uses the deployment's default (ROCKET_DEFAULT_MODEL), else the default model of the default provider: Anthropic's when its key is present, then OpenRouter, OpenAI, Gemini, DeepSeek. |
| `default_provider` | Organization | `` / `anthropic` / `openrouter` / `openai` / `gemini` / `deepseek` | empty | Whose models are the defaults (default, fast, economy and frontier) when several providers have a key. Empty picks Anthropic first, then OpenRouter, OpenAI, Gemini and DeepSeek. An explicit default, fast, economy or frontier model still wins. |
| `frontier_model` | Organization | str | empty | Model for the hardest work: agents set to the frontier tier (tier:frontier, such as the built-in Architect). Empty uses the frontier model of the turn model's provider (claude-opus-5-5 for Anthropic). |
| `context_windows` | Organization | dict | `{}` | Context-window size in tokens per model name or pattern (e.g. \{"my-provider/*": 32000\}), for models whose size is not known to the platform; drives when long conversations are compacted. |
| `context_target_tokens` | Organization | min 0, max 1e+07 | `200000` | Working context size in tokens: long conversations are pruned and compacted to stay under it even when the model's window is larger (requests stay fast and focused). 200000 (the default) trades that window for quicker, cheaper steps on very long sessions; 0 uses the model's whole window without a working cap. |
| `provider_preferences` | Organization | dict | `{}` | OpenRouter provider preferences per model name or pattern, e.g. \{"openrouter/deepseek/*": \{"order": ["deepseek"], "allow_fallbacks": false\}\}. Pinning a model to one provider keeps its prompt cache warm across requests. |
| `fallback_models` | Organization | list | empty | Ordered fail-over models used on transient provider errors (allowlisted). Empty uses the same tier through another provider that has a key, when automatic_fallbacks is on. |
| `automatic_fallbacks` | Organization | bool | `True` | When fallback_models is empty, fail over to the same tier through another provider that has a key (claude-sonnet-5-5 through OpenRouter when the Anthropic API errors, for example). |
| `allowed_models` | Organization | list | empty | fnmatch patterns of models members may use; empty allows all. |
| `forbidden_models` | Organization | list | empty | fnmatch patterns of models that are never allowed. |
| `custom_models` | Organization | list | empty | Extra model ids offered in the picker, e.g. a private vLLM or Ollama model (`ollama/llama3.1`, `hosted_vllm/qwen2.5-coder`); pair with an organization key that sets the endpoint's base URL. |
| `temperature` | Org default, member override | min 0, max 2 | `0.2` | Default sampling temperature for models that do not reason (an agent persona may override it). Reasoning models get the provider's own temperature unless the organization or the agent sets one explicitly (several reasoning APIs reject it), and extended thinking on an Anthropic model always takes the provider's. |
| `default_reasoning_effort` | Org default, member override | `` / `none` / `minimal` / `low` / `medium` / `high` / `xhigh` / `max` | `medium` | How much the model reasons before it acts when the agent does not choose: less is faster and cheaper, and keeps an agent from deliberating for minutes over a decision it could test. none turns reasoning off. An OpenRouter model that does not take the level gets the nearest one it does. The default (medium) applies to the reasoning models the platform knows; a value set here is also sent to other models. Empty leaves it to the provider. |
| `reasoning_step_limit_tokens` | Organization | min 0, max 200000 | `8000` | Reasoning tokens (estimated) a step may spend before it acts. Past it the step is stopped once and asked again with reasoning off and a note to decide, so a model that deliberates for minutes over a choice it could test gets on with it. It never ends the turn. 0 turns the limit off. |
| `max_tokens` | Organization | min 1, max 200000 | `32000` | Most completion tokens per call: each call asks for the smaller of this and the model's own maximum output (16384 for a model the platform does not know). Reasoning models spend output on thinking, so a low value cuts long patches. The matching entry of output_tokens, then an agent's own limit, win over it. |
| `output_tokens` | Organization | dict | `{}` | Maximum completion tokens per model name or pattern (e.g. \{'*': 64000, 'claude*': 64000\}), overriding the catalogue default ceiling. |
| `routing_mode` | Organization | `frontier` / `balanced` / `economy` | `balanced` | frontier = one model for all work, balanced = the fast model for auxiliary work (titles, suggestions, summaries), economy = the economy model everywhere. |
| `fast_model` | Organization | str | empty | Model for auxiliary work (titles, suggestions, summaries) in balanced mode. Empty uses the fast model of the turn model's provider (claude-haiku-5-5 for Anthropic), or the turn model itself for a provider the platform has no tiers for. |
| `economy_model` | Organization | str | empty | Model used for all work in economy mode (it replaces the default model everywhere while the mode is economy). Empty uses the economy model of the turn model's provider. |
| `context_prune_percent` | Organization | min 30, max 95 | `70` | Share of the working context at which old tool results are pruned to short receipts. |
| `context_compact_percent` | Organization | min 40, max 98 | `85` | Share of the working context at which the conversation is compacted into a summary (kept above the prune threshold). |
| `member_keys` | Organization | `off` / `fallback` / `preferred` | `preferred` | How members' own provider keys are used, for members allowed to bring their own (the bring_own_keys capability): off = never, fallback = only for a provider the organization has no key for, preferred = before the organization's key. |
| `provider_allowlist` | Organization | list | empty | Model providers that may receive organization data; empty allows all. |
| `zero_retention` | Organization | bool | `False` | Ask providers not to retain prompts or completions. Enforced for OpenRouter models; a call to a provider that cannot honour it is refused rather than sent. |
| `region` | Organization | str | empty | Pin inference to a provider region. Enforced for Vertex AI and Bedrock models; a call to any other provider is refused rather than sent. |

## Turn and task limits (`budgets`)

| Setting | Policy | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `max_turn_cost_usd` | Organization | min 0 | `15.0` | Spend cap for one turn in USD, its subagents included (0 = no cap). It stops a runaway turn without limiting steps or time: the turn stops with an explanation when it is reached, and the session and its work are kept. |
| `goal_max_total_tokens` | Organization | min 0 | `0` | Most tokens a long task may use across its automatic continuation turns (0 = no cap). Continuation stops when it is reached. |
| `goal_max_wall_time_minutes` | Organization | min 0, max 10080 | `240` | Longest a long task may run across its automatic continuation turns, in minutes (0 = no cap). |
| `max_continuations` | Organization | min 0, max 100 | `10` | Automatic continuation turns allowed for one task whose plan is unfinished when a turn reaches its step limit (0 disables continuation). |

## Sandboxes (`sandboxes`)

| Setting | Policy | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `workspace_release_days` | Organization | min 0, max 365 | `7` | Days an idle session keeps its workspace volume (the repositories it cloned) after its sandbox stopped; then the volume is deleted and the next turn starts blank. 0 keeps it as long as the session exists. A sandbox type can set its own. |
| `default_image` *(deprecated)* | Organization | str | `ghcr.io/rocket-chat/sandbox:0.7.2` | Seeds the sandbox catalogue the first time the organization uses it (the default entry); afterwards manage images under Settings > Organization > Sandboxes. The default is the devbox image with git, Python, Node and common dev tools. |
| `allowed_images` *(deprecated)* | Organization | list | empty | Legacy fnmatch patterns of extra images sessions may use. Literal images seed the sandbox catalogue; prefer adding entries to the catalogue, which also declares what each image can do. |
| `cpu_limit` | Organization | min 0.1 | `2.0` | CPU cores per sandbox when its sandbox type sets none. |
| `memory_limit` | Organization | str | `4Gi` | Memory limit per sandbox (e.g. 4Gi) when its sandbox type sets none. |
| `disk_size` | Organization | str | `30Gi` | Workspace volume size per session (e.g. 30Gi) when its sandbox type sets none: room for a monorepo, its dependencies and the language caches. Applies to workspaces created afterwards (Kubernetes). |
| `allow_terminal` | Organization | bool | `True` | Let members open an interactive shell in their session's running sandbox from the cockpit (it runs with the same limits, network rules and user as the agent). |
| `max_running_sandboxes` | Organization | min 0, max 10000 | `0` | Most sandboxes the organization may have running at once; a new session waits for an idle sandbox to be hibernated instead of starting another. 0 means no limit. |
| `max_background_processes` | Organization | min 1, max 50 | `5` | Most background processes (dev servers, watchers, long builds) one session may run at once; the agent is told to stop one before starting another. |
| `process_keepalive_minutes` | Organization | min 0, max 10080 | `120` | How long a sandbox with a running background process is kept out of idle hibernation, renewed whenever the process is used or its tab is open. 0 hibernates it on the normal idle timer (which ends the process). |
| `run_repo_setup` | Organization | bool | `True` | After the agent clones a repository into the workspace, run its setup once: .rocket/setup.sh, or the onCreateCommand / postCreateCommand of its .devcontainer/devcontainer.json. It runs in the sandbox as the sandbox user with git credentials removed from its environment. |
| `require_non_root` | Organization | bool | `False` | Run sandbox processes as an unprivileged user (uid 1000) with all Linux capabilities dropped. The image must support it (the Rocket Chat devbox does). |

## Git & GitHub (`github`)

| Setting | Policy | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `publish_as` | Org default, member override | `member` / `bot` | `member` | Who publishes the agent's work: member = pull requests opened with the member's linked GitHub account (the commits credit them as co-author), bot = the GitHub App opens them. Choosing bot needs the publish_as_bot capability. |
| `signing` | Organization | `github` / `member_gpg` / `none` | `github` | github: commits are created through GitHub's API so GitHub signs them (verified, made by the GitHub App); member_gpg: a member who imported a GPG key commits as themselves, signed with it, and everyone else gets github; none: unsigned. |
| `bot_author_name` | Organization; operator default `GIT_BOT_NAME` | str | `RocketChat Bot` | Name of the bot identity used for bot commits. |
| `bot_author_email` | Organization; operator default `GIT_BOT_EMAIL` | str | `bot@rocketchat.internal` | E-mail of the bot identity used for bot commits. |
| `default_branch` | Organization; operator default `GIT_DEFAULT_BRANCH` | str | `main` | Base branch for pull requests when a repository's own default branch is not known (each cloned repository's own default branch is used first). |
| `app_token_permissions` | Organization; operator default `GITHUB_APP_DEFAULT_PERMISSIONS` | dict | `{'contents': 'read', 'pull_requests': 'read', 'checks': 'read', 'actions': 'read', 'statuses': 'read', 'issues': 'read'}` | Permissions requested for the GitHub App token the sandbox receives. Always lowered to read: add reads such as actions, checks or statuses to let the agent follow CI; writing goes through git_push with a token that never enters the sandbox. |
| `app_token_target_repositories` | Organization | str | `all` | Repositories the GitHub App acts on for the agent, reading and changing: "all" or a comma-separated list of owner/name. Elsewhere the member's linked account is used. A bare name (deprecated) matches that repository under every owner. |

## MCP policy (`mcp`)

| Setting | Policy | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `allowed_transports` | Organization | list | `['sse', 'http', 'stdio']` | MCP transports administrators may register. |
| `user_allowed_transports` | Organization | list | `['sse', 'http']` | MCP transports members may register for themselves. |
| `max_user_servers` | Organization | min 0, max 100 | `10` | Maximum personal MCP servers per member. |

## Slack (`slack`)

| Setting | Policy | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `allowed_channel_types` | Org default, Slack bot override | list | `['public', 'private', 'im', 'mpim']` | Slack conversation types the assistant answers in. |
| `auto_link_by_verified_email` | Organization | bool | `False` | Link a Slack user to a Rocket account by their verified e-mail automatically. |
| `dm_policy` | Org default, Slack bot override | `none` / `paired` / `all` | `paired` | Who the agent may direct-message. |
| `outbound_channels` | Org default, Slack bot override | list | empty | Channel ids the agent may post or react in; empty limits it to the conversation it was invoked from. |
| `web_base_url` | Organization | str | empty | Public address of the Rocket Chat web app (for example https://rocket.example.com); every Slack thread links to its session there. Empty falls back to ROCKET_PUBLIC_URL. |
| `restrict_linked_members` | Organization | bool | `False` | Also block the deployment's restricted tools (by default `git_push`, `git_open_pr` and `git_revert`) for members who linked their Slack account, in their DMs and private requests. Off, those requests run with the member's own access, as in the web app; shared channel threads and unlinked users are always restricted. |
| `live_progress` | Org default, Slack bot override | bool | `True` | Stream each tool call into the reply as a step while a turn runs, then write the answer into the same message; off, the answer is posted when the turn ends. |
| `long_output_to_file` | Org default, Slack bot override | bool | `True` | Attach long answers and large diffs as Slack files instead of splitting them into many messages. |
| `long_output_chars` | Org default, Slack bot override | min 500, max 20000 | `3500` | Answers longer than this many characters are summarised in the thread and attached in full as a file. |
| `channel_profiles` | Org default, Slack bot override | dict | `{}` | Per-channel behaviour, keyed by channel id: \{agent_persona, instructions, allowed_tools, sandbox_image, response_mode, repository, branch\}. A channel with a profile uses its persona and house rules; allowed_tools narrows the organization's tool scope for that channel; response_mode ('thread' or 'private') overrides the bot's; repository and branch are stored for the channel's sessions. |
| `allowed_tools` | Org default, Slack bot override | list | `['*']` | Tools the Slack path may use ('*' = every tool except SLACK_BLOCKED_TOOLS). |

## Governance & data controls (`governance`)

| Setting | Policy | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `hooks` | Organization | dict | `{}` | Commands run in the sandbox around tool calls: \{pre_tool: [\{match, command\}], post_tool: [...]\}. A failing pre_tool command blocks the call (a policy gate); a failing post_tool command is reported to the agent. match is a tool-name pattern. |
| `auto_resume_turns` | Organization | bool | `True` | Resume a turn automatically when the server running it restarted mid-task (the agent is told it was interrupted and continues from the saved conversation). |
| `approval_policy` | Organization | dict | `{}` | Risk levels to auto-approve or deny: \{auto_approve: [...], deny: [...]\}. |
| `remembered_approvals` | Organization | `session_and_repository` / `session` / `off` | `session_and_repository` | Whether members may answer an approval once for good: session_and_repository ("Allow for this session" and "Always for this command in this repository"), session (only the first) or off. Never applies to irreversible actions, to risk levels the approval policy denies or auto-approves, or while the permission mode is locked. Switching it off stops existing rules from applying. |
| `question_timeout_seconds` | Organization | min 1 | unset | How long a question the agent asks (ask_question) waits for an answer. Past it, the question closes unanswered and the agent continues with its recommended option or its best judgement, stating the assumption. Empty waits until someone answers; set it for sessions nobody watches, such as ones driven through the API. |
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

| Setting | Policy | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `enabled` | Organization | bool | `True` | Enable long-term memory. |
| `scope_allowlist` | Organization | list | `['user', 'org', 'session']` | Memory scopes the agent may write. |
| `kind_allowlist` | Organization | list | `['fact', 'preference', 'decision', 'convention']` | Memory kinds the agent may write. |
| `top_k` | Organization | min 1, max 50 | `5` | Memories recalled per turn. |
| `dedupe_similarity` | Organization | min 0, max 1 | `0.95` | Cosine similarity above which a new memory updates an existing one. |
| `decay_half_life_days` | Organization | min 1 | `90.0` | Half-life of a memory's ranking weight since last use. |
| `embedding_model` | Organization | str | empty | Embedding model for semantic recall (1536 dimensions, called with the deployment's key). Empty picks one by provider: OpenAI's text-embedding-3-small, else Gemini's gemini-embedding-001. With neither key, recall falls back to keyword matching and Setup health says so. |

## Access (capabilities by role) (`access`)

| Setting | Policy | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `create_personal_agents` | Organization | `owner` / `admin` / `member` / `viewer` | `['owner', 'admin', 'member']` | Roles that may create personal agents. |
| `bring_own_keys` | Organization | `owner` / `admin` / `member` / `viewer` | `['owner', 'admin', 'member']` | Roles that may store their own model provider keys (used as Models > Members' own keys says). |
| `use_bypass_mode` | Organization | `owner` / `admin` / `member` / `viewer` | empty | Roles that may run sessions in bypass mode, where nothing waits for approval, not even the irreversible actions every other mode holds. Asking for bypass without it runs in ask mode. |
| `register_mcp` | Organization | `owner` / `admin` / `member` / `viewer` | `['owner', 'admin', 'member']` | Roles that may register personal MCP servers (organization and command servers stay administrator-only). |
| `publish_as_bot` | Organization | `owner` / `admin` / `member` / `viewer` | `['owner', 'admin']` | Roles that may choose to publish as the GitHub App (Git > Publish as: bot). |
| `manage_spend_limits` | Organization | `owner` / `admin` / `member` / `viewer` | `['owner', 'admin']` | Roles that may create and change spend limits. |

## Member preferences (`user_preferences`)

| Setting | Policy | Type / values | Default | Description |
| :-- | :-- | :-- | :-- | :-- |
| `permission_mode` | Org default, member override | `plan` / `ask` / `auto_edit` / `auto` / `bypass` | `auto` | How much of the agent's work waits for a person: plan (read-only), ask (approve every edit, command and publish), auto_edit (approve commands and publish), auto (only irreversible actions wait: merging a pull request, deleting on GitHub, a hard revert, wiping the workspace), bypass (nothing waits). The organization sets the default and can lock it; otherwise members and sessions choose. |
| `full_name` | Member | str | empty | Display name; the commit author name when no GitHub account is linked and no git name is set (a linked account's username is used otherwise). |
| `git_author_name` | Member | str | empty | Commit author name; empty uses the linked GitHub account's username. |
| `git_author_email` | Member | str | empty | Commit author e-mail; empty uses the linked GitHub account's primary e-mail, then its noreply address. |
| `git_branch_prefix` | Org default, member override | str | `rocket/` | Prefix the agent uses for the branches it creates. |
| `preferred_model` | Org default, member override | str | empty | Preferred model (below the agent, above the organization default); empty uses the organization default. |
| `suggest_next_questions` | Org default, member override | bool | `True` | Suggest a probable next prompt after each turn. |
| `turn_notifications` | Member | bool | `False` | Show a browser notification when a turn ends, or waits for an approval or an answer, while its tab is in the background. The browser asks for permission once. |
| `notification_sound` | Member | bool | `False` | Play a short sound when a turn ends, or waits for an approval or an answer, while its tab is in the background. |
