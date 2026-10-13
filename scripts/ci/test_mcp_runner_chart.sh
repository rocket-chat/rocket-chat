#!/bin/sh
# Install the chart's MCP runner on a throwaway k3d cluster and prove what the chart promises:
# the pod runs hardened, only backend pods may connect, the cluster API is unreachable from it,
# and the generated token survives an upgrade. Needs docker, k3d, kubectl, helm and the image
# rocket-chat-mcp-runner:dev. Uses its own kubeconfig; never touches the current context.
set -eu

CLUSTER="mcp-runner-chart-$$"
WORK="$(mktemp -d)"
export KUBECONFIG="$WORK/kubeconfig"
VALUES=deploy/helm/platform/ci/values.yaml
NS=rc

cleanup() { k3d cluster delete "$CLUSTER" >/dev/null 2>&1 || true; rm -rf "$WORK"; }
trap cleanup EXIT

k3d cluster create "$CLUSTER" --kubeconfig-update-default=false --kubeconfig-switch-context=false --no-lb --wait --timeout 180s >/dev/null
k3d kubeconfig get "$CLUSTER" > "$KUBECONFIG"
k3d image import rocket-chat-mcp-runner:dev -c "$CLUSTER" >/dev/null
kubectl create namespace "$NS" >/dev/null

helm template rc deploy/helm/platform -n "$NS" -f "$VALUES" \
    --set mcpRunner.image.repository=rocket-chat-mcp-runner --set mcpRunner.image.tag=dev \
    --set mcpRunner.image.pullPolicy=Never \
    -s templates/mcp-runner/secret.yaml -s templates/mcp-runner/pvc.yaml \
    -s templates/mcp-runner/deployment.yaml -s templates/mcp-runner/service.yaml \
    -s templates/mcp-runner/networkpolicy.yaml > "$WORK/runner.yaml"
kubectl -n "$NS" apply -f "$WORK/runner.yaml" >/dev/null
kubectl -n "$NS" rollout status deploy/rc-rocket-chat-mcp-runner --timeout=180s

# Hardened: unprivileged, read-only root, a writable cache volume, no route to the cluster API.
kubectl -n "$NS" exec deploy/rc-rocket-chat-mcp-runner -- python -c "
import os, socket
assert os.getuid() == 1000, 'must not run as root'
open('/cache/probe', 'w').write('x')
try:
    open('/app/x', 'w'); raise SystemExit('the root filesystem must be read-only')
except OSError:
    pass
s = socket.socket(); s.settimeout(5)
try:
    s.connect(('kubernetes.default.svc', 443)); raise SystemExit('the cluster API must be unreachable')
except OSError:
    pass
print('runner pod is hardened')
"

# Only backend pods may connect. Policy controllers take a few seconds to see new pods, so the
# probes wait before they connect.
TOKEN="$(kubectl -n "$NS" get secret rc-rocket-chat-mcp-runner -o jsonpath='{.data.MCP_RUNNER_TOKEN}' | base64 -d)"
cat > "$WORK/probe.py" <<'PY'
import asyncio, json, os

import websockets


async def main() -> None:
    try:
        async with asyncio.timeout(8):
            async with websockets.connect(
                "ws://rc-rocket-chat-mcp-runner:8100/v1/stdio",
                additional_headers={"Authorization": f"Bearer {os.environ['T']}"},
            ) as socket:
                await socket.send(json.dumps({"t": "start", "command": "echo '{}'", "env": {}, "org_id": "o", "server_id": "s"}))
                print("CONNECTED", json.loads(await socket.recv())["t"])
    except Exception as err:
        print("BLOCKED", type(err).__name__)


asyncio.run(main())
PY
kubectl -n "$NS" create configmap probe --from-file="$WORK/probe.py" >/dev/null
probe() {
    name="$1"; labels="$2"
    kubectl -n "$NS" run "$name" --image=rocket-chat-mcp-runner:dev --restart=Never ${labels:+--labels="$labels"} \
        --overrides='{"spec":{"volumes":[{"name":"p","configMap":{"name":"probe"}}],"containers":[{"name":"'"$name"'","image":"rocket-chat-mcp-runner:dev","imagePullPolicy":"Never","command":["sh","-c","sleep 25; python /p/probe.py"],"env":[{"name":"T","value":"'"$TOKEN"'"}],"volumeMounts":[{"name":"p","mountPath":"/p"}]}]}}' >/dev/null
}
probe from-backend "app.kubernetes.io/name=rocket-chat-backend,app.kubernetes.io/instance=rc"
probe from-stranger ""
sleep 50
[ "$(kubectl -n "$NS" logs from-backend | tail -1)" = "CONNECTED ready" ] || { echo "the backend could not reach the runner" >&2; exit 1; }
kubectl -n "$NS" logs from-stranger | tail -1 | grep -q '^BLOCKED' || { echo "another pod reached the runner" >&2; exit 1; }

# The generated token is kept across upgrades (a real install and upgrade of the secret template;
# the rest of the chart is removed so only the runner's secret is installed).
cp -R deploy/helm/platform "$WORK/mini"
find "$WORK/mini/templates" -mindepth 1 -maxdepth 1 ! -name _helpers.tpl ! -name mcp-runner -exec rm -rf {} +
find "$WORK/mini/templates/mcp-runner" -type f ! -name secret.yaml -delete
kubectl create namespace tokens >/dev/null
helm install m "$WORK/mini" -n tokens -f "$VALUES" >/dev/null
first="$(kubectl -n tokens get secret m-rocket-chat-mcp-runner -o jsonpath='{.data.MCP_RUNNER_TOKEN}')"
helm upgrade m "$WORK/mini" -n tokens -f "$VALUES" --set mcpRunner.maxProcesses=40 >/dev/null
second="$(kubectl -n tokens get secret m-rocket-chat-mcp-runner -o jsonpath='{.data.MCP_RUNNER_TOKEN}')"
[ -n "$first" ] && [ "$first" = "$second" ] || { echo "the runner token changed on upgrade" >&2; exit 1; }
echo "mcp-runner chart OK"
