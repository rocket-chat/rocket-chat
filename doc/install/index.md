---
title: Install
description: "Pick how to run Rocket Chat: on a laptop, on one server, on Kubernetes, from source, or offline."
---

# Install

Rocket Chat is three pieces: the **web app** (Next.js), the **API** (FastAPI, which also runs the agent) and **PostgreSQL** (with the pgvector extension). Each conversation's **sandbox** is created on demand by the API, as a Docker container or a Kubernetes pod. A small **MCP runner** hosts command-line (stdio) MCP servers away from everything else.

## Pick your path

| I want to... | Guide | Sandboxes run as |
| :--- | :--- | :--- |
| **Try it** on my laptop in a few minutes | [Quickstart](/quickstart) | Containers on my Docker host |
| **Run it for my team** on one server | [Docker Compose for production](/install/docker-compose) | Containers on that Docker host |
| **Run it for many people**, with autoscaling and isolation between organizations | [Kubernetes with Helm](/install/kubernetes), then the [Helm chart reference](/operate/helm-chart) | A pod and a volume per session |
| **Try the Kubernetes setup** on my laptop | [Local Kubernetes with Kind](/install/local-kubernetes) | Pods in a local cluster |
| **Use a self-hosted model**, or run with no internet | [Self-hosted models and air-gapped installs](/install/air-gapped) | Either |
| **Change Rocket Chat**, or run a branch | [Run from source](/install/from-source) | Docker, or a fake sandbox |

> **Which sandbox driver?** The Docker driver needs the host's Docker socket, which is equivalent to root on that machine for whoever controls the API. Use it for a single trusted team. For many people, or anything reachable from the internet, use Kubernetes: sandboxes run in their own namespace, with restricted privileges, network policies, per-organization limits, and autoscaling nodes.

## Try it in two minutes

You need Docker and a model key. This starts the published images with throwaway secrets and a developer login, for your own machine only:

```bash
curl -sSL https://raw.githubusercontent.com/rocket-chat/rocket-chat/main/deploy/docker-compose.yml -o docker-compose.yml
export OPENROUTER_API_KEY=sk-or-...
docker compose up -d
```

Open **http://localhost:3000**. The [Quickstart](/quickstart) walks through what to expect.

## What you need to decide

1. **Sign-in.** Password logins managed by an administrator, or your identity provider over OIDC ([SSO](/administer/sso)).
2. **A model.** Give the deployment a provider key (OpenRouter, Anthropic, OpenAI, Gemini, DeepSeek), or let members bring their own, or point at a self-hosted model ([air-gapped installs](/install/air-gapped)).
3. **GitHub**, so the agent can clone, publish signed commits and open pull requests ([GitHub](/administer/github)).
4. **Slack**, if you want your agents in your channels ([Connect Slack](/administer/slack)).
5. **The sandbox.** The default is a ready development image; build your own with your toolchains and repositories prepared ([Build your agents' sandbox](/administer/custom-sandboxes)).

Every environment variable is listed in the [configuration reference](/install/configuration); everything an administrator can change in the app is in the [settings reference](/administer/settings-reference).
