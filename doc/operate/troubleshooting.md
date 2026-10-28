# Troubleshooting & Operations Runbook

This runbook provides diagnostic workflows and remediation procedures for common operational issues in Rocket Chat.

---

## 1. Quick Diagnostic Checklist

```bash
# 1. Check control plane health endpoint
curl -i http://localhost:8000/health

# 2. Inspect active pods in control plane and sandbox namespaces
kubectl get pods -n rocket-chat
kubectl get pods -n agent-sandboxes

# 3. Check persistent volume claims in the sandbox namespace
kubectl get pvc -n agent-sandboxes

# 4. View real-time logs from the backend control plane
kubectl logs -f deployment/rocket-chat-backend -n rocket-chat
```

---

## 2. Common Scenarios & Remediation

### Scenario A: `PolicyViolationError` on Turn Submission
* **Symptom:** API returns HTTP 403 or ReAct loop fails with:
  ```text
  PolicyViolationError: Model 'openai/gpt-4o' violates Organization 'org-corp' allowed model policy.
  ```
* **Root Cause:** A user or team requested a model or sandbox image that is either blacklisted in `OrgPolicy.forbidden_models` or omitted from `OrgPolicy.allowed_models`.
* **Resolution:**
  1. Inspect the active organization policy in PostgreSQL:
     ```sql
     SELECT org_id, allowed_models, forbidden_models FROM organizations WHERE org_id = 'org-corp';
     ```
  2. Either update the organization policy to whitelist the model, or switch the user's override to an allowed model (e.g. `openrouter/anthropic/claude-3.7-sonnet`).

---

### Scenario B: Sandbox Pods Stuck in `Pending` Status
* **Symptom:** Agent session pauses and logs show `Waiting for sandbox pod to reach Running state...`
* **Diagnostics:**
  ```bash
  kubectl describe pod <pod-name> -n agent-sandboxes
  ```
* **Common Root Causes & Fixes:**
  1. **Image Pull Backoff:** If using private custom sandbox images, ensure `K8S_IMAGE_PULL_SECRETS` is configured with a valid Kubernetes docker-registry secret.
  2. **Insufficient Resource Quota:** Check if the sandbox namespace has exceeded its `ResourceQuota`:
     ```bash
     kubectl describe resourcequota -n agent-sandboxes
     ```
     Increase `sandbox.quota.pods` or `sandbox.quota.requestsStorage` in your Helm values.
  3. **StorageClass Provisioning Failure:** Check the PVC status:
     ```bash
     kubectl describe pvc <pvc-name> -n agent-sandboxes
     ```
     Verify that cloud CSI drivers (e.g. AWS EBS CSI) have IAM permissions to provision volumes.

---

### Scenario C: PostgreSQL Permission Denied for Table (`rocket_app`)
* **Symptom:** Queries fail with:
  ```text
  permission denied for table sessions
  ```
* **Root Cause:** Tables were created under the superuser (`postgres`) without granting permissions to the non-superuser application role (`rocket_app`).
* **Resolution:** Run the following grants as the database administrator:
  ```sql
  GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO rocket_app;
  GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO rocket_app;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO rocket_app;
  ```

---

### Scenario D: Slack Socket Mode Disconnections or `invalid_auth`
* **Symptom:** Backend logs:
  ```text
  slack_bolt.error: An error occurred while connecting to Slack Socket Mode (invalid_auth)
  ```
* **Root Cause:** Mismatched Slack tokens.
* **Resolution:**
  - Verify that `SLACK_APP_TOKEN` starts with **`xapp-...`** and was generated with the **`connections:write`** scope.
  - Verify that `SLACK_BOT_TOKEN` starts with **`xoxb-...`** and is installed to the target workspace.

---

### Scenario E: WebSocket Drops or Disconnects behind Ingress
* **Symptom:** Mission Control Web UI reports `WebSocket connection lost. Reconnecting...` every 60 seconds.
* **Applies to** both cockpit sockets, the open conversation's (`/v1/sessions/<id>/ws`) and the sidebar's (`/v1/sidebar/ws`): route all of `/v1` to the backend with WebSocket upgrades allowed, as the Helm chart's ingress does.
* **Root Cause:** Ingress proxy timeout closing idle WebSocket connections.
* **Resolution:** Configure long read and send timeouts in your Ingress annotations:
  ```yaml
  annotations:
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
    nginx.ingress.kubernetes.io/websocket-services: "rocket-chat-backend"
  ```

---

### Scenario: A Command MCP Server Is Unavailable
* **Symptom:** The MCP page shows "The MCP runner is not deployed" or "is not answering", or a command server's tools never appear.
* **Diagnostics:**
  ```bash
  kubectl get deploy,pods -l app.kubernetes.io/name=rocket-chat-mcp-runner
  kubectl logs deploy/<release>-rocket-chat-mcp-runner
  curl -s http://<runner-service>:8100/health      # from a backend pod
  ```
* **Common Root Causes & Fixes:**
  1. **Runner disabled:** `mcpRunner.enabled=false`, or `MCP_RUNNER_URL` is not set on the backend. Enable it and upgrade.
  2. **Token mismatch:** the backend and runner must share `MCP_RUNNER_TOKEN` (the chart wires both from one Secret). A wrong token closes the socket immediately.
  3. **NetworkPolicy:** the runner accepts connections only from backend pods; check the labels if you customised them, and that your CNI enforces policies.
  4. **A server starts and exits:** *Test & validate connection* shows the program's own error (a missing package, a bad variable). `npx`/`uvx` need egress to their registries: mirror them and set `npm_config_registry` / `UV_INDEX_URL` as server variables when the cluster is air-gapped.
  5. **"The runner is at capacity":** raise `mcpRunner.maxProcesses` / `maxProcessesPerOrg` or its memory limit.

### Scenario: The Agent Cannot Clone or Fetch a Repository
* **Symptom:** `clone_repository` (or `git fetch` in the sandbox) fails with `Failed to connect to … /v1/git/…`, `Couldn't connect to server`, or `401`/`403` from the gateway.
* **Background:** sandboxes hold no GitHub credential. Their git goes to the API's git gateway (`ROCKET_GIT_GATEWAY_URL` + `/v1/git/`), which adds the credential and talks to GitHub.
* **Diagnostics:**
  ```bash
  # From a sandbox (terminal or bash_exec): is the gateway reachable?
  curl -s -o /dev/null -w '%{http_code}\n' "$GATEWAY/v1/git/octocat/Hello-World.git/info/refs?service=git-upload-pack"
  # 401 = reachable (no capability on this request); a connection error = network path.
  kubectl -n <sandbox-namespace> get networkpolicy sandbox-default-egress -o yaml
  ```
* **Common Root Causes & Fixes:**
  1. **Wrong address:** `ROCKET_GIT_GATEWAY_URL` must be the API as a *sandbox* reaches it, not the public URL. The chart sets the backend Service; with Compose, the API must publish port 8000 on the host.
  2. **NetworkPolicy (Kubernetes):** the chart allows the API pods' port from every sandbox. If you renamed the release or changed pod labels, check `ROCKET_PLATFORM_POD_LABELS`, and that the platform namespace has the standard `kubernetes.io/metadata.name` label.
  3. **Restricted Docker sandboxes:** the egress proxy allows the gateway's exact `host:port`; a proxy started before an upgrade is replaced on the next sandbox start (its policy changed).
  4. **"Repository not found" or `401` on a private repository:** the member has not linked GitHub and no GitHub App installation that can read the repository is mapped to the organization (Settings > GitHub).
