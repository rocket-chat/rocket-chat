"""Unit tests for the redaction pipeline.

Covers both directions the spec calls out: positive cases remove the secret and record
that a redaction happened, and negative cases leave legitimate content byte-identical so
the pipeline cannot quietly corrupt ordinary tool output.
"""

from __future__ import annotations

from api.services.redaction import (
    REDACTION_MARKER,
    RedactionPolicy,
    Redactor,
    fingerprint,
)


def test_secret_patterns_are_redacted_and_counted() -> None:
    redactor = Redactor()
    text = (
        "Set OPENAI_KEY=sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 and "
        "token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789."
    )

    result = redactor.redact(text)

    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in result.text
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in result.text
    assert REDACTION_MARKER.format(label="openai_key") in result.text
    assert REDACTION_MARKER.format(label="github_token") in result.text
    assert result.count == 2
    assert result.applied is True


def test_private_key_and_assignment_are_redacted() -> None:
    redactor = Redactor()
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\npassword: hunter2swordfish"

    result = redactor.redact(text)

    assert "PRIVATE KEY-----" not in result.text
    assert "hunter2swordfish" not in result.text
    assert result.count >= 2


def test_ordinary_output_is_left_byte_identical() -> None:
    redactor = Redactor()
    text = "3 passed, 0 failed in 0.42s\nOn branch main\nnothing to commit"

    result = redactor.redact(text)

    assert result.text == text
    assert result.count == 0
    assert result.applied is False
    assert result.fingerprints == ()


def test_disabled_policy_is_a_no_op() -> None:
    redactor = Redactor(RedactionPolicy(enabled=False))

    result = redactor.redact("secret: sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")

    assert result.text == "secret: sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    assert result.count == 0


def test_pii_is_preserved_by_default_and_scrubbed_when_enabled() -> None:
    text = "Contact ada@example.com or call +1 415 555 0100."
    assert Redactor().redact(text).text == text

    enabled = Redactor(RedactionPolicy(pii_enabled=True))
    result = enabled.redact(text)

    assert "ada@example.com" not in result.text
    assert "415" not in result.text
    assert result.count >= 2


def test_org_patterns_apply_and_invalid_patterns_are_ignored() -> None:
    policy = RedactionPolicy(extra_patterns=(r"INTERNAL-\d{4}", r"(unclosed"))
    redactor = Redactor(policy)

    result = redactor.redact("ticket INTERNAL-1234 is open")

    assert "INTERNAL-1234" not in result.text
    assert REDACTION_MARKER.format(label="org") in result.text
    assert result.count == 1


def test_fingerprint_correlates_without_revealing_the_value() -> None:
    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    redactor = Redactor()

    result = redactor.redact(f"key={secret}")

    assert result.fingerprints == (fingerprint(secret),)
    assert secret not in result.fingerprints[0]
    assert result.fingerprints[0].endswith("2345")


def test_memory_hook_adapter_returns_text_and_count() -> None:
    redactor = Redactor()

    scrubbed, count = redactor.redact_text("token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")

    assert count == 1
    assert "ghp_" not in scrubbed


def test_policy_from_settings_defaults_are_safe() -> None:
    default = RedactionPolicy.from_settings(None)
    assert default.enabled is True
    assert default.pii_enabled is False
    assert default.extra_patterns == ()

    configured = RedactionPolicy.from_settings(
        {
            "redaction_enabled": False,
            "pii_redaction_enabled": True,
            "redaction_patterns": "TENANT-[0-9]+, BUILD-[0-9]+",
        }
    )
    assert configured.enabled is False
    assert configured.pii_enabled is True
    assert configured.extra_patterns == ("TENANT-[0-9]+", "BUILD-[0-9]+")
