---
title: Local Kubernetes with Kind
description: Run the full Kubernetes setup on your laptop, the way production runs it.
---

# Local Kubernetes with Kind

Use this to try the **Kubernetes** way of running Rocket Chat on your own machine: the Helm chart, a pod and a volume for every sandbox, and the same isolation you would have in production. It builds the images from your checkout, so it also suits testing a change.

> For a quick look at the product, the [Quickstart](/quickstart) is faster. For a real cluster, use [Kubernetes with Helm](/install/kubernetes).

## What you need

| Tool | Why |
| :--- | :--- |
| Docker | Kind runs the cluster in containers; the images are built with it |
| [`kind`](https://kind.sigs.k8s.io/), `kubectl`, [`helm`](https://helm.sh/) | The cluster and the deployment |
| `make`, `git` | The one-command setup |
| `python3` with `python-dotenv` | The Makefile reads your key from `.env` (`python3 -m pip install python-dotenv`) |
| A model key | OpenRouter, Anthropic, OpenAI, Gemini or DeepSeek |

## 1. Get the code and your key

```bash
git clone https://github.com/rocket-chat/rocket-chat.git
cd rocket-chat
echo "OPENROUTER_API_KEY=sk-or-..." > .env      # .env is git-ignored
```

## 2. One command

```bash
make dev-cluster
```

It creates a Kind cluster named `rocket-local` that publishes ports 3000 and 8000 on `127.0.0.1`, builds the backend and frontend images, loads them into the cluster, and installs the Helm chart in the `rocket-chat` namespace with the local values (`deploy/helm/platform/values.local.yaml`: one replica, the Kubernetes sandbox driver, small sandbox limits). The first run builds images and takes several minutes.

Watch it come up:

```bash
kubectl get pods -n rocket-chat
```

The backend, the frontend, PostgreSQL and the MCP runner should become `Running`.

## 3. Open it

Open **http://localhost:3000** and follow [Your first session](/use/index). When the agent runs its first command, the control plane creates a sandbox:

```bash
kubectl get pods,pvc -n agent-sandboxes
```

You will see `pod-session-<id>` and its `pvc-session-<id>`: the machine and the workspace volume of that conversation. When it has been idle for a while the pod is released and the volume stays, which is the same lifecycle as production ([Build your agents' sandbox](/administer/custom-sandboxes)).

## Iterate on a change

After editing code, rebuild and redeploy:

```bash
make images-build images-load
kubectl rollout restart deploy -n rocket-chat
```

## Troubleshooting

| Symptom | What to do |
| :--- | :--- |
| A sandbox pod stays in `ImagePullBackOff` | The cluster could not pull the sandbox image. Check that the machine is online, or load it: `docker pull ghcr.io/rocket-chat/sandbox:latest && kind load docker-image --name rocket-local ghcr.io/rocket-chat/sandbox:latest`. |
| `ModuleNotFoundError: dotenv` from `make` | Install `python-dotenv` for the `python3` that `make` uses. |
| Port 3000 or 8000 is taken | Stop what uses it before `make cluster-up`: Kind publishes both on `127.0.0.1`. |
| The agent answers with a model error | The key in `.env` was empty when you installed. Fix `.env` and run `make helm-install`. |

More in [Troubleshooting](/operate/troubleshooting).

## Clean up

```bash
make cluster-down        # delete the cluster and everything in it
```

To keep the cluster but remove Rocket Chat: `make helm-uninstall`.

## Next

* [Kubernetes with Helm](/install/kubernetes): a real cluster, storage classes, autoscaling.
* [Helm chart reference](/operate/helm-chart): every value.
* [Configuration reference](/install/configuration).
