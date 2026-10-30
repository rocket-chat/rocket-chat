"""The sandbox images a release ships, pinned to that release.

``:latest`` let a running deployment pick up a newer devbox than the platform it was tested
with. ``scripts/release.py`` rewrites ``SANDBOX_IMAGE_TAG`` with every version, and operators
point any of these at a mirror with the matching variable.
"""

from __future__ import annotations

import os

SANDBOX_IMAGE_TAG = "0.7.2"
SANDBOX_REGISTRY = "ghcr.io/rocket-chat"


def _image(env_name: str, repository: str) -> str:
    return os.getenv(env_name) or f"{SANDBOX_REGISTRY}/{repository}:{SANDBOX_IMAGE_TAG}"


# The devbox: git, Python with uv, Node, build tools, ripgrep, fd; runs as an unprivileged user.
DEFAULT_SANDBOX_IMAGE = _image("ROCKET_DEFAULT_SANDBOX_IMAGE", "sandbox")
# The devbox plus Go, Rust (rustup) and Java 21, for polyglot repositories and heavier builds.
POLYGLOT_SANDBOX_IMAGE = _image("ROCKET_POLYGLOT_SANDBOX_IMAGE", "sandbox-polyglot")
# The devbox plus Chromium and Playwright, for the ``browser`` capability.
BROWSER_SANDBOX_IMAGE = _image("ROCKET_BROWSER_SANDBOX_IMAGE", "sandbox-browser")
