#!/bin/sh
# Stands in for the GitHub CLI in Rocket Chat sandboxes (see Dockerfile.sandbox).
cat >&2 <<'EOF2'
gh is not available in this sandbox: it holds no GitHub credential.
Use the agent's GitHub tools instead (they act on the platform's side):
  pull requests  -> github_pull_request (view, diff, checks, review_threads, comment, review, merge)
  issues         -> github_issues (read, create, comment, edit, close)
  CI runs        -> github_actions (runs, run_view, job_logs, rerun_failed)
  releases       -> github_releases;  search -> github_search
  anything else  -> github_api (REST or GraphQL)
  publish        -> git_push / git_open_pr;  clone -> clone_repository (or plain git clone)
EOF2
exit 1
