"""A repository named by a template, mission or webhook becomes part of the instruction.

Sessions always start with a blank workspace; the agent clones what the task needs. These
sources know the repository up front, so they say so in the prompt instead of the platform
cloning it on the agent's behalf.
"""

from __future__ import annotations


def with_repository_hint(prompt: str, repository: str | None, branch: str | None = None) -> str:
    if not repository:
        return prompt
    where = f"`{repository}`" + (f" (branch `{branch}`)" if branch else "")
    return f"Repository for this task: {where}. Clone it with `clone_repository` first.\n\n{prompt}"
