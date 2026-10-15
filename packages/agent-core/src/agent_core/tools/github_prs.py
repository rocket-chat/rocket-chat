"""Pull requests and issues on GitHub: what ``gh pr`` and ``gh issue`` do, as agent tools.

Reads use the turn's read credential; changes use the write credential the platform resolves
for the repository (``GithubAccess.actor`` says who acted). Results are short and bounded.
"""

from __future__ import annotations

from typing import Any

from agent_core.tools.clamp import clamp_output
from agent_core.tools.github_client import GithubError, acted_as, graphql, paginate, request
from specifications.interfaces.agent import GithubAccess

PR_READ_ACTIONS = frozenset({"list", "view", "files", "diff", "checks", "review_threads"})
PR_WRITE_ACTIONS = frozenset(
    {
        "edit",
        "ready",
        "draft",
        "request_reviewers",
        "comment",
        "review",
        "reply_to_thread",
        "resolve_thread",
        "close",
        "reopen",
        "merge",
    }
)
ISSUE_WRITE_ACTIONS = frozenset({"create", "comment", "edit", "close", "reopen"})
DIFF_FILES_PER_PAGE = 10
REVIEW_EVENTS = {"comment": "COMMENT", "request_changes": "REQUEST_CHANGES", "approve": "APPROVE"}

_THREADS = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 50) {
        nodes {
          id isResolved path line
          comments(first: 20) { nodes { databaseId author { login } body } }
        }
      }
    }
  }
}"""


def _pr_line(pull: dict[str, Any]) -> str:
    draft = " draft" if pull.get("draft") else ""
    return (
        f"#{pull.get('number')} [{pull.get('state')}{draft}] {pull.get('title')} "
        f"({pull['head']['ref']} -> {pull['base']['ref']}; by {(pull.get('user') or {}).get('login')})"
    )


async def list_pulls(access: GithubAccess, slug: str, state: str) -> str:
    pulls = await request(
        access, "GET", f"/repos/{slug}/pulls", params={"state": state, "per_page": 20}
    )
    if not pulls:
        return f"No {state} pull requests in {slug}."
    return "\n".join(_pr_line(p) for p in pulls)


async def files(access: GithubAccess, slug: str, number: int) -> str:
    changed = await paginate(access, f"/repos/{slug}/pulls/{number}/files")
    lines = [
        f"{f.get('status')}  {f.get('filename')}  +{f.get('additions')} -{f.get('deletions')}"
        for f in changed
    ]
    return f"{len(changed)} files changed in #{number}:\n" + "\n".join(lines)


async def diff(access: GithubAccess, slug: str, number: int, page: int) -> str:
    changed = await paginate(access, f"/repos/{slug}/pulls/{number}/files")
    start = max(0, page - 1) * DIFF_FILES_PER_PAGE
    window = changed[start : start + DIFF_FILES_PER_PAGE]
    if not window:
        return f"#{number} has {len(changed)} changed files; page {page} is past the end."
    parts = [
        f"--- {f.get('filename')}\n{f.get('patch') or '(binary or too large to show)'}"
        for f in window
    ]
    pages = (len(changed) + DIFF_FILES_PER_PAGE - 1) // DIFF_FILES_PER_PAGE
    footer = (
        f"\n[files {start + 1}-{start + len(window)} of {len(changed)}; page {page} of {pages}]"
    )
    return clamp_output("\n".join(parts) + footer)


async def checks(access: GithubAccess, slug: str, number: int) -> str:
    pull = await request(access, "GET", f"/repos/{slug}/pulls/{number}")
    sha = pull["head"]["sha"]
    runs = await request(
        access, "GET", f"/repos/{slug}/commits/{sha}/check-runs", params={"per_page": 100}
    )
    lines = [
        f"{r.get('conclusion') or r.get('status')}  {r.get('name')}  (check run {r.get('id')})"
        for r in runs.get("check_runs", [])
    ]
    if not lines:
        return f"No checks reported on {sha[:8]} yet."
    failing = sum(1 for r in runs.get("check_runs", []) if r.get("conclusion") == "failure")
    hint = " Use github_actions job_logs on a failing job to see why." if failing else ""
    return f"Checks on {sha[:8]} ({failing} failing).{hint}\n" + "\n".join(lines)


async def review_threads(access: GithubAccess, slug: str, number: int) -> str:
    owner, name = slug.split("/", 1)
    data = await graphql(access, _THREADS, {"owner": owner, "name": name, "number": number})
    threads = data["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    if not threads:
        return f"No review threads on #{number}."
    blocks = []
    for thread in threads:
        state = "resolved" if thread["isResolved"] else "open"
        head = f"thread {thread['id']} [{state}] {thread.get('path')}:{thread.get('line')}"
        comments = [
            f"  comment {c['databaseId']} by {(c.get('author') or {}).get('login')}: "
            + " ".join((c.get("body") or "").split())[:400]
            for c in thread["comments"]["nodes"]
        ]
        blocks.append("\n".join([head, *comments]))
    return clamp_output("\n".join(blocks))


async def _node_id(access: GithubAccess, slug: str, number: int) -> str:
    return str((await request(access, "GET", f"/repos/{slug}/pulls/{number}"))["node_id"])


async def write_pull(
    access: GithubAccess, slug: str, number: int, action: str, fields: dict[str, Any]
) -> str:
    """One change to a pull request; returns what happened and who did it."""
    handler = _PULL_WRITES[action]
    done = await handler(access, slug, number, fields)
    return f"{done}{acted_as(access)}"


async def _edit(access: GithubAccess, slug: str, number: int, f: dict[str, Any]) -> str:
    body = {k: f[k] for k in ("title", "body", "base") if f.get(k)}
    if not body:
        raise GithubError("Nothing to change: give a title, a body or a base branch.")
    await request(access, "PATCH", f"/repos/{slug}/pulls/{number}", body=body)
    return f"Updated #{number} ({', '.join(body)})"


async def _ready(access: GithubAccess, slug: str, number: int, f: dict[str, Any]) -> str:
    mutation = "mutation($id: ID!) { markPullRequestReadyForReview(input: {pullRequestId: $id}) { clientMutationId } }"
    await graphql(access, mutation, {"id": await _node_id(access, slug, number)})
    return f"Marked #{number} ready for review"


async def _draft(access: GithubAccess, slug: str, number: int, f: dict[str, Any]) -> str:
    mutation = "mutation($id: ID!) { convertPullRequestToDraft(input: {pullRequestId: $id}) { clientMutationId } }"
    await graphql(access, mutation, {"id": await _node_id(access, slug, number)})
    return f"Turned #{number} back into a draft"


async def _reviewers(access: GithubAccess, slug: str, number: int, f: dict[str, Any]) -> str:
    people = [r for r in f.get("reviewers") or [] if r]
    if not people:
        raise GithubError("Name at least one reviewer (GitHub login).")
    await request(
        access,
        "POST",
        f"/repos/{slug}/pulls/{number}/requested_reviewers",
        body={"reviewers": people},
    )
    return f"Requested reviews on #{number} from {', '.join(people)}"


async def _comment(access: GithubAccess, slug: str, number: int, f: dict[str, Any]) -> str:
    text = _required(f, "body")
    made = await request(
        access, "POST", f"/repos/{slug}/issues/{number}/comments", body={"body": text}
    )
    return f"Commented on #{number}: {made.get('html_url', '')}"


async def _review(access: GithubAccess, slug: str, number: int, f: dict[str, Any]) -> str:
    event = REVIEW_EVENTS.get(str(f.get("event") or "comment"))
    if event is None:
        raise GithubError("The review event is one of: comment, request_changes, approve.")
    await request(
        access,
        "POST",
        f"/repos/{slug}/pulls/{number}/reviews",
        body={"event": event, "body": f.get("body") or ""},
    )
    return f"Submitted a {event.lower().replace('_', ' ')} review on #{number}"


async def _reply(access: GithubAccess, slug: str, number: int, f: dict[str, Any]) -> str:
    comment_id = f.get("comment_id")
    if not comment_id:
        raise GithubError("Give the comment_id to reply to (from review_threads).")
    await request(
        access,
        "POST",
        f"/repos/{slug}/pulls/{number}/comments/{int(comment_id)}/replies",
        body={"body": _required(f, "body")},
    )
    return f"Replied to review comment {comment_id} on #{number}"


async def _resolve(access: GithubAccess, slug: str, number: int, f: dict[str, Any]) -> str:
    thread = _required(f, "thread_id")
    mutation = "mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { isResolved } } }"
    await graphql(access, mutation, {"id": thread})
    return f"Resolved review thread {thread} on #{number}"


async def _state(access: GithubAccess, slug: str, number: int, state: str) -> str:
    await request(access, "PATCH", f"/repos/{slug}/pulls/{number}", body={"state": state})
    return f"{'Closed' if state == 'closed' else 'Reopened'} #{number}"


async def _close(access: GithubAccess, slug: str, number: int, f: dict[str, Any]) -> str:
    return await _state(access, slug, number, "closed")


async def _reopen(access: GithubAccess, slug: str, number: int, f: dict[str, Any]) -> str:
    return await _state(access, slug, number, "open")


async def _merge(access: GithubAccess, slug: str, number: int, f: dict[str, Any]) -> str:
    method = str(f.get("merge_method") or "squash")
    if method not in ("merge", "squash", "rebase"):
        raise GithubError("merge_method is one of: merge, squash, rebase.")
    result = await request(
        access, "PUT", f"/repos/{slug}/pulls/{number}/merge", body={"merge_method": method}
    )
    return f"Merged #{number} ({method}): {str((result or {}).get('sha', ''))[:12]}"


def _required(fields: dict[str, Any], key: str) -> str:
    value = str(fields.get(key) or "").strip()
    if not value:
        raise GithubError(f"'{key}' is required for this action.")
    return value


_PULL_WRITES = {
    "edit": _edit,
    "ready": _ready,
    "draft": _draft,
    "request_reviewers": _reviewers,
    "comment": _comment,
    "review": _review,
    "reply_to_thread": _reply,
    "resolve_thread": _resolve,
    "close": _close,
    "reopen": _reopen,
    "merge": _merge,
}


async def write_issue(
    access: GithubAccess, slug: str, number: int | None, action: str, fields: dict[str, Any]
) -> str:
    """Create, comment on, edit, close or reopen an issue."""
    if action == "create":
        body = {"title": _required(fields, "title"), "body": fields.get("body") or ""}
        for key in ("labels", "assignees"):
            if fields.get(key):
                body[key] = fields[key]
        made = await request(access, "POST", f"/repos/{slug}/issues", body=body)
        return f"Opened issue #{made.get('number')}: {made.get('html_url', '')}{acted_as(access)}"
    if number is None:
        raise GithubError("Give the issue number.")
    if action == "comment":
        return await write_pull(access, slug, number, "comment", fields)
    if action in ("close", "reopen"):
        state = "closed" if action == "close" else "open"
        await request(access, "PATCH", f"/repos/{slug}/issues/{number}", body={"state": state})
        return f"{'Closed' if state == 'closed' else 'Reopened'} #{number}{acted_as(access)}"
    body = {k: fields[k] for k in ("title", "body", "labels", "assignees") if fields.get(k)}
    if not body:
        raise GithubError("Nothing to change: give a title, body, labels or assignees.")
    await request(access, "PATCH", f"/repos/{slug}/issues/{number}", body=body)
    return f"Updated issue #{number} ({', '.join(body)}){acted_as(access)}"
