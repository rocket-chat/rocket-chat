---
title: Build your agents' sandbox
description: Give every conversation its own disposable, powerful environment that already has the tools the work needs.
---

# Build your agents' sandbox

Every conversation runs in its own **sandbox**: a fresh, isolated machine that starts when the agent first needs it, sleeps when it is idle and goes away when the work is done. This guide shows how to make that machine already have everything your agents need, so they start working instead of installing.

The result is a **temporary environment, ready to use**: your toolchains, your build tools, your credentials as variables, your repositories prepared, and the CPU and memory the job deserves.

## What an agent gets by default

The default image (`ghcr.io/rocket-chat/sandbox`) is a development environment that runs as an unprivileged user and contains git, Python with `uv`, Node with `pnpm` and `npm`, build tools, `ripgrep`, `fd` and `jq`. The agent also gets a persistent workspace under `/workspace`, an optional interactive shell, a live preview for the servers it starts, and, on request, a browser for checking the apps it builds.

That is enough for many projects. The rest of this page is for when it is not.

## Five levers, from simplest to most powerful

| You want | Use | Where |
| :--- | :--- | :--- |
| Your **language and tool versions** ready (Go, Java, Rust, a database client, a CLI) | A custom image | [Step 1](#step-1-a-custom-image) |
| **Credentials and settings** present in every command (a private-module setting, a token) | Environment variables, with secrets encrypted | [Step 2](#step-2-environment-variables-and-secrets) |
| A **repository prepared** before the first command (dependencies installed, code generated) | Repository setup | [Step 3](#step-3-repository-setup) |
| **More power** for heavy builds, or a specific node pool | Sizing and placement profiles | [Step 4](#step-4-size-and-place-it) |
| **Different environments for different jobs** | One sandbox type per job, chosen by agent | [Step 5](#step-5-match-sandboxes-to-agents) |

## Step 1: a custom image

Start from the default image so you keep everything above, and add what your repositories need. The default image runs as the unprivileged user `agent` (uid 1000) and has no `sudo`, so install as `root` and switch back.

```dockerfile
FROM ghcr.io/rocket-chat/sandbox:latest

USER root

# A language runtime: copy it from its official image.
COPY --from=golang:1.23-bookworm /usr/local/go /usr/local/go
ENV PATH=/usr/local/go/bin:${PATH}

# System packages and CLIs your builds call.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

USER agent

# Warm the caches that are slow to fill: the first session no longer pays for them.
RUN go install golang.org/x/tools/cmd/goimports@latest
```

Build it, push it to a registry your cluster or Docker host can pull from, and add it to the catalogue:

```bash
docker build -t registry.example.com/platform/sandbox-go:1 -f Dockerfile.sandbox-go .
docker push registry.example.com/platform/sandbox-go:1
```

Then **Settings → Organization → Sandboxes → Add sandbox**, with a name, the image and a description members will recognise. See [Sandboxes](/administer/sandboxes) for the fields. Keep these rules in mind:

* **Run as non-root** if your organization requires it (*Require non-root* in the sandbox settings): the image must set a non-root `USER`.
* **Private registry?** On Kubernetes, name your pull secrets under `sandbox.imagePullSecrets` in the Helm values.
* **Air-gapped?** Mirror the default image into your registry and set `sandbox.defaultImage` ([Self-hosted models and air-gapped installs](/install/air-gapped)).
* **The browser** is a capability, not a default: the `rocket-chat-sandbox-browser` image has Chromium and Playwright, and you tick *Browser tools* on an entry that uses it.

Installed dependencies also persist in a session's workspace across restarts, so a custom image mainly saves the first install.

## Step 2: environment variables and secrets

On a sandbox entry, **Environment variables** are given to every command of its sessions: shell commands, tests, builds and background processes. For example `GOPRIVATE=github.com/acme/*` makes Go fetch private modules directly.

Tick **Secret** for a credential: it is stored encrypted, never shown again, and never given to the model, which only learns the variable's name. Members do not see a sandbox type's variables. The agent can add its own for a session with `env_set`.

## Step 3: repository setup

Right after the agent clones a repository, Rocket Chat can prepare it once, in its folder, before the agent's first command:

* the repository's own **`.rocket/setup.sh`** (or the `onCreateCommand` and `postCreateCommand` of its `.devcontainer/devcontainer.json`), or
* **Repository setups** you define under *Settings → Organization → Sandboxes*, for a repository you cannot change or a monorepo whose services each need their own preparation. Each names a repository (`owner/name` or a pattern such as `acme/*`), a folder, and the commands.

```bash
npm ci
npm run generate
```

When it fails, the agent sees why instead of running into the symptom later. Details in [GitHub](/administer/github).

## Step 4: size and place it

On each sandbox entry (or per agent) you set CPU, memory and workspace disk. In a shared Kubernetes cluster, your platform operator can define **placement profiles** (Helm `sandbox.profiles`): a node pool with its tolerations, runtime class, priority class and storage class. You pick a profile per sandbox type, so heavy work lands on the right nodes and a toleration cannot be used to reach nodes reserved for something else.

A runtime class such as `gvisor` or `kata-qemu` that you have installed in the cluster (`sandbox.runtimeClassName`) puts every sandbox behind a stronger isolation boundary.

Parallel write subagents need at least 2 CPU cores and 4 GiB of memory, because each works in its own git worktree; smaller sandboxes serialize them.

## Step 5: match sandboxes to agents

An [agent](/use/extend#agents-personas) can name the sandbox its sessions start in. Give a backend agent a high-CPU type with the Go image, a frontend agent the browser image, and a security reviewer the default one. The person starting a session can still pick another sandbox; the agent's choice applies when they leave the picker on its first option.

## It scales with your cluster

On Kubernetes each session is a pod with its own volume, created on demand in a dedicated namespace:

* A sandbox starts with the first tool call that needs it. If the cluster has to add a node, that call waits up to two minutes for the pod, long enough for the node autoscaler.
* Pods are annotated so cluster autoscalers (Cluster Autoscaler, Karpenter) **never evict a sandbox that is running a command or keeping a dev server awake**, and may move an idle one, whose workspace survives on its volume.
* An idle sandbox sleeps (its pod is released, its volume kept) after the idle period your operator sets (`sandbox.inactivity.pauseSeconds` in the Helm chart, 30 minutes by default). A sandbox with a running command or process is never put to sleep mid-flight.
* *Maximum running sandboxes* caps an organization; at the cap, an idle sandbox is put to sleep to make room.
* After a while without activity the volume is released too (7 days by default), so storage does not pile up. The conversation stays; its next turn starts with a blank sandbox.

The control plane itself scales with the Helm chart's autoscaling and pod disruption budget ([Helm chart](/operate/helm-chart)).

## Control what it can reach

A sandbox holds no GitHub credentials: it clones and fetches through a read-only gateway, and commits leave through the publishing tool. Outbound network access can be restricted to an allowlist of hosts, with internal subnets and the cloud metadata address blocked ([Network egress](/administer/sandboxes#network-egress)).

## Next

* [Sandboxes](/administer/sandboxes): every field and limit.
* [GitHub](/administer/github): repository setup and publishing.
* [Skills, tools, knowledge and agents](/use/extend): agents that name their sandbox.
