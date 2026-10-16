"""A member's GPG key: validated by GnuPG itself, stored encrypted, applied by the policy."""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest
from api.services.credentials import get_user_credential
from api.services.git_signing import (
    MemberSigning,
    SigningKeyError,
    choose_commit_email,
    effective_signing_policy,
    gpg_available,
    inspect_private_key,
    member_signing_for_turn,
    save_signing_key,
)

from specifications.interfaces.agent import GitPolicy
from tests.support.gpg_keys import make_key

pytestmark = pytest.mark.skipif(not gpg_available(), reason="needs GnuPG")


async def test_a_key_is_parsed_tested_and_its_public_half_returned(tmp_path: pathlib.Path) -> None:
    private, public = make_key(tmp_path / "k", "Octo Cat <Octo@Example.com>", passphrase="s3cret")

    info = await inspect_private_key(private, "s3cret")

    assert len(info.fingerprint) == 40 and info.algorithm == "EdDSA"
    assert info.emails == ("octo@example.com",) and info.expires_at is None
    assert "BEGIN PGP PUBLIC KEY BLOCK" in info.public_key
    assert "PRIVATE" not in info.public_key
    # The same key, not the same armored text: two exports of one key can differ in bytes
    # (and so in their checksum line), depending on the GnuPG version and keyring.
    assert _fingerprints(info.public_key) == _fingerprints(public) == [info.fingerprint]


def _fingerprints(armored: str) -> list[str]:
    shown = subprocess.run(
        ["gpg", "--batch", "--with-colons", "--show-keys"],
        input=armored,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout
    rows = [line.split(":") for line in shown.splitlines()]
    primary = [i for i, row in enumerate(rows) if row[0] == "pub"]
    return [rows[i + 1][9] for i in primary if i + 1 < len(rows) and rows[i + 1][0] == "fpr"]


async def test_a_wrong_passphrase_is_explained(tmp_path: pathlib.Path) -> None:
    private, _ = make_key(tmp_path / "k", "A <a@x.test>", passphrase="right")

    with pytest.raises(SigningKeyError, match="cannot sign with that passphrase"):
        await inspect_private_key(private, "wrong")


async def test_a_public_key_or_garbage_is_not_accepted(tmp_path: pathlib.Path) -> None:
    _, public = make_key(tmp_path / "k", "A <a@x.test>")

    with pytest.raises(SigningKeyError, match="armored private key"):
        await inspect_private_key(public)
    with pytest.raises(SigningKeyError, match="armored private key"):
        await inspect_private_key("hello")
    with pytest.raises(SigningKeyError, match="could not read"):
        await inspect_private_key(
            "-----BEGIN PGP PRIVATE KEY BLOCK-----\n\nnot really a key\n-----END PGP PRIVATE KEY BLOCK-----"
        )


async def test_an_expired_key_is_refused(tmp_path: pathlib.Path) -> None:
    private, _ = make_key(
        tmp_path / "k", "A <a@x.test>", expires="1d", faked_time="20200101T000000"
    )

    with pytest.raises(SigningKeyError, match="expired"):
        await inspect_private_key(private)


async def test_the_commit_email_must_be_an_identity_of_the_key(tmp_path: pathlib.Path) -> None:
    private, _ = make_key(tmp_path / "k", "A <a@x.test>", "A Work <a@work.test>")
    info = await inspect_private_key(private)

    assert choose_commit_email(info, "A@Work.test") == "a@work.test"
    with pytest.raises(SigningKeyError, match="not an identity of this key"):
        choose_commit_email(info, "other@x.test")
    with pytest.raises(SigningKeyError, match="several e-mail identities"):
        choose_commit_email(info, None)


async def test_a_stored_key_is_encrypted_and_only_used_when_the_member_opts_in(
    tmp_path: pathlib.Path,
) -> None:
    private, _ = make_key(tmp_path / "k", "Octo <octo@example.com>", passphrase="pw")
    user, org = "signing_user", "org-sign"

    info, email = await save_signing_key(None, org, user, private, "pw", None)

    assert email == "octo@example.com"
    credential = await get_user_credential(None, user, "git_signing", org_id=org)
    assert credential is not None and json.loads(credential.secret)["passphrase"] == "pw"
    assert credential.metadata["fingerprint"] == info.fingerprint
    assert "private_key" not in credential.metadata

    assert await member_signing_for_turn(None, org, user, {}) is None
    chosen = await member_signing_for_turn(None, org, user, {"commit_as_me": True})
    assert chosen is not None and chosen.fingerprint == info.fingerprint
    assert chosen.commit_email == "octo@example.com"
    assert "BEGIN PGP" not in repr(chosen) and "pw" not in repr(chosen)


def test_gpg_mode_means_signing_as_the_member_or_the_verified_fallback() -> None:
    policy = GitPolicy(signing_mode="gpg_key", authorship="co_authored")
    signing = MemberSigning("ABCD" * 10, "octo@example.com", "key")

    mine = effective_signing_policy(policy, signing)
    assert (mine.signing_mode, mine.authorship) == ("gpg_key", "user_only")
    assert mine.user_email == "octo@example.com" and mine.signing_key_id == "ABCD" * 10

    fallback = effective_signing_policy(policy, None)
    assert (fallback.signing_mode, fallback.authorship) == ("github_app", "co_authored")

    other = GitPolicy(signing_mode="github_app")
    assert effective_signing_policy(other, signing) is other, "other modes are left alone"
