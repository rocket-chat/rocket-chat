# Getting Started with Rocket Chat

Welcome to **Rocket Chat**, your autonomous pair-programming workspace. Rocket Chat pairs you with specialized engineering agents that execute terminal commands, edit source files, review pull requests, and solve complex software problems inside dedicated, safe sandbox environments.

---

## 1. Fast-Track: Launch Your Local Workspace

Launch the complete Rocket Chat platform using single-command Docker Compose:

```bash
# 1. Clone repository
git clone https://github.com/rocket-chat/rocket-chat.git
cd rocket-chat

# 2. Launch production stack in background
docker compose -f deploy/docker-compose.prod.yml up -d
```

Once running, navigate to:
👉 **[http://localhost:3000](http://localhost:3000)** to access your Cockpit.

---

## 2. Platform Overview & Core UI Tour

When you open Rocket Chat, you are greeted by the clean, minimal pair-programming cockpit designed for maximum focus:

![Rocket Chat Minimal Workspace](../assets/screenshots/screen_minimal_workspace.png)

### Key Workspace Elements:

1. **Continuous Chat Stream (Center):**  
   Your conversation with the assistant appears in a distraction-free, borderless view. User messages are highlighted in subtle conversational bubbles on the right, while assistant responses format code, tests, and diffs cleanly without clutter.

2. **Persona & Model Selector (Bottom Deck):**  
   Choose between specialized pre-configured engineering agents (e.g. *Full-Stack Engineer*, *Security Auditor*, *Bug Hunter*, *Documentation Scribe*) or connect directly to raw flagship LLMs (DeepSeek V4.1, Claude 3.7 Sonnet, GPT-4o).

3. **Ignition Console (Composer):**  
   Type prompts or instruct actions. Press `Enter` (or `⌘⏎`) to launch an execution turn.

4. **Predictive Follow-Up Suggestions:**  
   At the end of an assistant response, the AI automatically formulates the most probable follow-up question. This appears as a subtle ghost text directly inside the composer with a `Tab ⇥` badge:
   - Press **`Tab`** to instantly autocomplete the prompt.
   - Press **`Enter`** to execute it immediately.

---

## 3. Real-Time Reasoning & Discreet Thought Stream

As the AI plans and executes tasks—such as inspecting directory trees, executing bash scripts, or diagnosing unit test failures—it records its thought process without overwhelming your chat stream:

![Reasoning Deployed View](../assets/screenshots/screen_reasoning_deployed.png)

* **Discreet by Default:** While the agent is working, reasoning and tool calls remain collapsed in a subtle, semi-transparent progress indicator so you can follow the status without noise.
* **Expandable on Click:** Click any reasoning or tool block to inspect full terminal traces, stdout/stderr streams, bash execution arguments, and duration metrics.

---

## 4. Configuring Organization & User Settings

Rocket Chat features a dedicated cascading configuration system:
- **Organization-Wide Policies:** Managed by administrators to define default models, commit signing rules, and tool access across all workspaces.
- **Personal Preferences:** Configured by individual developers for personal git authorship, notification settings, and model overrides.

Access the settings console by clicking the **Settings** gear icon in the top navigation bar:

![Rocket Chat Settings Console](../assets/screenshots/screen_settings_console.png)

### Settings Console Tabs:

| Tab Group | Section | What You Can Configure |
| :--- | :--- | :--- |
| **Organization** | **General & Policies** | Organization name, compliance tier (SOC2, HIPAA), session privacy defaults, predictive suggestions toggle. |
| | **GitHub Integration** | Commit signing policies (`github_app`, `gpg_key`), PR creation mode, bot authorship credentials. |
| | **Slack Integration** | Default agent persona, allowed channel types (`public`, `private`, `im`), authorized tools. |
| | **MCP Tools** | Model Context Protocol servers, transport types (`sse`, `http`, `stdio`), tool binding policies. |
| **Personal** | **Profile & Preferences** | Display name, default role, predictive next-question autocomplete preference. |
| | **Git Authorship** | Personal Git author name, email, branch prefix (`alex/rocket-`), personal access token (PAT). |
| | **Models & Inference** | Preferred model default, turbo reasoning mode, temperature override. |
| | **Slack & Alerts** | Personal Slack handle (`@user`), direct message notifications toggle. |

---

## 5. Interaction Modes: Web, Slack, and GitHub

Rocket Chat is designed to meet software engineers wherever they collaborate:

### A. Web Mission Control
Use the web cockpit (`http://localhost:3000`) for pair-programming sessions with Monaco side-by-side diff viewers, streaming xterm.js terminals, and interactive human-in-the-loop decision cards.

### B. Slack Assistant (Socket Mode)
Mention `@Rocket` in any authorized Slack channel:
```slack
@Rocket please investigate failing tests on branch feat/auth-tokens and open a fix PR
```
The agent executes code in a dedicated sandbox, posts progress spinners to the Slack thread, and provides actionable summaries.

### C. GitHub Automation
Add the `ai-fix` or `ai-review` label to any issue or PR in monitored repositories. Rocket Chat spins up an isolated sandbox, reproduces the issue, implements a fix, and submits a cryptographically signed pull request.

---

## 6. Next Steps

* Explore [Feature Use Cases & Workflows](/getting-started/use-cases) to learn how teams use Rocket Chat for debugging, refactoring, and code review.
* Read the [Organization & User Settings Guide](/getting-started/user-settings) for detailed configuration options.
* Platform engineers and SREs can consult [Kubernetes Setup](/getting-started/kubernetes-quickstart) and [Deployment Operations](/deployment-and-ops/index).
