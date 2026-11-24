"""Where a skill comes from, as people see it, and what that forbids.

A skill imported from an external source shows its source (repository, branch, folder, plugin)
and how it stands against it. Its name, description and instructions are not edited on the
platform: a change becomes a pull request (``source_changes.py``). A skill from a source read
with a member's own GitHub account stays that member's personal skill.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from git_engine.config import github_web_url

from api.services.external_sources import external_source_store
from api.services.git_gateway_env import github_api_url_of
from api.services.skill_store import SkillContent
from specifications.interfaces.external_sources import ExternalSource, SourceLink
from specifications.interfaces.ownership import Owner

SKILL_KIND = "skill"


@dataclass(frozen=True)
class SkillSourceView:
    source_id: str
    name: str
    repository: str
    ref: str
    path: str
    plugin: str | None
    commit: str
    status: str
    problem: str
    url: str
    files: tuple[str, ...]


def repository_url(state: Any, repository: str, ref: str = "", path: str = "") -> str:
    """The repository, or one folder of it at ``ref``, on GitHub's web interface."""
    base = f"{github_web_url(github_api_url_of(getattr(state, 'git_engine', None)))}/{repository}"
    if not ref:
        return base
    return f"{base}/tree/{quote(ref, safe='/')}/{quote(path, safe='/')}".rstrip("/")


def view_of(state: Any, link: SourceLink, source: ExternalSource) -> SkillSourceView:
    return SkillSourceView(
        source_id=source.id,
        name=source.name,
        repository=source.repository,
        ref=source.ref,
        path=link.path,
        plugin=link.plugin,
        commit=link.commit,
        status=link.status,
        problem=link.problem,
        url=repository_url(state, source.repository, source.ref, link.path),
        files=tuple(f.path for f in link.files),
    )


async def source_views(state: Any, org_id: str, skill_ids: list[str]) -> dict[str, SkillSourceView]:
    """The source of each of these skills that has one, by skill id."""
    store = external_source_store(getattr(state, "db_manager", None))
    links = await store.links_of_objects(org_id, SKILL_KIND, skill_ids)
    sources: dict[str, ExternalSource | None] = {}
    views: dict[str, SkillSourceView] = {}
    for skill_id, link in links.items():
        if link.source_id not in sources:
            sources[link.source_id] = await store.get_source(org_id, link.source_id)
        source = sources[link.source_id]
        if source is not None:
            views[skill_id] = view_of(state, link, source)
    return views


async def link_and_source(
    state: Any, org_id: str, skill_id: str
) -> tuple[SourceLink, ExternalSource] | None:
    store = external_source_store(getattr(state, "db_manager", None))
    link = (await store.links_of_objects(org_id, SKILL_KIND, [skill_id])).get(skill_id)
    if link is None:
        return None
    source = await store.get_source(org_id, link.source_id)
    return (link, source) if source is not None else None


async def edit_refusal(
    state: Any,
    org_id: str,
    skill_id: str,
    current: SkillContent,
    requested: SkillContent,
    destination: Owner,
) -> str | None:
    """Why saving ``requested`` (for ``destination``) over a skill linked to a source is
    refused, or None when the skill has no source or only its owner or activation changes."""
    found = await link_and_source(state, org_id, skill_id)
    if found is None:
        return None
    _, source = found
    if requested != current:
        return (
            f"This skill comes from {source.repository} ({source.name}): its content changes "
            "through the repository. Propose the change instead; it opens a pull request."
        )
    if source.auth == "member" and (
        destination.scope != "user" or destination.user_id != source.user_id
    ):
        return (
            "This skill comes from a source read with its owner's own GitHub account, so it "
            "stays their personal skill."
        )
    return None


async def forget_source_of(state: Any, org_id: str, skill_id: str) -> None:
    """The skill was deleted: it no longer comes from anywhere."""
    store = external_source_store(getattr(state, "db_manager", None))
    await store.delete_link(org_id, SKILL_KIND, skill_id)
