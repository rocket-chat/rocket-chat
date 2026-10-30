"""A member's GPG key for signing the commits the agent makes as them.

The private key is stored encrypted, like every credential, and is validated on import: it is
parsed by GnuPG itself in a throwaway keyring (never the platform's), must be a usable signing
key, must carry the commit e-mail as one of its identities, and a test signature must succeed
with the given passphrase. GitHub shows a commit as *verified* only when its signing key was
uploaded to the author's account and the commit e-mail is verified there, so the public key is
returned for the member to upload.

The key never enters a sandbox: the commits the agent makes there are unsigned, and the platform
re-creates them signed with this key when the branch is published (``git_push``, in a throwaway
keyring, see ``git_engine.host_repo``). It never reaches the model or the logs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field, replace
from typing import Any

import anyio.to_thread

from api.db.session import DatabaseSessionManager
from api.services.credentials import (
    delete_user_credential,
    get_user_credential,
    store_user_credential,
)
from specifications.interfaces.agent import GitPolicy
from specifications.interfaces.git import SigningKey

logger = logging.getLogger(__name__)

PROVIDER = "git_signing"
GPG_TIMEOUT_SECONDS = 20.0
MAX_KEY_BYTES = 64 * 1024
SIGNED_MODE = "member_gpg"
VERIFIED_FALLBACK_MODE = "github"


class SigningKeyError(ValueError):
    """The key cannot be used; the message tells the member what to fix."""


class SigningUnavailable(RuntimeError):
    """This deployment cannot validate or use GPG keys (GnuPG is not installed)."""


@dataclass(frozen=True)
class SigningKeyInfo:
    fingerprint: str
    key_id: str
    identities: tuple[str, ...]
    emails: tuple[str, ...]
    created_at: int
    expires_at: int | None
    algorithm: str
    public_key: str = field(default="", repr=False)


@dataclass(frozen=True)
class MemberSigning:
    """What a turn needs to sign as the member; the secrets are kept out of ``repr``."""

    fingerprint: str
    commit_email: str
    private_key: str = field(repr=False)
    passphrase: str = field(default="", repr=False)


def gpg_available() -> bool:
    return shutil.which("gpg") is not None


async def _gpg(
    home: str, *args: str, stdin: bytes | None = None, passphrase_file: str | None = None
) -> tuple[int, str, str]:
    """Run GnuPG against a throwaway keyring, with a timeout."""
    gpg = shutil.which("gpg")
    if gpg is None:
        raise SigningUnavailable("GnuPG is not installed on this deployment.")
    command = [gpg, "--homedir", home, "--batch", "--no-tty", "--pinentry-mode", "loopback"]
    if passphrase_file:
        command += ["--passphrase-file", passphrase_file]
    process = await asyncio.create_subprocess_exec(
        *command,
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": os.environ.get("PATH", ""), "LANG": "C"},
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(stdin), GPG_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        raise SigningKeyError("GnuPG did not answer in time.") from None
    return process.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def _parse_listing(listing: str) -> tuple[dict[str, str], list[str], str, list[str]]:
    """The primary key's colon record, its identities, its fingerprint and every secret record."""
    primary: dict[str, str] = {}
    identities: list[str] = []
    fingerprint = ""
    secrets: list[str] = []
    for line in listing.splitlines():
        fields = line.split(":")
        kind = fields[0]
        if kind == "sec":
            secrets.append(line)
            if not primary:
                primary = {
                    "validity": fields[1],
                    "algo": fields[3],
                    "key_id": fields[4],
                    "created": fields[5],
                    "expires": fields[6],
                }
        elif kind == "fpr" and primary and not fingerprint:
            fingerprint = fields[9]
        elif kind == "uid" and fields[1] not in ("r", "e"):
            identities.append(fields[9].replace("\\x3a", ":"))
    return primary, identities, fingerprint, secrets


def _emails_of(identities: list[str]) -> tuple[str, ...]:
    found = []
    for identity in identities:
        if "<" in identity and identity.rstrip().endswith(">"):
            found.append(identity.rsplit("<", 1)[1].rstrip(">").strip().lower())
    return tuple(dict.fromkeys(found))


_ALGORITHMS = {"1": "RSA", "17": "DSA", "18": "ECDH", "19": "ECDSA", "22": "EdDSA", "16": "ElGamal"}


async def inspect_private_key(armored: str, passphrase: str = "") -> SigningKeyInfo:
    """Parse and test a private key; raises ``SigningKeyError`` with what to fix."""
    if len(armored.encode()) > MAX_KEY_BYTES:
        raise SigningKeyError("That is too large to be a GPG key.")
    if "BEGIN PGP PRIVATE KEY BLOCK" not in armored:
        raise SigningKeyError(
            "Paste an armored private key (it starts with -----BEGIN PGP PRIVATE KEY BLOCK-----); "
            "export it with `gpg --armor --export-secret-keys <key id>`."
        )
    home, pass_file = await anyio.to_thread.run_sync(_private_keyring, passphrase)
    try:
        code, _, err = await _gpg(
            home, "--import", stdin=armored.encode(), passphrase_file=pass_file
        )
        if code != 0:
            raise SigningKeyError(f"GnuPG could not read that key: {_first_line(err)}")
        _, listing, _ = await _gpg(home, "--list-secret-keys", "--with-colons", "--fixed-list-mode")
        primary, identities, fingerprint, secrets = _parse_listing(listing)
        if not primary or not fingerprint:
            raise SigningKeyError("That text holds no private key (a public key cannot sign).")
        if len(secrets) > 1:
            raise SigningKeyError("Import one key at a time: that text holds several.")
        if primary["validity"] in ("r", "e"):
            raise SigningKeyError(
                "That key is " + ("revoked" if primary["validity"] == "r" else "expired") + "."
            )
        code, _, err = await _gpg(
            home,
            "--local-user",
            fingerprint,
            "--clearsign",
            stdin=b"rocket signing test\n",
            passphrase_file=pass_file,
        )
        if code != 0:
            raise SigningKeyError(
                "The key cannot sign with that passphrase "
                f"({_first_line(err)}). Check the passphrase, or that it has a signing key."
            )
        code, public, _ = await _gpg(home, "--armor", "--export", fingerprint)
        expires = int(primary["expires"]) if primary["expires"] else None
        return SigningKeyInfo(
            fingerprint=fingerprint,
            key_id=primary["key_id"],
            identities=tuple(identities),
            emails=_emails_of(identities),
            created_at=int(primary["created"] or 0),
            expires_at=expires,
            algorithm=_ALGORITHMS.get(primary["algo"], primary["algo"]),
            public_key=public if code == 0 else "",
        )
    finally:
        await _discard_keyring(home)


async def _discard_keyring(home: str) -> None:
    """Stop the agent GnuPG started for the throwaway home, then delete the home."""
    gpgconf = shutil.which("gpgconf")
    if gpgconf:
        try:
            process = await asyncio.create_subprocess_exec(
                gpgconf,
                "--homedir",
                home,
                "--kill",
                "all",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(process.wait(), GPG_TIMEOUT_SECONDS)
        except (TimeoutError, OSError):
            logger.debug("Could not stop the GnuPG agent of %s", home)
    await anyio.to_thread.run_sync(lambda: shutil.rmtree(home, ignore_errors=True))


def _private_keyring(passphrase: str) -> tuple[str, str]:
    """A throwaway GnuPG home (mode 700) holding the passphrase file (mode 600).

    Under /tmp when there is one: GnuPG keeps unix sockets in its home, and a long temporary
    directory (macOS) exceeds the socket path limit.
    """
    home = tempfile.mkdtemp(prefix="rocket-gpg-", dir="/tmp" if os.path.isdir("/tmp") else None)
    os.chmod(home, 0o700)
    pass_file = os.path.join(home, "pass")
    descriptor = os.open(pass_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(passphrase)
    return home, pass_file


def _first_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "no details"


def choose_commit_email(info: SigningKeyInfo, requested: str | None) -> str:
    """The e-mail commits carry: it must be one of the key's identities, or GitHub cannot match them."""
    if requested:
        email = requested.strip().lower()
        if email not in info.emails:
            known = ", ".join(info.emails) or "none"
            raise SigningKeyError(
                f"{requested} is not an identity of this key (it has: {known}). GitHub matches a "
                "signed commit to its author by e-mail, so they must agree."
            )
        return email
    if len(info.emails) == 1:
        return info.emails[0]
    if not info.emails:
        raise SigningKeyError("The key has no e-mail identity; add one with `gpg --edit-key`.")
    raise SigningKeyError(
        "The key has several e-mail identities; choose which one commits use: "
        + ", ".join(info.emails)
    )


async def save_signing_key(
    db_mgr: DatabaseSessionManager | None,
    org_id: str,
    user_id: str,
    armored: str,
    passphrase: str,
    commit_email: str | None,
) -> tuple[SigningKeyInfo, str]:
    info = await inspect_private_key(armored, passphrase)
    email = choose_commit_email(info, commit_email)
    metadata: dict[str, Any] = {
        "fingerprint": info.fingerprint,
        "key_id": info.key_id,
        "identities": list(info.identities),
        "emails": list(info.emails),
        "created_at": info.created_at,
        "expires_at": info.expires_at,
        "algorithm": info.algorithm,
        "commit_email": email,
        "has_passphrase": bool(passphrase),
        "public_key": info.public_key,
    }
    secret = json.dumps({"private_key": armored.strip() + "\n", "passphrase": passphrase})
    await store_user_credential(db_mgr, user_id, org_id, PROVIDER, secret, metadata)
    return info, email


async def stored_signing_metadata(
    db_mgr: DatabaseSessionManager | None, org_id: str, user_id: str
) -> dict[str, Any] | None:
    credential = await get_user_credential(db_mgr, user_id, PROVIDER, org_id=org_id)
    return dict(credential.metadata) if credential and credential.secret else None


async def delete_signing_key(
    db_mgr: DatabaseSessionManager | None, org_id: str, user_id: str
) -> None:
    # The organization matters: row-level security hides every other tenant's rows, so a
    # delete under the default organization would silently remove nothing.
    await delete_user_credential(db_mgr, user_id, PROVIDER, org_id=org_id)


async def member_signing_for_turn(
    db_mgr: DatabaseSessionManager | None,
    org_id: str,
    user_id: str,
    member_prefs: dict[str, Any],
) -> MemberSigning | None:
    """The key to sign with, when the member stored one (``github.signing: member_gpg``)."""
    credential = await get_user_credential(db_mgr, user_id, PROVIDER, org_id=org_id)
    if not (credential and credential.secret):
        return None
    try:
        material = json.loads(credential.secret)
    except ValueError:
        logger.warning("The stored signing key of %s is unreadable", user_id)
        return None
    metadata = credential.metadata
    expires = metadata.get("expires_at")
    if expires and int(expires) < int(time.time()):
        return None
    return MemberSigning(
        fingerprint=str(metadata["fingerprint"]),
        commit_email=str(metadata["commit_email"]),
        private_key=str(material["private_key"]),
        passphrase=str(material.get("passphrase") or ""),
    )


def effective_signing_policy(policy: GitPolicy, signing: MemberSigning | None) -> GitPolicy:
    """What ``github.signing`` means for this member.

    ``member_gpg``: a member who stored a key commits as themselves, signed with it; everyone
    else falls back to GitHub-signed commits made through the API, so they are verified either
    way. Other modes apply as they are.
    """
    if policy.signing != SIGNED_MODE:
        return policy
    if signing is None:
        return replace(policy, signing=VERIFIED_FALLBACK_MODE)
    return replace(
        policy,
        user_email=signing.commit_email,
        signing_key_id=signing.fingerprint,
        signing_key=SigningKey(signing.fingerprint, signing.private_key, signing.passphrase),
    )
