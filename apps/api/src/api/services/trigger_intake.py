"""From a parsed event to a session: which organization it belongs to, whether this delivery was
already handled, which trigger takes it and who owns what it starts.

Forge webhooks carry no Rocket identity: the organization is the one that mapped the installation
the event came through (only an unmapped installation falls back to ``ROCKET_WEBHOOK_ORG_ID``),
and the session belongs to a member made for the event's author (``gh_<login>``).
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from api.db.store import ensure_tenant_identity
from api.services.github_installations import installation_org
from api.services.triggers import (
    TriggerOutcome,
    claim_delivery,
    matching_trigger,
    start_from_trigger,
)
from specifications.interfaces.automation import TriggerEvent

_FORGE_PREFIX = {"github": "gh", "gitlab": "gl", "jira": "jira", "linear": "linear"}


async def webhook_identity(
    app: FastAPI, sender_login: str, installation_id: int | None, forge: str = "github"
) -> tuple[str, str]:
    """The organization and member that own what a webhook starts."""
    db_mgr = getattr(app.state, "db_manager", None)
    mapped = (
        await installation_org(db_mgr, installation_id) if installation_id is not None else None
    )
    org_id: str = mapped or os.getenv("ROCKET_WEBHOOK_ORG_ID") or "default_org"
    safe_login = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in sender_login)
    return org_id, f"{_FORGE_PREFIX.get(forge, forge)}_{safe_login}"


async def receive_trigger_event(
    app: FastAPI, event: TriggerEvent, org_id: str | None = None
) -> TriggerOutcome:
    """Start or wake the session the event asks for, once per delivery."""
    forge = event.source.split("_", 1)[0]
    mapped_org, owner = await webhook_identity(app, event.actor, event.installation_id, forge)
    org_id = org_id or mapped_org
    db_mgr = getattr(app.state, "db_manager", None)
    trigger = await matching_trigger(db_mgr, org_id, event)
    if trigger is None:
        return TriggerOutcome("ignored", reason="No trigger of the organization takes this event")
    if not await claim_delivery(db_mgr, org_id, forge, event.delivery_id):
        return TriggerOutcome("duplicate", trigger_id=trigger.id)
    if db_mgr is not None:
        async with db_mgr.session(tenant_org_id=org_id) as session:
            await ensure_tenant_identity(session, org_id, owner)
    return await start_from_trigger(app, trigger, event, owner)
