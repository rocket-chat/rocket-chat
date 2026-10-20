"""An organization's Slack bots: each a Slack app with its own tokens, agent or router and policy.

Tokens enter sealed with ``CredentialCipher`` and leave only as fingerprints, except to the bot
manager that opens the bots' connections (``enabled_bots``). Rows are protected by row-level
security; the manager reads every organization's bots on the schema-owner connection.
"""

from __future__ import annotations

import builtins
import re
import time
import uuid
from dataclasses import dataclass, field, fields, replace
from typing import Any

from config_engine.crypto import CredentialCipher
from slack_assistant.bot import RESPONSE_MODES, SlackBot, SlackRouter, command_for
from sqlalchemy import delete, select

from api.db.models import SlackBotModel
from api.db.session import DatabaseSessionManager

MAX_BOTS_PER_ORG = 20
_COMMAND = re.compile(r"/[a-z0-9_-]{1,31}")
# The organization's `slack` settings a bot may override (the rest stay organization-wide).
POLICY_KEYS = frozenset(
    {
        "dm_policy",
        "outbound_channels",
        "allowed_channel_types",
        "allowed_tools",
        "live_progress",
        "long_output_to_file",
        "long_output_chars",
        "channel_profiles",
    }
)


class SlackBotError(ValueError):
    """The bot is not acceptable as submitted."""


@dataclass
class SlackBotRecord:
    """A bot as stored, without its tokens."""

    id: str
    org_id: str
    name: str
    enabled: bool = True
    bot_token_fingerprint: str | None = None
    app_token_fingerprint: str | None = None
    team_id: str | None = None
    team_name: str | None = None
    bot_user_id: str | None = None
    app_id: str | None = None
    handle: str | None = None
    slash_command: str | None = None
    agent_id: str | None = None
    response_mode: str = "thread"
    respond_in_dms: bool = True
    listen_channels: list[str] = field(default_factory=list)
    private_reaction: str = "white_check_mark"
    private_dm_result: bool = False
    private_dm_updates: bool = False
    router: dict[str, Any] | None = None
    policy: dict[str, Any] = field(default_factory=dict)
    created_by: str | None = None
    created_at: int = 0
    updated_at: int = 0


@dataclass(frozen=True)
class SlackTokens:
    bot_token: str
    app_token: str


@dataclass(frozen=True)
class SlackIdentity:
    """What ``auth.test`` says the bot token belongs to."""

    team_id: str
    team_name: str
    bot_user_id: str
    app_id: str
    handle: str


_RECORD_FIELDS = tuple(f.name for f in fields(SlackBotRecord))
EDITABLE = frozenset(
    {
        "name",
        "enabled",
        "slash_command",
        "agent_id",
        "response_mode",
        "respond_in_dms",
        "listen_channels",
        "private_reaction",
        "private_dm_result",
        "private_dm_updates",
        "router",
        "policy",
    }
)


def validate(record: SlackBotRecord) -> SlackBotRecord:
    """Normalise and check what an administrator submitted."""
    if not record.name.strip():
        raise SlackBotError("A bot needs a name.")
    if record.response_mode not in RESPONSE_MODES:
        raise SlackBotError(f"Response mode must be one of {', '.join(RESPONSE_MODES)}.")
    unknown = set(record.policy) - POLICY_KEYS
    if unknown:
        raise SlackBotError(f"Unknown policy keys: {', '.join(sorted(unknown))}.")
    if record.router is not None:
        _validate_router(record.router)
    command = (record.slash_command or "").strip().lower() or None
    if command is not None and not _COMMAND.fullmatch(command):
        raise SlackBotError(
            "A slash command is '/' then up to 31 lowercase letters, digits, '-' or '_'."
        )
    return replace(
        record,
        name=record.name.strip()[:100],
        slash_command=command,
        listen_channels=sorted({c.strip() for c in record.listen_channels if c.strip()}),
        private_reaction=record.private_reaction.strip(":") or "white_check_mark",
    )


def _validate_router(raw: dict[str, Any]) -> None:
    router = SlackRouter.from_mapping(raw)
    if router is None:
        raise SlackBotError("A router needs a classifier agent.")
    names = [r.name for r in router.routes]
    if any(not n for n in names) or len(names) != len(set(names)):
        raise SlackBotError("Every route needs a unique name.")
    if "dismiss" in names:
        raise SlackBotError("'dismiss' is reserved; enable dismissal instead.")
    if not router.active_routes():
        raise SlackBotError("A router needs at least one enabled route with an agent.")
    if not 0.0 <= router.min_confidence <= 1.0:
        raise SlackBotError("Minimum confidence is between 0 and 1.")


def slash_command_of(record: SlackBotRecord) -> str:
    """The bot's own command if it chose one, else one named after its handle."""
    return record.slash_command or command_for(record.handle or record.name)


def runtime_bot(record: SlackBotRecord, tokens: SlackTokens) -> SlackBot:
    """The package's view of a stored bot, ready to connect."""
    return SlackBot(
        id=record.id,
        org_id=record.org_id,
        name=record.name,
        bot_token=tokens.bot_token,
        app_token=tokens.app_token,
        agent_id=record.agent_id or "",
        router=SlackRouter.from_mapping(record.router),
        response_mode=record.response_mode,
        respond_in_dms=record.respond_in_dms,
        listen_channels=frozenset(record.listen_channels),
        private_reaction=record.private_reaction,
        private_dm_result=record.private_dm_result,
        private_dm_updates=record.private_dm_updates,
        policy=dict(record.policy),
        command=slash_command_of(record),
    )


def _to_record(row: SlackBotModel) -> SlackBotRecord:
    return SlackBotRecord(**{name: getattr(row, name) for name in _RECORD_FIELDS})


class SlackBotStore:
    """Bots in PostgreSQL (tenant-scoped); a process-local dict without a database."""

    def __init__(
        self, db_manager: DatabaseSessionManager | None, cipher: CredentialCipher | None = None
    ) -> None:
        self._db = (
            db_manager
            if db_manager is not None and getattr(db_manager, "session_factory", None)
            else None
        )
        self._cipher = cipher or CredentialCipher()
        self._memory: dict[str, tuple[SlackBotRecord, str | None, str | None]] = {}

    async def list(self, org_id: str) -> list[SlackBotRecord]:
        if self._db is None:
            found = [r for r, _, _ in self._memory.values() if r.org_id == org_id]
        else:
            async with self._db.session(tenant_org_id=org_id) as session:
                stmt = select(SlackBotModel).where(SlackBotModel.org_id == org_id)
                found = [_to_record(r) for r in (await session.execute(stmt)).scalars()]
        return sorted(found, key=lambda r: r.created_at)

    async def get(self, org_id: str, bot_id: str) -> SlackBotRecord | None:
        return next((r for r in await self.list(org_id) if r.id == bot_id), None)

    async def create(self, record: SlackBotRecord, tokens: SlackTokens | None) -> SlackBotRecord:
        if len(await self.list(record.org_id)) >= MAX_BOTS_PER_ORG:
            raise SlackBotError(f"An organization can have at most {MAX_BOTS_PER_ORG} bots.")
        now = int(time.time())
        record = replace(validate(record), id=uuid.uuid4().hex[:12], created_at=now, updated_at=now)
        sealed = self._seal(record, tokens)
        if self._db is None:
            self._memory[record.id] = sealed
            return sealed[0]
        async with self._db.session(tenant_org_id=record.org_id) as session:
            session.add(self._row(*sealed))
        return sealed[0]

    async def update(
        self,
        org_id: str,
        bot_id: str,
        changes: dict[str, Any],
        tokens: SlackTokens | None = None,
        identity: SlackIdentity | None = None,
    ) -> SlackBotRecord | None:
        current = await self.get(org_id, bot_id)
        if current is None:
            return None
        editable = {k: v for k, v in changes.items() if k in EDITABLE}
        record = validate(replace(current, **editable, updated_at=int(time.time())))
        if identity is not None:
            record = replace(record, **vars(identity))
        sealed = self._seal(record, tokens) if tokens is not None else None
        if self._db is None:
            _, bot_enc, app_enc = self._memory[bot_id]
            self._memory[bot_id] = sealed or (record, bot_enc, app_enc)
            return record
        async with self._db.session(tenant_org_id=org_id) as session:
            row = await session.get(SlackBotModel, bot_id)
            if row is None or row.org_id != org_id:
                return None
            for name in _RECORD_FIELDS:
                setattr(row, name, getattr(record, name))
            if sealed is not None:
                row.bot_token_encrypted, row.app_token_encrypted = sealed[1], sealed[2]
        return record

    async def delete(self, org_id: str, bot_id: str) -> bool:
        if self._db is None:
            found = self._memory.get(bot_id)
            if found is None or found[0].org_id != org_id:
                return False
            del self._memory[bot_id]
            return True
        async with self._db.session(tenant_org_id=org_id) as session:
            result = await session.execute(
                delete(SlackBotModel).where(
                    SlackBotModel.id == bot_id, SlackBotModel.org_id == org_id
                )
            )
        return bool(getattr(result, "rowcount", 0))

    async def tokens(self, org_id: str, bot_id: str) -> SlackTokens | None:
        """The bot's tokens, for the platform's own Slack calls (never for a response)."""
        if self._db is None:
            found = self._memory.get(bot_id)
            if found is None or found[0].org_id != org_id:
                return None
            return self._open(found[1], found[2])
        async with self._db.session(tenant_org_id=org_id) as session:
            row = await session.get(SlackBotModel, bot_id)
            if row is None or row.org_id != org_id:
                return None
            return self._open(row.bot_token_encrypted, row.app_token_encrypted)

    async def enabled_bots(
        self, owner_db: DatabaseSessionManager | None
    ) -> builtins.list[tuple[SlackBotRecord, SlackTokens]]:
        """Every organization's enabled bots that have both tokens, for the bot manager.

        Reads across tenants, so it runs on the schema-owner connection like the other
        cross-tenant jobs (missions, retention).
        """
        if self._db is None or owner_db is None:
            rows = [(r, b, a) for r, b, a in self._memory.values()]
        else:
            async with owner_db.session() as session:
                stmt = select(SlackBotModel).where(SlackBotModel.enabled.is_(True))
                rows = [
                    (_to_record(r), r.bot_token_encrypted, r.app_token_encrypted)
                    for r in (await session.execute(stmt)).scalars()
                ]
        ready = []
        for record, bot_enc, app_enc in rows:
            tokens = self._open(bot_enc, app_enc)
            if record.enabled and tokens is not None:
                ready.append((record, tokens))
        return ready

    def _seal(
        self, record: SlackBotRecord, tokens: SlackTokens | None
    ) -> tuple[SlackBotRecord, str | None, str | None]:
        if tokens is None:
            return record, None, None
        return (
            replace(
                record,
                bot_token_fingerprint=CredentialCipher.fingerprint(tokens.bot_token),
                app_token_fingerprint=CredentialCipher.fingerprint(tokens.app_token),
            ),
            self._cipher.encrypt(tokens.bot_token),
            self._cipher.encrypt(tokens.app_token),
        )

    def _open(self, bot_enc: str | None, app_enc: str | None) -> SlackTokens | None:
        if not (bot_enc and app_enc):
            return None
        return SlackTokens(self._cipher.decrypt(bot_enc), self._cipher.decrypt(app_enc))

    @staticmethod
    def _row(record: SlackBotRecord, bot_enc: str | None, app_enc: str | None) -> SlackBotModel:
        return SlackBotModel(
            **vars(record), bot_token_encrypted=bot_enc, app_token_encrypted=app_enc
        )
