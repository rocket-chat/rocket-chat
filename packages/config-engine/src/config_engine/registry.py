"""Typed settings registry: every key has an owner scope, a type and a runtime consumer.

Settings used to be untyped ``dict[str, Any]`` defaults with no record of who may write a
key or which code reads it, which is how ~35 keys came to be editable in the UI while doing
nothing. The registry is the single declaration of that contract:

* ``policy`` says who decides the key (``config_engine.policy``): ``instance`` (the operator,
  through the environment), ``org`` (administrators, ``PUT /admin/settings``), ``org_default``
  (an organization default that members, sessions and presets may override unless the key is
  locked) or ``member`` (the member, ``PUT /settings``). ``ceiling`` lets the levels below
  only lower a number; ``env`` names the variables that hold the operator's value.
* ``consumer`` names the repository file whose runtime code reads the key. A contract test
  fails if a key is declared without a consumer, if the consumer does not mention the key,
  or if runtime code reads an undeclared key.
* ``validate`` coerces and checks values, so a typo is a ``422`` with field names rather
  than a silently stored dead setting.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from .environment import env_value
from .model_defaults import PROVIDER_PREFERENCE
from .policy import Policy
from .sandbox_images import DEFAULT_SANDBOX_IMAGE

Kind = Literal["bool", "int", "float", "str", "enum", "list", "dict"]
Writer = Literal["org", "member"]


@dataclass(frozen=True)
class SettingSpec:
    """Declaration of one setting."""

    key: str
    kind: Kind
    default: Any
    policy: Policy
    consumer: str
    description: str
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    secret: bool = False
    # Shown in forms instead of the key's capitalised name when that would mislead.
    label: str = ""
    # What the number counts ("minutes", "USD"), shown beside the input.
    unit: str = ""
    # Rarely changed: forms fold these under "Advanced".
    advanced: bool = False
    # Superseded: still honoured, hidden from forms unless set, and flagged in the reference.
    deprecated: bool = False
    # Lower levels (members, sessions, agents) may lower this number, never raise it.
    ceiling: bool = False
    # Under ``org_default``: whether members may choose their own value, or only presets (a
    # Slack bot overriding the organization's Slack policy) may.
    member_override: bool = True
    # Environment variables holding the operator's value: the whole value for ``instance``,
    # the default under the organization's for any other policy.
    env: tuple[str, ...] = ()


@dataclass(frozen=True)
class FieldError:
    """A rejected setting: the key and why."""

    key: str
    message: str


@dataclass
class ValidationResult:
    values: dict[str, Any] = field(default_factory=dict)
    errors: list[FieldError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# The shipped instruction skills (``api.services.starter_content``), all on by default.
STARTER_SKILLS: tuple[str, ...] = (
    "review-diff", "write-tests", "fix-ci-failure", "write-pr-description", "investigate-bug",
    "upgrade-dependency",
)  # fmt: skip


def _spec(
    key: str,
    kind: Kind,
    default: Any,
    policy: Policy,
    consumer: str,
    description: str,
    **extra: Any,
) -> SettingSpec:
    return SettingSpec(key, kind, default, policy, consumer, description, **extra)


_PROVISIONING = "apps/api/src/api/turns/provisioning.py"
_TURN_CALLBACKS = "apps/api/src/api/turns/callbacks.py"
_SANDBOX_CATALOGUE = "apps/api/src/api/services/sandbox_catalogue.py"
_TURN_ROUTING = "apps/api/src/api/turns/routing.py"
_TURN_PROMPT = "apps/api/src/api/turns/prompt.py"
_TURN_LIMITS = "apps/api/src/api/services/turn_limits.py"
_TURN_FINALISE = "apps/api/src/api/turns/finalise.py"
_MCP_SERVERS = "apps/api/src/api/routers/mcp_servers.py"
_SESSION_CRUD = "apps/api/src/api/routers/session_crud.py"
_SHARING = "apps/api/src/api/routers/sharing.py"
_ROUTES = "apps/api/src/api/routers/settings_api.py"
_TURN = "apps/api/src/api/services/turn_service.py"
_GIT_CREDS = "apps/api/src/api/services/git_credentials.py"
_GIT_IDENTITY = "apps/api/src/api/services/github_identity.py"
_GIT_SIGNING = "apps/api/src/api/services/git_signing.py"
_BUDGETS = "apps/api/src/api/services/budgets.py"
_TURN_CONTINUATION = "apps/api/src/api/turns/continuation.py"
_DISPATCHER = "packages/agent-core/src/agent_core/dispatcher.py"
_MODEL_CREDENTIALS = "apps/api/src/api/services/model_credentials.py"
_TURN_RUNNER = "apps/api/src/api/turns/runner.py"
_CAPABILITIES = "apps/api/src/api/auth/capabilities.py"
_MODEL_DEFAULTS = "apps/api/src/api/services/model_defaults.py"
_MEMORY = "apps/api/src/api/services/memory.py"

# The roles a member can hold, highest first (``api.auth.capabilities``).
ROLES = ("owner", "admin", "member", "viewer")
# Read by the cockpit, which alerts the member; the API only stores the choice.
_TURN_ATTENTION = "apps/web/src/lib/turn-attention.ts"

_DOMAINS: dict[str, tuple[SettingSpec, ...]] = {
    "general": (
        _spec(
            "company_name",
            "str",
            "",
            "org",
            _TURN_PROMPT,
            "Organization name given to the agent as context.",
        ),
        _spec(
            "starter_skills",
            "list",
            list(STARTER_SKILLS),
            "org",
            "apps/api/src/api/services/skills_lookup.py",
            "The shipped instruction skills members can use (review-diff, write-tests, "
            "fix-ci-failure, write-pr-description, investigate-bug, upgrade-dependency). Remove "
            "one to turn it off; a skill of the organization's with the same name replaces it.",
            label="Starter skills",
        ),
        _spec(
            "session_privacy_default",
            "enum",
            "private",
            "org",
            _SESSION_CRUD,
            "Whether new sessions are private to their owner or shared with the organization.",
            choices=("private", "shared_org"),
        ),
        _spec(
            "public_session_links",
            "bool",
            False,
            "org",
            _SHARING,
            "Let members create read-only public links to a session's conversation "
            "(anyone with the link can read it; turning this off disables links already issued).",
        ),
        _spec(
            "max_turns",
            "int",
            100,
            "org",
            _TURN_LIMITS,
            "Maximum reasoning steps per turn (0: no limit, the turn runs until the agent answers, "
            "is cancelled or reaches a cost cap). It is the default for agents, and the ceiling "
            "for an agent that sets its own limit.",
            label="Max steps per turn",
            minimum=0,
            maximum=500,
            ceiling=True,
            env=("AGENT_MAX_TURNS", "ROCKET_MAX_TURNS", "MAX_TURNS"),
        ),
        _spec(
            "output_offload_threshold",
            "int",
            6000,
            "org",
            _DISPATCHER,
            "Character threshold above which tool execution outputs are offloaded to "
            "/workspace/.rocket/outputs with head and tail preserved in context.",
            minimum=500,
            maximum=100000,
        ),
        _spec(
            "plan_critic",
            "bool",
            False,
            "org",
            _TURN,
            "Before a person decides on a plan the agent proposes (exit_plan_mode), a 'reviewer' "
            "subagent reads it and its notes are shown on the plan card and given to the agent. "
            "Costs one subagent run per proposed plan.",
            label="Review plans before approval",
        ),
        _spec(
            "sso_allowed_domains",
            "list",
            [],
            "org",
            "apps/api/src/api/auth/sso_policy.py",
            "E-mail domains allowed to sign in (enforced when sso_enforce_domain_match is on).",
        ),
        _spec(
            "sso_enforce_domain_match",
            "bool",
            False,
            "org",
            "apps/api/src/api/auth/sso_policy.py",
            "Block sign-in from e-mail domains outside sso_allowed_domains.",
        ),
    ),
    "models": (
        _spec(
            "default_model",
            "str",
            "",
            "org",
            _TURN_ROUTING,
            "Organization default model (below the agent and the user's choice). Empty uses the "
            "deployment's default (ROCKET_DEFAULT_MODEL), else the default model of the default "
            "provider: Anthropic's when its key is present, then OpenRouter, OpenAI, Gemini, "
            "DeepSeek.",
        ),
        _spec(
            "default_provider",
            "enum",
            "",
            "org",
            _MODEL_DEFAULTS,
            "Whose models are the defaults (default, fast, economy and frontier) when several "
            "providers have a key. Empty picks Anthropic first, then OpenRouter, OpenAI, Gemini "
            "and DeepSeek. An explicit default, fast, economy or frontier model still wins.",
            choices=("", *PROVIDER_PREFERENCE),
            label="Default provider",
        ),
        _spec(
            "frontier_model",
            "str",
            "",
            "org",
            _MODEL_DEFAULTS,
            "Model for the hardest work: agents set to the frontier tier (tier:frontier, such as "
            "the built-in Architect). Empty uses the frontier model of the turn model's provider "
            "(claude-opus-5-5 for Anthropic).",
        ),
        _spec(
            "context_windows",
            "dict",
            {},
            "org",
            _TURN,
            "Context-window size in tokens per model name or pattern (e.g. "
            '{"my-provider/*": 32000}), for models whose size is not known to the platform; '
            "drives when long conversations are compacted.",
        ),
        _spec(
            "context_target_tokens",
            "int",
            200_000,
            "org",
            _TURN,
            "Working context size in tokens: long conversations are pruned and compacted to stay "
            "under it even when the model's window is larger (requests stay fast and focused). "
            "200000 (the default) trades that window for quicker, cheaper steps on very long sessions; "
            "0 uses the model's whole window without a working cap.",
            minimum=0,
            maximum=10_000_000,
        ),
        _spec(
            "provider_preferences",
            "dict",
            {},
            "org",
            _TURN,
            "OpenRouter provider preferences per model name or pattern, e.g. "
            '{"openrouter/deepseek/*": {"order": ["deepseek"], "allow_fallbacks": false}}. '
            "Pinning a model to one provider keeps its prompt cache warm across requests.",
        ),
        _spec(
            "fallback_models",
            "list",
            [],
            "org",
            _TURN,
            "Ordered fail-over models used on transient provider errors (allowlisted). Empty "
            "uses the same tier through another provider that has a key, when "
            "automatic_fallbacks is on.",
        ),
        _spec(
            "automatic_fallbacks",
            "bool",
            True,
            "org",
            _TURN,
            "When fallback_models is empty, fail over to the same tier through another provider "
            "that has a key (claude-sonnet-5-5 through OpenRouter when the Anthropic API errors, "
            "for example).",
        ),
        _spec(
            "allowed_models",
            "list",
            [],
            "org",
            _TURN_ROUTING,
            "fnmatch patterns of models members may use; empty allows all.",
        ),
        _spec(
            "forbidden_models",
            "list",
            [],
            "org",
            _TURN_ROUTING,
            "fnmatch patterns of models that are never allowed.",
        ),
        _spec(
            "custom_models",
            "list",
            [],
            "org",
            "apps/api/src/api/services/model_catalogue.py",
            "Extra model ids offered in the picker, e.g. a private vLLM or Ollama model "
            "(`ollama/llama3.1`, `hosted_vllm/qwen2.5-coder`); pair with an organization key "
            "that sets the endpoint's base URL.",
        ),
        _spec(
            "temperature",
            "float",
            0.2,
            "org_default",
            _TURN,
            "Default sampling temperature for models that do not reason (an agent persona may "
            "override it). Reasoning models get the provider's own temperature unless the "
            "organization or the agent sets one explicitly (several reasoning APIs reject it), "
            "and extended thinking on an Anthropic model always takes the provider's.",
            minimum=0,
            maximum=2,
        ),
        _spec(
            "default_reasoning_effort",
            "str",
            "medium",
            "org_default",
            _TURN,
            "How much the model reasons before it acts when the agent does not choose: less is "
            "faster and cheaper, and keeps an agent from deliberating for minutes over a decision "
            "it could test. none turns reasoning off. An OpenRouter model that does not take the "
            "level gets the nearest one it does. The default (medium) applies to the reasoning "
            "models the platform knows; a value set here is also sent to other models. Empty "
            "leaves it to the provider.",
            choices=("", "none", "minimal", "low", "medium", "high", "xhigh", "max"),
        ),
        _spec(
            "reasoning_step_limit_tokens",
            "int",
            8000,
            "org",
            _TURN,
            "Reasoning tokens (estimated) a step may spend before it acts. Past it the step is "
            "stopped once and asked again with reasoning off and a note to decide, so a model "
            "that deliberates for minutes over a choice it could test gets on with it. It never "
            "ends the turn. 0 turns the limit off.",
            minimum=0,
            maximum=200_000,
        ),
        _spec(
            "max_tokens",
            "int",
            32_000,
            "org",
            _TURN,
            "Most completion tokens per call: each call asks for the smaller of this and the "
            "model's own maximum output (16384 for a model the platform does not know). Reasoning "
            "models spend output on thinking, so a low value cuts long patches. The matching "
            "entry of output_tokens, then an agent's own limit, win over it.",
            minimum=1,
            maximum=200_000,
        ),
        _spec(
            "output_tokens",
            "dict",
            {},
            "org",
            _TURN,
            "Maximum completion tokens per model name or pattern (e.g. {'*': 64000, 'claude*': 64000}), "
            "overriding the catalogue default ceiling.",
        ),
        _spec(
            "routing_mode",
            "enum",
            "balanced",
            "org",
            _TURN_ROUTING,
            "frontier = one model for all work, balanced = the fast model for auxiliary work "
            "(titles, suggestions, summaries), economy = the economy model everywhere.",
            choices=("frontier", "balanced", "economy"),
            label="Routing mode",
        ),
        _spec(
            "fast_model",
            "str",
            "",
            "org",
            _MODEL_DEFAULTS,
            "Model for auxiliary work (titles, suggestions, summaries) in balanced mode. Empty "
            "uses the fast model of the turn model's provider (claude-haiku-5-5 for Anthropic), "
            "or the turn model itself for a provider the platform has no tiers for.",
        ),
        _spec(
            "economy_model",
            "str",
            "",
            "org",
            _MODEL_DEFAULTS,
            "Model used for all work in economy mode (it replaces the default model everywhere "
            "while the mode is economy). Empty uses the economy model of the turn model's "
            "provider.",
        ),
        _spec(
            "context_prune_percent",
            "int",
            70,
            "org",
            _TURN,
            "Share of the working context at which old tool results are pruned to short receipts.",
            minimum=30,
            maximum=95,
            label="Prune context at",
        ),
        _spec(
            "context_compact_percent",
            "int",
            85,
            "org",
            _TURN,
            "Share of the working context at which the conversation is compacted into a summary "
            "(kept above the prune threshold).",
            minimum=40,
            maximum=98,
            label="Compact context at",
        ),
        _spec(
            "member_keys",
            "enum",
            "preferred",
            "org",
            _MODEL_CREDENTIALS,
            "How members' own provider keys are used, for members allowed to bring their own "
            "(the bring_own_keys capability): off = never, fallback = only for a provider the "
            "organization has no key for, preferred = before the organization's key.",
            choices=("off", "fallback", "preferred"),
            label="Members' own keys",
        ),
        _spec(
            "provider_allowlist",
            "list",
            [],
            "org",
            _TURN_RUNNER,
            "Model providers that may receive organization data; empty allows all.",
        ),
        _spec(
            "zero_retention",
            "bool",
            False,
            "org",
            _TURN_RUNNER,
            "Ask providers not to retain prompts or completions. Enforced for OpenRouter models; "
            "a call to a provider that cannot honour it is refused rather than sent.",
        ),
        _spec(
            "region",
            "str",
            "",
            "org",
            _TURN_RUNNER,
            "Pin inference to a provider region. Enforced for Vertex AI and Bedrock models; a "
            "call to any other provider is refused rather than sent.",
        ),
    ),
    "budgets": (
        _spec(
            "max_turn_cost_usd",
            "float",
            15.0,
            "org",
            _TURN,
            "Spend cap for one turn in USD, its subagents included (0 = no cap). It stops a "
            "runaway turn without limiting steps or time: the turn stops with an explanation "
            "when it is reached, and the session and its work are kept.",
            minimum=0,
        ),
        _spec(
            "goal_max_total_tokens",
            "int",
            0,
            "org",
            _TURN_CONTINUATION,
            "Most tokens a long task may use across its automatic continuation turns "
            "(0 = no cap). Continuation stops when it is reached.",
            minimum=0,
        ),
        _spec(
            "goal_max_wall_time_minutes",
            "int",
            240,
            "org",
            _TURN_CONTINUATION,
            "Longest a long task may run across its automatic continuation turns, in minutes "
            "(0 = no cap).",
            minimum=0,
            maximum=10080,
        ),
        _spec(
            "max_continuations",
            "int",
            10,
            "org",
            _TURN_CONTINUATION,
            "Automatic continuation turns allowed for one task whose plan is unfinished when a "
            "turn reaches its step limit (0 disables continuation).",
            minimum=0,
            maximum=100,
        ),
    ),
    "sandboxes": (
        _spec(
            "workspace_release_days",
            "int",
            7,
            "org",
            "apps/api/src/api/services/workspace_release.py",
            "Days an idle session keeps its workspace volume (the repositories it cloned) after "
            "its sandbox stopped; then the volume is deleted and the next turn starts blank. "
            "0 keeps it as long as the session exists. A sandbox type can set its own.",
            minimum=0,
            maximum=365,
        ),
        _spec(
            "default_image",
            "str",
            DEFAULT_SANDBOX_IMAGE,
            "org",
            _SANDBOX_CATALOGUE,
            "Seeds the sandbox catalogue the first time the organization uses it (the default "
            "entry); afterwards manage images under Settings > Organization > Sandboxes. The "
            "default is the devbox image with git, Python, Node and common dev tools.",
            deprecated=True,
        ),
        _spec(
            "allowed_images",
            "list",
            [],
            "org",
            _PROVISIONING,
            "Legacy fnmatch patterns of extra images sessions may use. Literal images seed the "
            "sandbox catalogue; prefer adding entries to the catalogue, which also declares what "
            "each image can do.",
            deprecated=True,
        ),
        _spec(
            "cpu_limit",
            "float",
            2.0,
            "org",
            _PROVISIONING,
            "CPU cores per sandbox when its sandbox type sets none.",
            minimum=0.1,
        ),
        _spec(
            "memory_limit",
            "str",
            "4Gi",
            "org",
            _PROVISIONING,
            "Memory limit per sandbox (e.g. 4Gi) when its sandbox type sets none.",
        ),
        _spec(
            "disk_size",
            "str",
            "30Gi",
            "org",
            _PROVISIONING,
            "Workspace volume size per session (e.g. 30Gi) when its sandbox type sets none: "
            "room for a monorepo, its dependencies and the language caches. Applies to "
            "workspaces created afterwards (Kubernetes).",
            label="Workspace disk",
        ),
        _spec(
            "allow_terminal",
            "bool",
            True,
            "org",
            "apps/api/src/api/routers/terminal.py",
            "Let members open an interactive shell in their session's running sandbox from "
            "the cockpit (it runs with the same limits, network rules and user as the agent).",
        ),
        _spec(
            "max_running_sandboxes",
            "int",
            0,
            "org",
            _PROVISIONING,
            "Most sandboxes the organization may have running at once; a new session waits for an "
            "idle sandbox to be hibernated instead of starting another. 0 means no limit.",
            minimum=0,
            maximum=10000,
        ),
        _spec(
            "max_background_processes",
            "int",
            5,
            "org",
            _TURN_CALLBACKS,
            "Most background processes (dev servers, watchers, long builds) one session may run "
            "at once; the agent is told to stop one before starting another.",
            minimum=1,
            maximum=50,
        ),
        _spec(
            "process_keepalive_minutes",
            "int",
            120,
            "org",
            _TURN_CALLBACKS,
            "How long a sandbox with a running background process is kept out of idle hibernation, "
            "renewed whenever the process is used or its tab is open. 0 hibernates it on the "
            "normal idle timer (which ends the process).",
            minimum=0,
            maximum=10080,
        ),
        _spec(
            "run_repo_setup",
            "bool",
            True,
            "org",
            _TURN_CALLBACKS,
            "After the agent clones a repository into the workspace, run its setup once: "
            ".rocket/setup.sh, or the onCreateCommand / postCreateCommand of its "
            ".devcontainer/devcontainer.json. It runs in the sandbox as the sandbox user with "
            "git credentials removed from its environment.",
        ),
        _spec(
            "require_non_root",
            "bool",
            False,
            "org",
            _PROVISIONING,
            "Run sandbox processes as an unprivileged user (uid 1000) with all Linux "
            "capabilities dropped. The image must support it (the Rocket Chat devbox does).",
        ),
    ),
    "github": (
        _spec(
            "publish_as",
            "enum",
            "member",
            "org_default",
            _TURN,
            "Who publishes the agent's work: member = pull requests opened with the member's linked "
            "GitHub account (the commits credit them as co-author), bot = the GitHub App opens them. "
            "Choosing bot needs the publish_as_bot capability.",
            choices=("member", "bot"),
            label="Publish as",
        ),
        _spec(
            "signing",
            "enum",
            "github",
            "org",
            _TURN,
            "github: commits are created through GitHub's API so GitHub signs them (verified, made "
            "by the GitHub App); member_gpg: a member who imported a GPG key commits as themselves, "
            "signed with it, and everyone else gets github; none: unsigned.",
            choices=("github", "member_gpg", "none"),
            label="Commit signing",
        ),
        _spec(
            "bot_author_name",
            "str",
            "RocketChat Bot",
            "org",
            "apps/api/src/api/services/turn_service.py",
            "Name of the bot identity used for bot commits.",
            env=("GIT_BOT_NAME",),
        ),
        _spec(
            "bot_author_email",
            "str",
            "bot@rocketchat.internal",
            "org",
            "apps/api/src/api/services/turn_service.py",
            "E-mail of the bot identity used for bot commits.",
            env=("GIT_BOT_EMAIL",),
        ),
        _spec(
            "default_branch",
            "str",
            "main",
            "org",
            "apps/api/src/api/services/turn_service.py",
            "Base branch for pull requests when a repository's own default branch is not known "
            "(each cloned repository's own default branch is used first).",
            env=("GIT_DEFAULT_BRANCH",),
        ),
        _spec(
            "app_token_permissions",
            "dict",
            {
                "contents": "read",
                "pull_requests": "read",
                "checks": "read",
                "actions": "read",
                "statuses": "read",
                "issues": "read",
            },
            "org",
            _GIT_CREDS,
            "Permissions requested for the GitHub App token the sandbox receives. Always lowered to "
            "read: add reads such as actions, checks or statuses to let the agent follow CI; "
            "writing goes through git_push with a token that never enters the sandbox.",
            env=("GITHUB_APP_DEFAULT_PERMISSIONS",),
        ),
        _spec(
            "app_token_target_repositories",
            "str",
            "all",
            "org",
            _GIT_CREDS,
            'Repositories the GitHub App acts on for the agent, reading and changing: "all" or a '
            "comma-separated list of owner/name. Elsewhere the member's linked account is used. "
            "A bare name (deprecated) matches that repository under every owner.",
        ),
    ),
    "mcp": (
        _spec(
            "allowed_transports",
            "list",
            ["sse", "http", "stdio"],
            "org",
            _MCP_SERVERS,
            "MCP transports administrators may register.",
        ),
        _spec(
            "user_allowed_transports",
            "list",
            ["sse", "http"],
            "org",
            _MCP_SERVERS,
            "MCP transports members may register for themselves.",
        ),
        _spec(
            "max_user_servers",
            "int",
            10,
            "org",
            _MCP_SERVERS,
            "Maximum personal MCP servers per member.",
            minimum=0,
            maximum=100,
        ),
    ),
    "slack": (
        _spec(
            "allowed_channel_types",
            "list",
            ["public", "private", "im", "mpim"],
            "org_default",
            "packages/slack-assistant/src/slack_assistant/policy.py",
            "Slack conversation types the assistant answers in.",
            member_override=False,
        ),
        _spec(
            "auto_link_by_verified_email",
            "bool",
            False,
            "org",
            "packages/slack-assistant/src/slack_assistant/policy.py",
            "Link a Slack user to a Rocket account by their verified e-mail automatically.",
        ),
        _spec(
            "dm_policy",
            "enum",
            "paired",
            "org_default",
            "packages/slack-assistant/src/slack_assistant/policy.py",
            "Who the agent may direct-message.",
            choices=("none", "paired", "all"),
            member_override=False,
        ),
        _spec(
            "outbound_channels",
            "list",
            [],
            "org_default",
            "packages/slack-assistant/src/slack_assistant/policy.py",
            "Channel ids the agent may post or react in; empty limits it to the conversation it was invoked from.",
            member_override=False,
        ),
        _spec(
            "web_base_url",
            "str",
            "",
            "org",
            "packages/slack-assistant/src/slack_assistant/policy.py",
            "Public address of the Rocket Chat web app (for example https://rocket.example.com); "
            "every Slack thread links to its session there. Empty falls back to ROCKET_PUBLIC_URL.",
        ),
        _spec(
            "restrict_linked_members",
            "bool",
            False,
            "org",
            "packages/slack-assistant/src/slack_assistant/policy.py",
            "Also block the deployment's restricted tools (by default `git_push`, `git_open_pr` and "
            "`git_revert`) for members who linked their Slack account, in their DMs and private "
            "requests. Off, those requests run with the member's own access, as in the web app; "
            "shared channel threads and unlinked users are always restricted.",
        ),
        _spec(
            "live_progress",
            "bool",
            True,
            "org_default",
            "packages/slack-assistant/src/slack_assistant/policy.py",
            "Stream each tool call into the reply as a step while a turn runs, then write the "
            "answer into the same message; off, the answer is posted when the turn ends.",
            member_override=False,
        ),
        _spec(
            "long_output_to_file",
            "bool",
            True,
            "org_default",
            "packages/slack-assistant/src/slack_assistant/policy.py",
            "Attach long answers and large diffs as Slack files instead of splitting them into "
            "many messages.",
            member_override=False,
        ),
        _spec(
            "long_output_chars",
            "int",
            3500,
            "org_default",
            "packages/slack-assistant/src/slack_assistant/policy.py",
            "Answers longer than this many characters are summarised in the thread and attached "
            "in full as a file.",
            minimum=500,
            maximum=20000,
            member_override=False,
        ),
        _spec(
            "channel_profiles",
            "dict",
            {},
            "org_default",
            "packages/slack-assistant/src/slack_assistant/policy.py",
            "Per-channel behaviour, keyed by channel id: {agent_persona, instructions, "
            "allowed_tools, sandbox_image, response_mode, repository, branch}. A channel with a "
            "profile uses its persona and house rules; allowed_tools narrows the organization's "
            "tool scope for that channel; response_mode ('thread' or 'private') overrides the "
            "bot's; repository and branch are stored for the channel's sessions.",
            member_override=False,
        ),
        _spec(
            "allowed_tools",
            "list",
            ["*"],
            "org_default",
            "packages/slack-assistant/src/slack_assistant/policy.py",
            "Tools the Slack path may use ('*' = every tool except SLACK_BLOCKED_TOOLS).",
            member_override=False,
        ),
    ),
    "governance": (
        _spec(
            "hooks",
            "dict",
            {},
            "org",
            _TURN,
            "Commands run in the sandbox around tool calls: {pre_tool: [{match, command}], "
            "post_tool: [...]}. A failing pre_tool command blocks the call (a policy gate); a "
            "failing post_tool command is reported to the agent. match is a tool-name pattern.",
        ),
        _spec(
            "auto_resume_turns",
            "bool",
            True,
            "org",
            "apps/api/src/api/services/turn_resume.py",
            "Resume a turn automatically when the server running it restarted mid-task "
            "(the agent is told it was interrupted and continues from the saved conversation).",
        ),
        _spec(
            "approval_policy",
            "dict",
            {},
            "org",
            "apps/api/src/api/services/audit.py",
            "Risk levels to auto-approve or deny: {auto_approve: [...], deny: [...]}.",
        ),
        _spec(
            "remembered_approvals",
            "enum",
            "session_and_repository",
            "org",
            "apps/api/src/api/services/approval_rules.py",
            "Whether members may answer an approval once for good: session_and_repository "
            '("Allow for this session" and "Always for this command in this repository"), '
            "session (only the first) or off. Never applies to irreversible actions, to risk "
            "levels the approval policy denies or auto-approves, or while the permission mode is "
            "locked. Switching it off stops existing rules from applying.",
            choices=("session_and_repository", "session", "off"),
        ),
        _spec(
            "question_timeout_seconds",
            "int",
            None,
            "org",
            "apps/api/src/api/services/audit.py",
            "How long a question the agent asks (ask_question) waits for an answer. Past it, the "
            "question closes unanswered and the agent continues with its recommended option or "
            "its best judgement, stating the assumption. Empty waits until someone answers; set "
            "it for sessions nobody watches, such as ones driven through the API.",
            minimum=1,
        ),
        _spec(
            "egress_allowlist",
            "list",
            [],
            "org",
            "apps/api/src/api/services/audit.py",
            "Sandbox egress destinations: CIDR blocks, IPs, host names or *.suffix patterns; empty "
            "means unrestricted. Docker sandboxes reach only these through an allow-listing proxy "
            "(checked after DNS resolution); Kubernetes resolves host names when the sandbox starts "
            "and enforces the addresses with a NetworkPolicy.",
        ),
        _spec(
            "block_internal_subnets",
            "bool",
            False,
            "org",
            "apps/api/src/api/services/audit.py",
            "Block sandbox access to private and link-local ranges.",
        ),
        _spec(
            "redaction_enabled",
            "bool",
            True,
            "org",
            "apps/api/src/api/services/redaction.py",
            "Redact secrets from tool output, traces and logs.",
        ),
        _spec(
            "pii_redaction_enabled",
            "bool",
            False,
            "org",
            "apps/api/src/api/services/redaction.py",
            "Also redact personal data (e-mail addresses, phone numbers, ...).",
        ),
        _spec(
            "redaction_patterns",
            "list",
            [],
            "org",
            "apps/api/src/api/services/redaction.py",
            "Additional regular expressions to redact.",
        ),
        *(
            _spec(
                f"{name}_retention_days",
                "int",
                None,
                "org",
                "apps/api/src/api/services/audit.py",
                f"Delete {name} data older than this many days; empty keeps it.",
                minimum=1,
            )
            for name in ("session", "trace", "memory", "attachment", "audit")
        ),
    ),
    "memory": (
        _spec(
            "enabled",
            "bool",
            True,
            "org",
            "apps/api/src/api/services/memory.py",
            "Enable long-term memory.",
        ),
        _spec(
            "scope_allowlist",
            "list",
            ["user", "org", "session"],
            "org",
            "apps/api/src/api/services/memory.py",
            "Memory scopes the agent may write.",
        ),
        _spec(
            "kind_allowlist",
            "list",
            ["fact", "preference", "decision", "convention"],
            "org",
            "apps/api/src/api/services/memory.py",
            "Memory kinds the agent may write.",
        ),
        _spec(
            "top_k",
            "int",
            5,
            "org",
            "apps/api/src/api/services/memory.py",
            "Memories recalled per turn.",
            minimum=1,
            maximum=50,
        ),
        _spec(
            "dedupe_similarity",
            "float",
            0.95,
            "org",
            "apps/api/src/api/services/memory.py",
            "Cosine similarity above which a new memory updates an existing one.",
            minimum=0,
            maximum=1,
        ),
        _spec(
            "decay_half_life_days",
            "float",
            90.0,
            "org",
            "apps/api/src/api/services/memory.py",
            "Half-life of a memory's ranking weight since last use.",
            minimum=1,
        ),
        _spec(
            "embedding_model",
            "str",
            "",
            "org",
            _MEMORY,
            "Embedding model for semantic recall (1536 dimensions, called with the deployment's "
            "key). Empty picks one by provider: OpenAI's text-embedding-3-small, else Gemini's "
            "gemini-embedding-001. With neither key, recall falls back to keyword matching and "
            "Setup health says so.",
        ),
    ),
    "access": (
        _spec(
            "create_personal_agents",
            "list",
            ["owner", "admin", "member"],
            "org",
            _CAPABILITIES,
            "Roles that may create personal agents.",
            choices=ROLES,
        ),
        _spec(
            "bring_own_keys",
            "list",
            ["owner", "admin", "member"],
            "org",
            _CAPABILITIES,
            "Roles that may store their own model provider keys (used as Models > Members' "
            "own keys says).",
            choices=ROLES,
        ),
        _spec(
            "use_bypass_mode",
            "list",
            [],
            "org",
            _CAPABILITIES,
            "Roles that may run sessions in bypass mode, where nothing waits for approval, "
            "not even the irreversible actions every other mode holds. Asking for bypass "
            "without it runs in ask mode.",
            choices=ROLES,
        ),
        _spec(
            "register_mcp",
            "list",
            ["owner", "admin", "member"],
            "org",
            _CAPABILITIES,
            "Roles that may register personal MCP servers (organization and command servers "
            "stay administrator-only).",
            choices=ROLES,
        ),
        _spec(
            "publish_as_bot",
            "list",
            ["owner", "admin"],
            "org",
            _CAPABILITIES,
            "Roles that may choose to publish as the GitHub App (Git > Publish as: bot).",
            choices=ROLES,
        ),
        _spec(
            "manage_spend_limits",
            "list",
            ["owner", "admin"],
            "org",
            _CAPABILITIES,
            "Roles that may create and change spend limits.",
            choices=ROLES,
        ),
    ),
    "user_preferences": (
        _spec(
            "permission_mode",
            "enum",
            "auto",
            "org_default",
            _TURN,
            "How much of the agent's work waits for a person: plan (read-only), ask (approve every "
            "edit, command and publish), auto_edit (approve commands and publish), auto (only "
            "irreversible actions wait: merging a pull request, deleting on GitHub, a hard revert, "
            "wiping the workspace), bypass (nothing waits). The organization sets the default and "
            "can lock it; otherwise members and sessions choose.",
            choices=("plan", "ask", "auto_edit", "auto", "bypass"),
        ),
        _spec(
            "full_name",
            "str",
            "",
            "member",
            _GIT_IDENTITY,
            "Display name; the commit author name when no GitHub account is linked and no git "
            "name is set (a linked account's username is used otherwise).",
        ),
        _spec(
            "git_author_name",
            "str",
            "",
            "member",
            _GIT_IDENTITY,
            "Commit author name; empty uses the linked GitHub account's username.",
        ),
        _spec(
            "git_author_email",
            "str",
            "",
            "member",
            _GIT_IDENTITY,
            "Commit author e-mail; empty uses the linked GitHub account's primary e-mail, then its "
            "noreply address.",
        ),
        _spec(
            "git_branch_prefix",
            "str",
            "rocket/",
            "org_default",
            _TURN_PROMPT,
            "Prefix the agent uses for the branches it creates.",
        ),
        _spec(
            "preferred_model",
            "str",
            # Empty means "no preference". A concrete default would count as the member's own
            # choice and always outrank the organization default and the platform default.
            "",
            "org_default",
            _TURN_ROUTING,
            "Preferred model (below the agent, above the organization default); empty uses the "
            "organization default.",
        ),
        _spec(
            "suggest_next_questions",
            "bool",
            True,
            "org_default",
            _TURN_FINALISE,
            "Suggest a probable next prompt after each turn.",
            label="Suggest next prompts",
        ),
        _spec(
            "turn_notifications",
            "bool",
            False,
            "member",
            _TURN_ATTENTION,
            "Show a browser notification when a turn ends, or waits for an approval or an answer, "
            "while its tab is in the background. The browser asks for permission once.",
            label="Browser notifications",
        ),
        _spec(
            "notification_sound",
            "bool",
            False,
            "member",
            _TURN_ATTENTION,
            "Play a short sound when a turn ends, or waits for an approval or an answer, while its "
            "tab is in the background.",
            label="Notification sound",
        ),
    ),
}

# Presentation only: which settings a form folds under "Advanced", and what a number counts.
_ADVANCED: dict[str, frozenset[str]] = {
    "general": frozenset({"output_offload_threshold"}),
    "models": frozenset(
        {
            "context_windows",
            "context_target_tokens",
            "provider_preferences",
            "fallback_models",
            "automatic_fallbacks",
            "context_prune_percent",
            "context_compact_percent",
            "custom_models",
            "output_tokens",
            "max_tokens",
            "reasoning_step_limit_tokens",
            "temperature",
            "provider_allowlist",
            "zero_retention",
            "region",
        }
    ),
    "budgets": frozenset(
        {
            "goal_max_total_tokens",
            "goal_max_wall_time_minutes",
            "max_continuations",
        }
    ),
    "sandboxes": frozenset(
        {
            "allowed_images",
            "default_image",
            "workspace_release_days",
            "max_background_processes",
            "process_keepalive_minutes",
            "run_repo_setup",
            "require_non_root",
            "max_running_sandboxes",
            "disk_size",
        }
    ),
    "github": frozenset(
        {
            "app_token_permissions",
            "app_token_target_repositories",
            "bot_author_name",
            "bot_author_email",
            "default_branch",
        }
    ),
    "slack": frozenset(
        {
            "channel_profiles",
            "allowed_tools",
            "long_output_chars",
            "web_base_url",
            "outbound_channels",
            "long_output_to_file",
        }
    ),
    "governance": frozenset(
        {
            "hooks",
            "approval_policy",
            "redaction_patterns",
            "egress_allowlist",
            "block_internal_subnets",
            "session_retention_days",
            "trace_retention_days",
            "memory_retention_days",
            "attachment_retention_days",
            "audit_retention_days",
            "auto_resume_turns",
            "pii_redaction_enabled",
        }
    ),
    "memory": frozenset(
        {"dedupe_similarity", "decay_half_life_days", "kind_allowlist", "scope_allowlist"}
    ),
    "user_preferences": frozenset({"git_branch_prefix"}),
}
_UNITS: dict[str, str] = {
    "max_turns": "steps",
    "output_offload_threshold": "characters",
    "context_target_tokens": "tokens",
    "max_tokens": "tokens",
    "context_prune_percent": "percent",
    "context_compact_percent": "percent",
    "reasoning_step_limit_tokens": "tokens",
    "max_turn_cost_usd": "USD",
    "goal_max_total_tokens": "tokens",
    "goal_max_wall_time_minutes": "minutes",
    "workspace_release_days": "days",
    "process_keepalive_minutes": "minutes",
    "cpu_limit": "cores",
    "long_output_chars": "characters",
    "session_retention_days": "days",
    "trace_retention_days": "days",
    "memory_retention_days": "days",
    "attachment_retention_days": "days",
    "audit_retention_days": "days",
    "top_k": "memories",
    "decay_half_life_days": "days",
    "max_user_servers": "servers",
    "max_background_processes": "processes",
    "max_running_sandboxes": "sandboxes",
}

REGISTRY: dict[str, dict[str, SettingSpec]] = {
    domain: {
        spec.key: replace(
            spec,
            advanced=spec.key in _ADVANCED.get(domain, frozenset()),
            unit=_UNITS.get(spec.key, ""),
        )
        for spec in specs
    }
    for domain, specs in _DOMAINS.items()
}


def domains() -> list[str]:
    return list(REGISTRY)


def specs_for(domain: str) -> dict[str, SettingSpec]:
    return REGISTRY.get(domain, {})


_WRITERS: dict[Writer, tuple[Policy, ...]] = {
    "org": ("org", "org_default"),
    "member": ("org_default", "member"),
}


def writable_keys(domain: str, writer: Writer) -> set[str]:
    """The keys of ``domain`` that ``writer`` may set (nobody writes an ``instance`` key)."""
    return {key for key, spec in specs_for(domain).items() if _may_write(spec, writer)}


def _may_write(spec: SettingSpec, writer: Writer) -> bool:
    if writer == "member" and not spec.member_override:
        return False
    return spec.policy in _WRITERS[writer]


def instance_values(domain: str) -> dict[str, Any]:
    """The operator's values for ``domain`` from the environment, coerced; malformed ones are skipped."""
    out: dict[str, Any] = {}
    for key, spec in specs_for(domain).items():
        raw = env_value(*spec.env) if spec.env else None
        if raw is None or raw == "":
            continue
        try:
            out[key] = _coerce(spec, _from_env(spec, raw))
        except ValueError:
            continue
    return out


def _from_env(spec: SettingSpec, raw: str) -> Any:
    """An environment string as the spec's kind (``a:read,b:write`` for a mapping)."""
    if spec.kind == "dict":
        pairs = (item.split(":", 1) for item in raw.split(",") if ":" in item)
        return {name.strip(): value.strip() for name, value in pairs}
    return raw


def system_defaults(domain: str) -> dict[str, Any]:
    """Platform defaults for ``domain`` (deep-copied so callers may mutate them)."""
    out: dict[str, Any] = {}
    for key, spec in specs_for(domain).items():
        default = spec.default
        out[key] = (
            list(default)
            if isinstance(default, list)
            else (dict(default) if isinstance(default, dict) else default)
        )
    return out


def _coerce_bool(spec: SettingSpec, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ValueError("must be true or false")


def _coerce_number(spec: SettingSpec, value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError(f"must be a {spec.kind}")
    try:
        number: float = int(value) if spec.kind == "int" else float(value)
    except (TypeError, ValueError) as err:
        raise ValueError(f"must be a {spec.kind}") from err
    if spec.minimum is not None and number < spec.minimum:
        raise ValueError(f"must be at least {spec.minimum:g}")
    if spec.maximum is not None and number > spec.maximum:
        raise ValueError(f"must be at most {spec.maximum:g}")
    return number


def _coerce_str(spec: SettingSpec, value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("must be text")
    return value.strip()


def _coerce_enum(spec: SettingSpec, value: Any) -> Any:
    if value not in spec.choices:
        raise ValueError(f"must be one of: {', '.join(spec.choices)}")
    return value


def _coerce_list(spec: SettingSpec, value: Any) -> list[str]:
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        items = [item.strip() for item in value if item.strip()]
    else:
        raise ValueError("must be a list of text values")
    unknown = [item for item in items if spec.choices and item not in spec.choices]
    if unknown:
        raise ValueError(f"unknown values {unknown}; choose from: {', '.join(spec.choices)}")
    return items


def _coerce_dict(spec: SettingSpec, value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    raise ValueError("must be an object")


_COERCERS: dict[str, Callable[[SettingSpec, Any], Any]] = {
    "bool": _coerce_bool,
    "int": _coerce_number,
    "float": _coerce_number,
    "str": _coerce_str,
    "enum": _coerce_enum,
    "list": _coerce_list,
    "dict": _coerce_dict,
}


def _coerce(spec: SettingSpec, value: Any) -> Any:
    """Return ``value`` as ``spec.kind`` or raise ``ValueError`` with a user-facing reason."""
    if value is None:
        if spec.default is None:
            return None
        raise ValueError("must not be empty")
    coercer = _COERCERS.get(spec.kind)
    if coercer is None:
        raise ValueError(f"unsupported kind {spec.kind}")
    return coercer(spec, value)


def validate(
    domain: str,
    values: Mapping[str, Any],
    *,
    writer: Writer,
) -> ValidationResult:
    """Validate ``values`` for ``domain`` as written by ``writer``.

    Unknown keys, keys the writer may not set, and malformed values are errors; nothing is
    silently dropped or stored.
    """
    result = ValidationResult()
    specs = specs_for(domain)
    if not specs:
        result.errors.append(FieldError("*", f"unknown settings domain '{domain}'"))
        return result
    for key, raw in values.items():
        spec = specs.get(key)
        if spec is None:
            result.errors.append(FieldError(key, "unknown setting"))
            continue
        if not _may_write(spec, writer):
            decider = (
                _DECIDER["org"]
                if writer == "member" and spec.policy == "org_default"
                else _DECIDER[spec.policy]
            )
            result.errors.append(FieldError(key, f"can only be set by {decider}"))
            continue
        try:
            result.values[key] = _coerce(spec, raw)
        except ValueError as err:
            result.errors.append(FieldError(key, str(err)))
    return result


_DECIDER: dict[Policy, str] = {
    "instance": "the operator (an environment variable)",
    "org": "an organization administrator",
    "org_default": "an organization administrator or a member",
    "member": "the member",
}


def validate_locked_keys(domain: str, locked_keys: list[str]) -> list[FieldError]:
    """Locked keys must be declared organization defaults (policy ``org_default``)."""
    specs = specs_for(domain)
    errors: list[FieldError] = []
    for key in locked_keys:
        spec = specs.get(key)
        if spec is None:
            errors.append(FieldError(key, "cannot lock an unknown setting"))
        elif spec.policy != "org_default":
            errors.append(FieldError(key, "only organization defaults can be locked"))
    return errors


@dataclass(frozen=True)
class LegacyKey:
    """A key a release retired, still read (with a warning) from rows not migrated yet."""

    domain: str
    key: str
    new_domain: str
    new_key: str
    translate: Callable[[Any], Any] = lambda value: value


# Read for one release after the merge (a stored row the migration did not reach, or the
# in-memory store), then removed: ``api.services.settings`` logs every use.
LEGACY_KEYS: tuple[LegacyKey, ...] = (
    LegacyKey("routing", "mode", "models", "routing_mode"),
    LegacyKey("routing", "fast_model", "models", "fast_model"),
    LegacyKey("routing", "economy_model", "models", "economy_model"),
    LegacyKey("governance", "provider_allowlist", "models", "provider_allowlist"),
    LegacyKey("governance", "zero_retention", "models", "zero_retention"),
    LegacyKey("governance", "region", "models", "region"),
    LegacyKey(
        "budgets",
        "byok_policy",
        "models",
        "member_keys",
        lambda value: "preferred" if value == "user_keys" else "off",
    ),
    LegacyKey(
        "github",
        "pr_creation_policy",
        "github",
        "publish_as",
        lambda value: "bot" if value == "as_bot" else "member",
    ),
    LegacyKey(
        "github",
        "commit_signing_mode",
        "github",
        "signing",
        lambda value: {"github_app": "github", "gpg_key": "member_gpg"}.get(value, "none"),
    ),
)


def legacy_keys_for(domain: str) -> tuple[LegacyKey, ...]:
    """The retired keys whose value lands in ``domain``."""
    return tuple(legacy for legacy in LEGACY_KEYS if legacy.new_domain == domain)
