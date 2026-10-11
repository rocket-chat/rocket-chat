#!/bin/sh
# Verifies a sandbox image has the tools agents rely on and runs unprivileged-capable.
# Usage: scripts/smoke_sandbox_image.sh [image]   (default: rocket-chat-sandbox:dev)
set -eu
image="${1:-rocket-chat-sandbox:dev}"

run() { docker run --rm --cap-drop ALL --security-opt no-new-privileges:true "$image" sh -c "$1"; }

for tool in git gh rg fd jq node pnpm npm uv python3 make gcc base64 find stat; do
  run "command -v $tool >/dev/null" || { echo "missing tool: $tool" >&2; exit 1; }
done
[ "$(run 'id -u')" = "1000" ] || { echo "image must default to uid 1000" >&2; exit 1; }
run 'touch /workspace/.probe && rm /workspace/.probe' || { echo "/workspace not writable" >&2; exit 1; }
run 'cd /tmp && git init -q probe && git -C probe -c user.name=t -c user.email=t@t commit -q --allow-empty -m probe'

# The browser variant must really load a page under the same restrictions agents run with.
if run 'command -v rocket-browser >/dev/null'; then
  report="$(run 'cd /tmp && printf "<title>smoke</title><h1>ok</h1>" > index.html \
    && (python3 -m http.server 4173 >/dev/null 2>&1 &) && sleep 1 \
    && rocket-browser http://127.0.0.1:4173/')"
  echo "$report" | grep -q '"title":"smoke"' || { echo "browser could not load a page: $report" >&2; exit 1; }
fi
echo "OK: $image"
