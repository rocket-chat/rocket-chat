"""``github_api``: any GitHub REST or GraphQL call the dedicated tools do not cover (``gh api``).

Reads (REST ``GET``/``HEAD``, GraphQL queries) use the turn's read credential. Anything else is
a change, made with the write credential the platform resolves for the repository in the path,
and only on repository resources. Administrative endpoints are refused whatever the mode:
secrets, deploy keys, webhooks, branch protection and rulesets, collaborators and teams,
organization membership and billing, App installations and tokens, and deleting, transferring or
reconfiguring a repository.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from agent_core.tools.clamp import clamp_output
from agent_core.tools.github_client import GithubError, acted_as, graphql, paginate, request
from specifications.interfaces.agent import GithubAccess

READ_METHODS = frozenset({"GET", "HEAD"})
WRITE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})
_REPO = r"/repos/[^/]+/[^/]+"
# Refused for every method: reading them is already more than an agent needs.
_ALWAYS_DENIED = [
    rf"^{_REPO}/(actions|dependabot|codespaces)/(secrets|variables)(/|$)",
    rf"^{_REPO}/environments/[^/]+/(secrets|variables)(/|$)",
    r"^/orgs/[^/]+/(actions|dependabot|codespaces)/(secrets|variables)(/|$)",
    rf"^{_REPO}/(keys|hooks)(/|$)",
    r"^/orgs/[^/]+/hooks(/|$)",
    r"^/(app|installation|applications|authorizations|admin|enterprises)(/|$)",
    r"^/user/(installations|keys|gpg_keys|ssh_signing_keys|emails|social_accounts)(/|$)",
]
# Refused for changes; reading them is harmless.
_WRITE_DENIED = [
    rf"^{_REPO}/?$",  # delete or reconfigure the repository
    rf"^{_REPO}/transfer$",
    rf"^{_REPO}/(collaborators|teams|invitations)(/|$)",
    rf"^{_REPO}/branches/[^/]+/protection(/|$)",
    rf"^{_REPO}/rulesets(/|$)",
    r"^/orgs/[^/]+(/|$)",
    r"^/user(/|$)",
]
_DENIED_MUTATIONS = re.compile(
    r"\b(delete|archive|transfer|update)Repository\b|BranchProtection|Ruleset|DeployKey|"
    r"Webhook|Secret|Team|Membership|Collaborator|Enterprise|Installation",
    re.IGNORECASE,
)
_REPO_OF_PATH = re.compile(r"^/repos/([^/]+)/([^/]+)")


def normalized_path(raw: str) -> str:
    """The REST path the call may use, or a refusal for anything that is not one."""
    path = raw.strip()
    if not path.startswith("/") or "://" in path or "//" in path or ".." in path.split("/"):
        raise GithubError("Give a REST path relative to the API, e.g. /repos/owner/name/issues.")
    return path.split("?", 1)[0].rstrip("/") or "/"


def refusal(method: str, path: str) -> str | None:
    """Why the platform refuses this call, or None."""
    if any(re.search(p, path) for p in _ALWAYS_DENIED):
        return "This endpoint (secrets, keys, webhooks, installations, administration) is never available to the agent."
    if method in WRITE_METHODS and any(re.search(p, path) for p in _WRITE_DENIED):
        return "Changing repository settings, access, protection or organization resources is not available to the agent."
    return None


def is_mutation(query: str) -> bool:
    return bool(re.match(r"^\s*mutation\b", query or ""))


def call_is_read(arguments: dict[str, Any]) -> bool:
    if arguments.get("graphql"):
        return not is_mutation(str(arguments["graphql"]))
    return str(arguments.get("method") or "GET").upper() in READ_METHODS


def call_is_critical(arguments: dict[str, Any]) -> bool:
    return str(arguments.get("method") or "").upper() == "DELETE"


async def call(
    read: GithubAccess,
    write_for: Callable[[str], Awaitable[GithubAccess]] | None,
    arguments: dict[str, Any],
) -> str:
    if arguments.get("graphql"):
        return await _graphql_call(read, write_for, arguments)
    method = str(arguments.get("method") or "GET").upper()
    if method not in READ_METHODS | WRITE_METHODS:
        raise GithubError("method is one of GET, POST, PATCH, PUT, DELETE.")
    path = normalized_path(str(arguments.get("path") or ""))
    if reason := refusal(method, path):
        raise GithubError(reason)
    if method in READ_METHODS:
        if arguments.get("paginate"):
            result: Any = await paginate(read, path, arguments.get("query"))
        else:
            result = await request(read, method, path, params=arguments.get("query"))
        return _render(result)
    access = await _write_access(write_for, path)
    result = await request(
        access, method, path, params=arguments.get("query"), body=arguments.get("body")
    )
    return f"{method} {path} done{acted_as(access)}.\n" + _render(result)


async def _write_access(
    write_for: Callable[[str], Awaitable[GithubAccess]] | None, path: str
) -> GithubAccess:
    match = _REPO_OF_PATH.match(path)
    if match is None:
        raise GithubError("github_api changes only repository resources (/repos/owner/name/...).")
    if write_for is None:
        raise GithubError("Changes on GitHub are not available in this session.")
    return await write_for(f"{match.group(1)}/{match.group(2)}")


async def _graphql_call(
    read: GithubAccess,
    write_for: Callable[[str], Awaitable[GithubAccess]] | None,
    arguments: dict[str, Any],
) -> str:
    query = str(arguments["graphql"])
    variables = arguments.get("variables") or {}
    if not is_mutation(query):
        return _render(await graphql(read, query, variables))
    if _DENIED_MUTATIONS.search(query):
        raise GithubError(
            "That mutation changes settings, access or secrets and is not available to the agent."
        )
    repository = str(arguments.get("repository") or "")
    if not re.match(r"^[^/\s]+/[^/\s]+$", repository):
        raise GithubError("A GraphQL mutation needs the repository it changes (owner/name).")
    access = await _write_access(write_for, f"/repos/{repository}")
    return f"Mutation done{acted_as(access)}.\n" + _render(await graphql(access, query, variables))


def _render(result: Any) -> str:
    if result is None:
        return "(no content)"
    text = result if isinstance(result, str) else json.dumps(result, indent=1, default=str)
    return clamp_output(text, hint="narrow the query or use paginate=false with page/per_page")
