"""Who may sign in through SSO, and which organization they land in.

Organization assignment is explicit configuration, never inferred from a public mail
domain: ``ROCKET_SSO_ORG_MODE=single`` (default) places every user in one organization
(``ROCKET_DEFAULT_ORG_ID``), ``domain`` derives it from a *corporate* e-mail domain.
"""

from __future__ import annotations

import os
import re

PUBLIC_MAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "yahoo.com",
        "icloud.com",
        "me.com",
        "proton.me",
        "protonmail.com",
        "aol.com",
        "gmx.com",
        "gmx.net",
        "yandex.com",
        "mail.com",
        "qq.com",
        "fastmail.com",
        "hey.com",
    }
)


def default_org_id() -> str:
    return os.getenv("ROCKET_DEFAULT_ORG_ID", "default_org")


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower() if "@" in email else ""


def _parse_domains(raw: str | list[str] | None) -> set[str]:
    if not raw:
        return set()
    items = raw.split(",") if isinstance(raw, str) else raw
    return {str(item).strip().lower() for item in items if str(item).strip()}


def map_org(email: str, claimed_org: str | None = None) -> str:
    """Organization for a freshly authenticated user."""
    if claimed_org:
        # Asserted by the identity provider itself, so it is as trustworthy as the login.
        return claimed_org
    if os.getenv("ROCKET_SSO_ORG_MODE", "single").strip().lower() == "domain":
        domain = email_domain(email)
        if domain and domain not in PUBLIC_MAIL_DOMAINS:
            return re.sub(r"[^a-z0-9]+", "_", domain).strip("_")
    return default_org_id()


def domain_permitted(email: str, org_general_settings: dict[str, object] | None = None) -> bool:
    """Whether ``email`` may sign in, per the deployment and the organization's policy.

    The deployment-level ``SSO_ALLOWED_DOMAINS`` / ``GOOGLE_ALLOWED_DOMAIN`` always
    restrict; the organization's ``general.sso_allowed_domains`` restricts when
    ``general.sso_enforce_domain_match`` is on.
    """
    domain = email_domain(email)
    settings = org_general_settings or {}

    deployment = _parse_domains(os.getenv("SSO_ALLOWED_DOMAINS")) | _parse_domains(
        os.getenv("GOOGLE_ALLOWED_DOMAIN")
    )
    if deployment and domain not in deployment:
        return False

    org_domains = _parse_domains(settings.get("sso_allowed_domains"))  # type: ignore[arg-type]
    return not (
        settings.get("sso_enforce_domain_match") and org_domains and domain not in org_domains
    )
