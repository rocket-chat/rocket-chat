"""A throwaway repository on the platform where a published branch is imported, signed and pushed.

The sandbox hands over a bundle of the branch's new commits. They are imported here, on top of
the one commit they build on (fetched shallow and without file contents, so this costs little
however large the repository is), optionally re-created signed with the member's key, and
pushed. The write credential and the signing key are used only here: git runs with no user or
system configuration and no hooks, the credential travels as an environment header for the
one host it is meant for, and the directory (keyring included) is removed afterwards.
"""

from __future__ import annotations

import asyncio
import base64
import os
import shutil
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from specifications.interfaces.git import SigningKey

COMMAND_TIMEOUT_SECONDS = 300.0
PUBLISH_REF = "refs/rocket/publish"
_IDENTITY_FORMAT = "%an%x00%ae%x00%ad%x00%cn%x00%ce%x00%cd%x00%P"
# A memory-backed directory keeps a signing key off disk where the platform's OS offers one. The
# keyring path is kept short either way: gpg-agent's socket lives in it, and Unix socket paths
# are limited to about 100 characters.
_KEYRING_PARENTS = ("/dev/shm", "/tmp")


class HostGitError(RuntimeError):
    """A git command on the platform failed; the message says which and why."""


@dataclass(frozen=True)
class _CommitIdentity:
    author_name: str
    author_email: str
    author_date: str
    committer_name: str
    committer_email: str
    committer_date: str
    parents: tuple[str, ...]


class HostRepository:
    """``async with HostRepository(remote_url, token) as repo:`` - removed on exit."""

    def __init__(self, remote_url: str, token: str | None) -> None:
        self.remote_url = remote_url
        self._token = token
        self._root = Path()
        self.path = Path()

    async def __aenter__(self) -> HostRepository:
        self._root = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="rocket-publish-"))
        self.path = self._root / "repo.git"
        await self._git("init", "--quiet", "--bare", str(self.path), cwd=self._root)
        # A declared promisor remote: an object a push unexpectedly needs is fetched from it
        # instead of failing the push for a file content this repository never downloaded.
        for key, value in (
            ("core.repositoryformatversion", "1"),
            ("extensions.partialClone", "origin"),
            ("remote.origin.url", self.remote_url),
            ("remote.origin.promisor", "true"),
            ("remote.origin.partialclonefilter", "blob:none"),
        ):
            await self._git("config", key, value)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await asyncio.to_thread(shutil.rmtree, self._root, ignore_errors=True)

    def _environment(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self._root),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
        entries = [("core.hooksPath", os.devnull)]
        if self._token:
            basic = base64.b64encode(f"x-access-token:{self._token}".encode()).decode()
            entries.append((f"http.{self.remote_url}.extraHeader", f"Authorization: Basic {basic}"))
        environment["GIT_CONFIG_COUNT"] = str(len(entries))
        for index, (key, value) in enumerate(entries):
            environment[f"GIT_CONFIG_KEY_{index}"] = key
            environment[f"GIT_CONFIG_VALUE_{index}"] = value
        environment.update(extra or {})
        return environment

    async def _git(
        self,
        *args: str,
        cwd: Path | None = None,
        stdin: bytes | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> bytes:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd or self.path),
            env=self._environment(extra_env),
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(stdin), COMMAND_TIMEOUT_SECONDS
            )
        except TimeoutError as err:
            process.kill()
            await process.wait()
            raise HostGitError(f"`git {args[0]}` timed out") from err
        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip() or "no output"
            if self._token:
                detail = detail.replace(self._token, "***")
            raise HostGitError(f"`git {args[0]}` failed: {detail[-800:]}")
        return stdout

    async def text(self, *args: str) -> str:
        return (await self._git(*args)).decode(errors="replace").strip()

    async def fetch_base(self, base_sha: str) -> None:
        """The one commit the new commits build on: its trees, no history, no file contents."""
        await self._git("fetch", "--quiet", "--depth=1", "--filter=blob:none", "origin", base_sha)

    async def import_bundle(self, bundle: bytes) -> str:
        """Fetch the bundle's commits into ``PUBLISH_REF``; returns its tip."""
        bundle_path = self._root / "publish.bundle"
        await asyncio.to_thread(bundle_path.write_bytes, bundle)
        try:
            await self._git("bundle", "verify", "--quiet", str(bundle_path))
            await self._git("fetch", "--quiet", str(bundle_path), f"HEAD:{PUBLISH_REF}")
        finally:
            await asyncio.to_thread(bundle_path.unlink, missing_ok=True)
        return await self.text("rev-parse", PUBLISH_REF)

    async def blob(self, sha: str) -> bytes:
        return await self._git("cat-file", "blob", sha)

    async def resign(self, commits: list[str], key: SigningKey) -> str:
        """Re-create ``commits`` (oldest first) signed with ``key``; returns the new tip.

        Tree, parents (mapped onto the re-created ones), author, committer, dates and message are
        kept, so only the signature and the hashes change.
        """
        async with _Keyring(self._root, key) as keyring:
            mapped: dict[str, str] = {}
            for sha in commits:
                identity = await self._identity(sha)
                message = await self._git("log", "-1", "--format=%B", sha)
                tree = await self.text("rev-parse", f"{sha}^{{tree}}")
                parent_args = [
                    arg for parent in identity.parents for arg in ("-p", mapped.get(parent, parent))
                ]
                created = await self._git(
                    "-c",
                    f"gpg.program={keyring.program}",
                    "-c",
                    f"user.signingkey={key.fingerprint}",
                    "commit-tree",
                    f"-S{key.fingerprint}",
                    tree,
                    *parent_args,
                    stdin=message,
                    extra_env={
                        "GIT_AUTHOR_NAME": identity.author_name,
                        "GIT_AUTHOR_EMAIL": identity.author_email,
                        "GIT_AUTHOR_DATE": identity.author_date,
                        "GIT_COMMITTER_NAME": identity.committer_name,
                        "GIT_COMMITTER_EMAIL": identity.committer_email,
                        "GIT_COMMITTER_DATE": identity.committer_date,
                    },
                )
                mapped[sha] = created.decode().strip()
        tip = mapped[commits[-1]]
        await self._git("update-ref", PUBLISH_REF, tip)
        return tip

    async def _identity(self, sha: str) -> _CommitIdentity:
        fields = await self.text("log", "-1", f"--format={_IDENTITY_FORMAT}", "--date=raw", sha)
        parts = fields.split("\0")
        return _CommitIdentity(
            author_name=parts[0],
            author_email=parts[1],
            author_date=parts[2],
            committer_name=parts[3],
            committer_email=parts[4],
            committer_date=parts[5],
            parents=tuple(parts[6].split()),
        )

    async def push(self, branch: str, *, lease: str | None = None) -> None:
        """Push ``PUBLISH_REF`` to ``branch``; ``lease`` replaces only that remote head."""
        refspec = f"{PUBLISH_REF}:refs/heads/{branch}"
        args = ["push", "--quiet", "--no-verify"]
        if lease is not None:
            args.append(f"--force-with-lease=refs/heads/{branch}:{lease}")
        await self._git(*args, "origin", refspec)


class _Keyring:
    """A temporary GnuPG home holding one key, and a ``gpg`` wrapper that unlocks it."""

    def __init__(self, root: Path, key: SigningKey) -> None:
        self._root = root
        self._key = key
        self._home = Path()
        self.program = ""

    def _prepare(self) -> None:
        parent = next((p for p in _KEYRING_PARENTS if os.path.isdir(p)), str(self._root))
        self._home = Path(tempfile.mkdtemp(prefix="rg-", dir=parent))
        self._home.chmod(stat.S_IRWXU)
        passphrase = self._home / "pass"
        passphrase.write_text(self._key.passphrase)
        passphrase.chmod(stat.S_IRUSR | stat.S_IWUSR)
        wrapper = self._home / "gpg-sign"
        wrapper.write_text(
            "#!/bin/sh\n"
            f'exec gpg --homedir "{self._home}" --batch --no-tty --pinentry-mode loopback '
            f'--passphrase-file "{passphrase}" "$@"\n'
        )
        wrapper.chmod(stat.S_IRWXU)
        self.program = str(wrapper)

    async def __aenter__(self) -> _Keyring:
        await asyncio.to_thread(self._prepare)
        process = await asyncio.create_subprocess_exec(
            self.program,
            "--import",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(
            process.communicate(self._key.private_key.encode()), COMMAND_TIMEOUT_SECONDS
        )
        if process.returncode != 0:
            await asyncio.to_thread(shutil.rmtree, self._home, ignore_errors=True)
            raise HostGitError(f"The signing key could not be loaded: {stderr.decode()[-300:]}")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # The agent gpg started for this keyring would otherwise outlive it.
        stopper = await asyncio.create_subprocess_exec(
            "gpgconf",
            "--homedir",
            str(self._home),
            "--kill",
            "gpg-agent",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(stopper.wait(), COMMAND_TIMEOUT_SECONDS)
        await asyncio.to_thread(shutil.rmtree, self._home, ignore_errors=True)
