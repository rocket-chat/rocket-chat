---
title: Sandboxes
description: The images members can choose, what each can do, and the limits that apply.
---

# Sandboxes

A **sandbox** is the container a session runs in. You decide which ones your organization offers under **Settings → Organization → Sandboxes**.

## The catalogue

![The sandbox catalogue](../assets/screenshots/sandboxes.png)

Each entry has a **name**, an **image**, a **description**, optional **capabilities** and exactly one is the **default**.

* The first time your organization is used, the catalogue is created with one entry: the deployment's default image (`ROCKET_DEFAULT_SANDBOX_IMAGE`; the development image with git, GitHub CLI, Python with `uv`, Node and build tools), plus any images an older install had allowed.
* Members choose a sandbox when they start a session (a picker appears once there are two or more); a session keeps the one it started with. Starting a session with an image that is not in the catalogue is refused.
* An agent can name the sandbox its sessions start in (**Settings → Agents → Sandbox**), for example a high-CPU type for a backend agent. It is used when the person starting the session leaves the picker on its first option, which then reads, for example, **Agent's: High CPU**; a sandbox picked there wins. An agent whose type was removed uses the default.
* You cannot remove the default or the last entry; make another entry the default first.
* Changes are recorded in the audit log.

Build your own image from the development image to add toolchains and warm caches (`FROM` the default, add what your repositories need), push it to a registry the cluster can pull from, and add it here. Installed dependencies persist in a session's workspace across restarts, so a custom image mostly saves the first install.

## Runtime of a sandbox type

Each entry can also set, under **Runtime**:

* **Placement profile**: a node pool and its sizing (node selector, tolerations, runtime class, priority class, storage class, CPU and memory requests and limits) that your platform operator defined in the deployment (Helm `sandbox.profiles`). Administrators choose one; only operators define them, because in a cluster several organizations share, a toleration could put sandboxes on nodes reserved for something else. A profile's CPU and memory replace the sizing below.
* **CPU cores, memory and workspace disk**: this type's sizing instead of the organization's (an agent's own sizing still wins).
* **Release the workspace after (days)**: this type's own delay (see *Sleeping and cleanup*); empty uses the organization's.

## Environment variables of a sandbox type

An entry can set **environment variables** that every command of its sessions receives (shell commands, tests, builds, background processes), for example `GOPRIVATE=github.com/acme/*` so Go fetches private modules directly instead of through the public proxy. Tick **Secret** for a credential: its value is stored encrypted, never shown again (leave the field empty when editing to keep it) and never given to the model, which only learns the variable's name. Members do not see a type's variables.

* Names are letters, digits and `_`; at most 50 variables of up to 8,192 characters each.
* Variables the platform owns cannot be set: `PATH`, `HOME`, `SHELL`, `USER`, `PWD`, `HOSTNAME` and anything starting with `GIT_`, `ROCKET_` or `LD_`. The platform's own credentials (such as the GitHub token) always win over a type's variables.
* A change applies from the session's next turn.

The agent can set its own variables for a session with `env_set` (they last for the rest of the session, over the type's), remove them with `env_unset` and list them with `env_list`.

The settings menu marks these pages **ADMIN**: what you change applies to every member.

## Capabilities

| Capability | What it enables | What the image must contain |
| :--- | :--- | :--- |
| **Browser tools** | The agent can open a page it built and read its text, console errors and failed requests, optionally with a screenshot (`browser_inspect`). | Chromium and Playwright. The `rocket-chat-sandbox-browser` image (built from `deploy/docker/Dockerfile.sandbox-browser`) has both. |

Capabilities are an explicit promise: ticking *Browser tools* on an image that lacks Chromium makes the tool fail, and an entry without it never offers the tool. **Looking at images** is not a sandbox capability: the agent can open any image in the workspace (`view_image`), and whether it can see it depends on the model.

## Resources and behaviour

Under **Sandbox runtime** on the same page:

| Setting | Effect |
| :--- | :--- |
| CPU and memory limit | Per sandbox. |
| Maximum running sandboxes | Per organization; at the cap, an idle sandbox is put to sleep to make room. `0` means no limit. |
| Require non-root | Refuse images that run as root. |
| Allow terminal | Let members open an interactive shell in their sandbox. |
| Run repository setup | Run `.rocket/setup.sh` / dev container hooks after the agent clones a repository. |
| Maximum background processes | Servers and watchers one session may run at once (default 5). |
| Process keep-awake (minutes) | How long a sandbox with a running process is kept out of idle sleep. |

### Parallel write subagents

When an agent delegates work concurrently using parallel subagents (`delegate_subagents_parallel`), each write-capable subagent operates in its own isolated git worktree (`.rocket/worktrees/<id>`). To prevent memory exhaustion and compiler out-of-memory errors during concurrent builds, parallel write subagents require a sandbox profile with **at least 2 CPU cores and 4 GiB RAM**. If a sandbox has smaller resource limits, delegations are automatically serialized one-by-one.

## Network egress

The governance settings can restrict what a sandbox reaches: an allowlist of hosts, CIDRs or `*.suffix` names, and blocking internal subnets (including the cloud metadata address). Rules are applied to the **resolved** address, on Docker through an allow-listing proxy and on Kubernetes through network policy. Loopback always works, so previews and local servers keep working.

## Sleeping and cleanup

A sandbox starts with the first tool call that needs it. On Kubernetes that call waits up to 2 minutes for the pod, long enough for the cluster to add a node to a full pool. A pod still waiting after that is kept, and the next call waits on it; a pod whose image cannot be pulled fails at once with the reason.

A sandbox with no activity for the idle period is stopped and its workspace volume is kept (the operator sets the period with `SANDBOX_INACTIVITY_PAUSE_SECONDS`: 30 minutes on Docker, 10 minutes on Kubernetes by default), so the next turn finds the repositories it cloned. Sandboxes executing an active turn or running background processes are held awake and never hibernated mid-flight. In multi-replica deployments, the inactivity reaper runs under cluster leader election and cross-checks active running turns and recent turn activity from the database, preventing non-executing replicas from prematurely hibernating active sandboxes. Running background processes end when a sandbox sleeps.

A kept volume still costs storage. After **`workspace_release_days`** (Settings → Sandboxes, 7 days by default, 0 = keep it as long as the session exists) without activity in the session, the volume is deleted too; a sandbox type can set its own delay. The session stays: its next turn starts with a blank sandbox, and the agent is told the workspace was released, so it clones again (work that was not pushed is gone). Workspaces are also removed with their session.
