"""The pull request card of a Slack thread: posted when a turn opens a pull request, then kept
current as GitHub reports CI and the pull request's end.

Cards are stored on the session (``slack_pr_cards``), so whichever replica receives the GitHub
webhook updates them: posting needs the bot's token only, not its Socket Mode connection.
"""

from __future__ import annotations

import logging
from typing import Any

from slack_assistant.cards import (
    MAX_PR_CARDS,
    PR_CARDS_KEY,
    PullRequestCard,
    build_pull_request_card,
    ci_state_of,
    with_ci,
)
from slack_assistant.policy import SlackOrgPolicy
from slack_assistant.turn_stream import SlackTurnStreamMixin, TurnView
from specifications.interfaces.session import SessionRecord

logger = logging.getLogger(__name__)


class SlackPullRequestMixin(SlackTurnStreamMixin):
    """Pull request cards: one per pull request a turn opened, updated in place."""

    # Provided by the assistant.
    async def _load_org_policy(self, org_id: str) -> SlackOrgPolicy:
        raise NotImplementedError

    def _session_link(self, org_policy: SlackOrgPolicy, session_id: str) -> str | None:
        raise NotImplementedError

    async def _post_pull_request_cards(self, view: TurnView) -> None:
        diffs = view.outcome.diffs
        for repository, number, url in view.outcome.pull_requests:
            card = PullRequestCard(
                repository=repository,
                number=number,
                url=url,
                branch=view.outcome.branch,
                files=len(diffs),
                additions=sum(d.additions for d in diffs),
                deletions=sum(d.deletions for d in diffs),
                channel_id=view.channel_id,
            )
            try:
                posted = await view.say_fn(
                    blocks=build_pull_request_card(card, view.link_url),
                    text=f"Pull request {repository}#{number}",
                    thread_ts=view.thread_ts,
                )
            except Exception as err:
                logger.warning("Could not post the pull request card of %s: %s", url, err)
                continue
            ts = str(posted.get("ts") or "") if hasattr(posted, "get") else ""
            # An ephemeral card (a private request) cannot be updated: nothing to remember.
            if ts and not view.private:
                await self._remember_card(view.session_id, view.org_id, card, ts)

    async def _remember_card(
        self, session_id: str, org_id: str, card: PullRequestCard, ts: str
    ) -> None:
        if self.session_store is None:
            return
        stored = PullRequestCard(**{**card.as_metadata(), "ts": ts}).as_metadata()

        def remember(record: SessionRecord) -> None:
            known = record.metadata if isinstance(record.metadata, dict) else {}
            cards = [c for c in known.get(PR_CARDS_KEY) or [] if isinstance(c, dict)]
            record.metadata = {**known, PR_CARDS_KEY: [*cards, stored][-MAX_PR_CARDS:]}

        try:
            await self.session_store.mutate_session(session_id, org_id, remember)
        except Exception as err:
            logger.warning("Could not remember the pull request card of %s: %s", session_id, err)

    async def update_pull_request_cards(
        self, session_id: str, org_id: str, event: dict[str, Any]
    ) -> int:
        """Bring the session's cards for the event's pull request up to date; how many changed.

        Called by the platform for each GitHub event of a session it follows (a
        ``GithubEventPayload``).
        """
        if self.session_store is None or not self._slack_api_ready:
            return 0
        state = ci_state_of(str(event.get("kind") or ""), str(event.get("outcome") or ""))
        if state is None:
            return 0
        record = await self.session_store.get_session(session_id, org_id, include_history=False)
        metadata = (
            record.metadata if record is not None and isinstance(record.metadata, dict) else {}
        )
        cards = [PullRequestCard.from_metadata(raw) for raw in metadata.get(PR_CARDS_KEY) or []]
        changed = [
            card
            for card in cards
            if card is not None
            and card.repository == event.get("repo")
            and card.number == event.get("pr_number")
            and card.ci != state
            and card.ts
        ]
        if not changed:
            return 0
        link = self._session_link(await self._load_org_policy(org_id), session_id)
        for card in changed:
            updated = with_ci(card, state)
            try:
                await self.app.client.chat_update(
                    channel=card.channel_id,
                    ts=card.ts,
                    blocks=build_pull_request_card(updated, link),
                    text=f"Pull request {card.repository}#{card.number}",
                )
            except Exception as err:
                logger.info("Pull request card %s not updated: %s", card.url, err)
        await self._store_ci_state(session_id, org_id, changed, state)
        return len(changed)

    async def _store_ci_state(
        self, session_id: str, org_id: str, changed: list[PullRequestCard], state: str
    ) -> None:
        keys = {(card.channel_id, card.ts) for card in changed}

        def update(record: SessionRecord) -> None:
            known = record.metadata if isinstance(record.metadata, dict) else {}
            cards = [
                {**raw, "ci": state}
                if isinstance(raw, dict) and (raw.get("channel_id"), raw.get("ts")) in keys
                else raw
                for raw in known.get(PR_CARDS_KEY) or []
            ]
            record.metadata = {**known, PR_CARDS_KEY: cards}

        assert self.session_store is not None
        await self.session_store.mutate_session(session_id, org_id, update)
