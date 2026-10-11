---
title: "Self-Hosted Models & Air-Gapped Installs"
description: "Run Rocket Chat with Ollama or vLLM and no outbound internet"
---

# Self-hosted models & air-gapped installs

Rocket Chat needs a model, but not a *hosted* one. Any OpenAI-compatible server (vLLM, LM Studio, llama.cpp, a corporate gateway) and Ollama work through the same path as a cloud provider, and the whole platform can run with no route to the internet.

## 1. Point an organization at your model server

An administrator stores an endpoint as a provider "key" with a base URL (**Settings > Models > Provider keys**, or `PUT /v1/admin/model-keys/{provider}` with `{"api_key": ..., "api_base": ...}`):

| Server | Provider id | Base URL | Model id to use |
| :--- | :--- | :--- | :--- |
| vLLM / any OpenAI-compatible server | `hosted_vllm` (or `openai`) | `http://vllm.internal:8000/v1` | `hosted_vllm/Qwen2.5-Coder-32B-Instruct` |
| Ollama | `ollama_chat` | `http://ollama.internal:11434` | `ollama_chat/qwen2.5-coder:32b` |

The API key is whatever the server expects (any non-empty text when it checks nothing). Then list the model ids under **Models > Custom models** (`models.custom_models`) so they appear in the picker, and set the default (`models.default_model`) and, if you want to be strict, the allow list (`models.allowed_models`) to only those. The endpoint is resolved per request from the organization's stored settings - nothing about it needs to be in the process environment. `tests/integration/test_local_models.py` exercises both dialects over real HTTP against a loopback server.

Governance flags still apply and are meaningful with a local model: `models.forbidden_models` and the provider allow list (`governance.provider_allowlist`) can pin the organization to `hosted_vllm`/`ollama_chat` so a stray selection cannot send data to a cloud provider, and the usage ledger records each call (cost 0 for models LiteLLM does not price).

> A self-hosted model is only as good as its tool-calling. Pick a coder model with reliable function calling (e.g. Qwen2.5-Coder, Llama 3.1 70B) and expect weaker results than frontier models on long autonomous tasks.

## 2. Stop reaching for the internet

Set `global.airGapped: true` (Helm) or `LITELLM_LOCAL_MODEL_COST_MAP=True` (any deployment). Without it LiteLLM downloads its model price list at startup. Everything else below is configuration:

| Capability | Default | In an air-gapped install |
| :--- | :--- | :--- |
| Container images | public registries | Mirror `backend`, `frontend`, the devbox image and your sandbox images to an internal registry; set `image.repository`, `sandbox.imagePullSecrets` (Secrets that exist in the sandbox namespace) and restrict `sandboxes.allowed_images` to the mirrored names |
| `web_search` | DuckDuckGo HTML | `search.provider: searxng` pointing at an internal instance, or remove `web_search`/`web_fetch` from agents' tool allow list |
| `web_fetch` | public hosts only | Add an egress proxy (`search.fetchProxy`) or `ROCKET_WEB_FETCH_ALLOW_PRIVATE=true` for internal documentation sites |
| GitHub | github.com | `GITHUB_API_URL` for GitHub Enterprise Server; repositories on another host are refused unless the host is configured |
| Sandbox network | open | `governance.egress_allowlist` / `block_internal_subnets` (Kubernetes enforces per sandbox) - an air-gapped cluster usually allows only the internal package mirror |
| Package installs | public indexes | Bake the toolchain into your sandbox image, or point pip/npm at an internal mirror through the image's configuration |
| Identity | OIDC provider | Any OIDC provider you host (Keycloak); or the built-in password sign-in (`LOCAL_AUTH_ENABLED`) |
| Cockpit assets | self-hosted | Fonts (`next/font`, downloaded at *build* time), the Monaco editor (`/monaco`, copied from the `monaco-editor` package at build) and the terminal stylesheet are served by the app; a Playwright test fails if the cockpit requests any third-party origin. Build the frontend image where the build can fetch the fonts, then run it anywhere |
| Telemetry | none | Rocket Chat sends none. OpenTelemetry export is off unless `observability.otlpEndpoint` is set |

## 3. Verify it

1. Block the pods' outbound traffic (a default-deny `NetworkPolicy` with exceptions for PostgreSQL, the model server and the registry).
2. Start a session, ask for a change, run the tests. Everything that worked before should still work; anything that tried to leave the cluster fails visibly instead of silently.
3. `GET /v1/ready` and the turn metrics (`/metrics`) confirm the control plane and model path are healthy without internet.
