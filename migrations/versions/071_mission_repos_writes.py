"""Missions name several repositories and say whether their runs may change GitHub.

Revision ID: 071_mission_repos_writes
Revises: 070_change_proposals
Create Date: 2026-11-17 10:00:00.000000

``git_repos`` replaces ``git_repo`` (a mission's one repository becomes the first of its list,
as ``owner/name`` like the API now stores it).
``github_write`` says whether runs may push, open pull requests and write through the GitHub
tools; a new mission reads only, but every mission stored before this revision could write, so
it keeps writing.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "071_mission_repos_writes"
down_revision: str | None = "070_change_proposals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "missions"
_GITHUB_URL = "https://github.com/"


def _slug(repository: str) -> str:
    return repository.removeprefix(_GITHUB_URL).removesuffix(".git")


def _json() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _missions(*columns: sa.Column[object]) -> sa.TableClause:
    return sa.table(_TABLE, sa.column("id", sa.String()), *columns)


def upgrade() -> None:
    op.add_column(
        _TABLE, sa.Column("git_repos", _json(), nullable=False, server_default=sa.text("'[]'"))
    )
    op.add_column(
        _TABLE,
        sa.Column("github_write", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    bind = op.get_bind()
    old = _missions(sa.column("git_repo", sa.String()))
    new = _missions(sa.column("git_repos", _json()), sa.column("github_write", sa.Boolean()))
    for mission_id, repository in bind.execute(sa.select(old.c.id, old.c.git_repo)).all():
        bind.execute(
            new.update()
            .where(new.c.id == mission_id)
            .values(git_repos=[_slug(repository)] if repository else [], github_write=True)
        )
    op.drop_column(_TABLE, "git_repo")


def downgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("git_repo", sa.String(length=255), nullable=False, server_default=""),
    )
    bind = op.get_bind()
    old = _missions(sa.column("git_repo", sa.String()))
    new = _missions(sa.column("git_repos", _json()))
    for mission_id, repositories in bind.execute(sa.select(new.c.id, new.c.git_repos)).all():
        first = repositories[0] if isinstance(repositories, list) and repositories else ""
        bind.execute(old.update().where(old.c.id == mission_id).values(git_repo=first))
    op.drop_column(_TABLE, "github_write")
    op.drop_column(_TABLE, "git_repos")
