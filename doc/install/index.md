---
title: Install
description: Run Rocket Chat with Docker Compose or on Kubernetes.
---

# Install

Rocket Chat is three pieces: the **web app** (Next.js), the **API** (FastAPI, which also runs the agent) and **PostgreSQL** (with the pgvector extension). Sandboxes are created on demand by the API, as Docker containers or Kubernetes pods.

| | Docker Compose | Kubernetes (Helm) |
| :--- | :--- | :--- |
| **For** | Trying it out, a small team on one machine | Production, many users, autoscaling |
| **Sandboxes** | Containers on the same Docker host | Pods with a volume each, in their own namespace |
| **Guide** | [Docker Compose](/install/docker-compose) | [Kubernetes](/install/kubernetes) |

## Try it in two minutes

You need Docker. This starts the published images with throwaway secrets and a developer login, for evaluation on your own machine only:

```bash
curl -sSL https://raw.githubusercontent.com/rocket-chat/rocket-chat/main/deploy/docker-compose.yml -o docker-compose.yml
docker compose up -d
```

Open **http://localhost:3000**. Then follow [Your first session](/use/index).

Before anyone else uses it, read [Docker Compose](/install/docker-compose) for the production settings (real secrets, sign-in, backups).

## What you need to decide

1. **Sign-in.** Password logins managed by an administrator, or your identity provider over OIDC ([SSO](/administer/sso)).
2. **A model.** Give the deployment a provider key (OpenRouter, Anthropic, OpenAI, Gemini, DeepSeek), or let members bring their own, or point at a self-hosted model ([air-gapped installs](/install/air-gapped)).
3. **GitHub**, so the agent can clone and open pull requests ([GitHub](/administer/github)).
4. **Slack**, if you want it ([Connect Slack](/administer/slack)).
5. **Sandbox image.** The default is a development image; you can offer others later ([Sandboxes](/administer/sandboxes)).

Every environment variable is listed in the [configuration reference](/install/configuration); everything an administrator can change in the app is in the [settings reference](/administer/settings-reference).
