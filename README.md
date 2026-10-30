<p align="center">
  <img src="doc/assets/banner.svg" alt="Rocket Chat: engineering agents for your team, on your infrastructure. Self-hosted, a sandbox per conversation, browser, Slack and GitHub." width="100%">
</p>

<p align="center">
  <a href="https://github.com/rocket-chat/rocket-chat/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-green?style=flat-square" alt="License: GPL-3.0"></a>
  <img src="https://img.shields.io/badge/self--hosted-Docker%20%7C%20Kubernetes-ff5722?style=flat-square" alt="Self-hosted on Docker or Kubernetes">
  <a href="https://artifacthub.io/packages/helm/rocket-chat/rocket-chat"><img src="https://img.shields.io/endpoint?url=https%3A%2F%2Fartifacthub.io%2Fbadge%2Frepository%2Frocket-chat&style=flat-square" alt="Artifact Hub"></a>
</p>

<h3 align="center">Software-engineering agents for your team, each with its own sandbox, on your infrastructure.</h3>

<p align="center">
  <a href="https://rocket-chat.mintlify.site/"><img src="https://img.shields.io/badge/Read_the_docs-ff5722?style=for-the-badge&logo=readthedocs&logoColor=white" alt="Read the docs"></a>
  <a href="doc/quickstart.md"><img src="https://img.shields.io/badge/Quickstart-5_minutes-1f2937?style=for-the-badge&logo=docker&logoColor=white" alt="Quickstart in five minutes"></a>
  <a href="doc/install/kubernetes.md"><img src="https://img.shields.io/badge/Kubernetes-Helm_chart-326ce5?style=for-the-badge&logo=helm&logoColor=white" alt="Kubernetes with the Helm chart"></a>
  <a href="doc/compare.md"><img src="https://img.shields.io/badge/How_it_compares-6b7280?style=for-the-badge" alt="How it compares"></a>
</p>

<p align="center">
  <img src="doc/assets/demo.gif" alt="A request typed in the cockpit, the agent working in its sandbox, the result and its diff" width="860">
</p>

> Not to be confused with Rocket.Chat, the team messaging app: Rocket Chat is a platform of coding agents your team talks to from the browser or Slack.

Ask for work the way you would ask a colleague with a terminal: *fix this failing test and open a pull request*. Rocket Chat gives the conversation its own **sandbox**, a disposable machine with your toolchains, where an agent reads the code, runs the tests, edits files and publishes **signed commits** while you watch, steer and approve. From the **browser** or **Slack**, on **your** Docker host or Kubernetes cluster, with **your** models.

## Get started

<table>
  <tr>
    <td width="33%" valign="top">
      <h3>1. Start it</h3>
      Docker and one model key (OpenRouter, Anthropic, OpenAI, Gemini or DeepSeek).
<pre lang="bash">curl -sSLO https://raw.githubusercontent.com/rocket-chat/rocket-chat/main/deploy/docker-compose.yml
export OPENROUTER_API_KEY=sk-or-...
docker compose up -d</pre>
    </td>
    <td width="33%" valign="top">
      <h3>2. Sign in</h3>
      Open <b>http://localhost:3000</b> and use the developer login. You are the administrator of a fresh organization.
    </td>
    <td width="33%" valign="top">
      <h3>3. Ask for something</h3>
      <i>Clone acme/billing-api, find why <code>test_refund_rounding</code> fails and fix it. Run the tests when you are done.</i><br><br>
      Watch it work, answer its questions, review the diff.
    </td>
  </tr>
</table>

This stack uses throwaway secrets, for trying Rocket Chat on your own machine. Step by step, with what to expect: **[Quickstart](doc/quickstart.md)**. For a team: **[Install your way](#install-your-way)**.

## What you can ask it

Anything you would hand to a capable engineer who has a terminal and a morning:

> *Fix this failing test and open a pull request.*<br>
> *Review this PR for bugs and missing tests, and reply on the threads you are sure about.*<br>
> *Upgrade the framework in these three services and tell me what broke.*<br>
> *Every Monday, check the repos for dependency advisories and summarise.*

## Why teams pick it

### 🚀 Every conversation gets its own sandbox

A disposable machine built from an image you control, with your toolchains and your repositories prepared. The agent clones, tests, edits, starts the app and checks it in a browser. It never holds your GitHub token: commits leave through a signing step on the platform.

<p align="center"><img src="doc/assets/showcase/architecture.png" alt="Browser and Slack talk to the control plane, which runs one sandbox per conversation; code comes in through a read-only gateway and leaves as signed commits" width="820"></p>

### 🧭 It asks instead of guessing

When a choice is yours, the agent shows a card with its options and a recommendation, and waits. In the browser, in Slack, or on your phone.

<p align="center"><img src="doc/assets/showcase/question.png" alt="A question card: the build is blocked, three options, one recommended" width="820"></p>

### 💬 It works in your Slack threads

Mention a bot in a thread and it answers there. Everyone in the thread sees the work, and the same conversation opens in the browser with each person's name on their messages.

<p align="center"><img src="doc/assets/showcase/slack-thread.png" alt="A Slack thread opened in the cockpit: two people ask, the agent finds the bug and opens a pull request" width="820"></p>

### 🛠️ Agents shaped to your team

Start from the built-in engineer, reviewer, architect or security auditor, or make your own: instructions, model, tools, skills, knowledge, sandbox and limits. Bind an agent to its own provider key.

<p align="center"><img src="doc/assets/showcase/agent-editor.png" alt="The agent editor: identity, model and credentials, tools, sandbox, access and limits" width="820"></p>

### 🔒 Yours to run

Your servers, your models (a self-hosted one works, even air-gapped), your rules: single sign-on, roles, spend limits and an audit trail, all in the one GPL-3.0 edition.

<p align="center">
  <a href="doc/why-rocket-chat.md"><b>Why it exists</b></a> ·
  <a href="doc/compare.md"><b>How it compares</b></a> with Claude Code, Codex, OpenCode, Cursor, Copilot's agent, Devin and OpenHands
</p>

## Install your way

- 💻 **Try it on your laptop**: the [Quickstart](doc/quickstart.md), with Docker, in about five minutes.
- 🖥️ **Run it for a team on one server**: [Docker Compose for production](doc/install/docker-compose.md).
- ☸️ **Run it for many people, with autoscaling**: [Kubernetes with Helm](doc/install/kubernetes.md) ([chart reference](doc/operate/helm-chart.md), or [try it locally with Kind](doc/install/local-kubernetes.md)).
- 🛰️ **Self-hosted model, or no internet**: [air-gapped installs](doc/install/air-gapped.md).
- 🧑‍💻 **Hack on it**: [run from source](doc/install/from-source.md).

Every setting, environment variable and Helm value is in the [configuration reference](doc/install/configuration.md).

## The details

<details>
<summary><b>The sandbox</b>: customized, powerful, ephemeral, autoscalable, contained</summary>

- **Customized.** An image with your languages and CLIs, encrypted secrets and variables, and every repository prepared right after it is cloned (`.rocket/setup.sh`, a dev container, or setups you define). [Build your agents' sandbox](doc/administer/custom-sandboxes.md).
- **Powerful.** CPU, memory and disk per sandbox type, which each agent picks; node pools and placement profiles; gVisor or Kata for a stronger boundary; a real shell, live previews of the apps it starts, and a browser to check them.
- **Ephemeral.** It starts with the first command that needs it, sleeps when idle, and releases its volume when the work is long over.
- **Autoscalable.** On Kubernetes a sandbox is a pod with its own volume; autoscalers never evict one that is mid-command.
- **Contained.** No GitHub credentials, non-root, and only the network you allow.

</details>

<details>
<summary><b>The toolset</b>: code, shell, git, GitHub, Slack, the web, delegation</summary>

- **Code**: read, search, edit and patch files; builds, tests, lint and type checks; a package manager.
- **Shell and processes**: a real shell, background servers with their logs, a live **Preview** of a running app, an interactive terminal.
- **Git**: branches, commits with co-author trailers (amend included), diffs, checkpoints and rewind, several repositories in one workspace.
- **GitHub and GitLab**: pull requests, review threads and replies, CI runs and failed-job logs, issues, releases, code search, and a watch that wakes the agent when CI finishes or a review lands.
- **Slack**: read threads, channels and user groups, find people, post and react under your policy, from any session.
- **The web**: fetch and search; a browser that opens the page the agent built and reports console errors and failed requests.
- **Working like an engineer**: a task checklist, durable waits for CI or a review, long-term memory, knowledge-base search, plan mode, questions as cards.
- **Delegation**: parallel subagents, each in its own git worktree.
- **Extensions**: skills, knowledge bases, **MCP servers** (remote, or command-line servers on a locked-down runner), your own agents.

</details>

<details>
<summary><b>For a team</b>: sign-in, roles, spend, supervision, any model</summary>

- **Sign-in and people**: your identity provider, people in several organizations, roles and capabilities, invitations, who may use each agent, shared sessions.
- **Supervision**: from read-only *Plan* to *Ask* (approve every edit and command) to *Auto*, with checkpoints to rewind. Approvals wait as long as you need.
- **Models and spend**: several keys per provider, members can bring theirs, an agent can be bound to its own; routing by task, fallbacks, and spend limits per organization, person, agent or Slack workspace.
- **Long work**: a turn can run for hours, waits for CI and reviews without burning tokens, and resumes after a restart.
- **Chat platforms**: several Slack bots, each mapped to an agent with its own slash command, working in the open or privately; Microsoft Teams and Mattermost too. [Slack](doc/use/slack.md) · [Connect Slack](doc/administer/slack.md).
- **GitHub**: an organization GitHub App for the platform's own work, each member's linked account to act as them, and commits signed by GitHub (Verified) or by the member's own key, which never enters the sandbox. [GitHub](doc/administer/github.md).

</details>

## Documentation

Organised by what you are doing, on **[the documentation site](https://rocket-chat.mintlify.site/)**:

<p align="center">
  <a href="doc/install/index.md"><img src="https://img.shields.io/badge/Install-ff5722?style=for-the-badge" alt="Install"></a>
  <a href="doc/use/index.md"><img src="https://img.shields.io/badge/Use-ff5722?style=for-the-badge" alt="Use"></a>
  <a href="doc/administer/index.md"><img src="https://img.shields.io/badge/Administer-ff5722?style=for-the-badge" alt="Administer"></a>
  <a href="doc/operate/index.md"><img src="https://img.shields.io/badge/Operate-ff5722?style=for-the-badge" alt="Operate"></a>
  <a href="doc/api/index.md"><img src="https://img.shields.io/badge/API-ff5722?style=for-the-badge" alt="API"></a>
</p>

## Contributing and security

Contributions are welcome: start with [CONTRIBUTING.md](CONTRIBUTING.md); the working rules are in [AGENTS.md](AGENTS.md). Report security issues privately to `contact@nathp.fr`, not in a public issue.

## License

Rocket Chat is free software under the **[GNU General Public License v3.0](LICENSE)**. Modified versions you distribute must stay open under the same license, with attribution to this project.
