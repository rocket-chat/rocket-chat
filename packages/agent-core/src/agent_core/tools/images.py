"""Letting the agent look at an image in its workspace (``view_image``).

Looking is model-dependent, not sandbox-dependent: any sandbox can hold an image, and whether
the agent can *see* it depends on the model. The bytes are queued on the turn's execution
context and the orchestrator attaches them to the model requests of this turn only; the
gateway formats them as vision blocks, or explains plainly when the model cannot take images.
"""

from __future__ import annotations

import hashlib
import posixpath

from sandbox_driver.blobs import BlobTooLargeError, read_bytes

from specifications.interfaces.llm import AttachmentRef
from specifications.interfaces.sandbox import SandboxDriverProtocol

WORKSPACE = "/workspace"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES_PER_TURN = 4

_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_mime(data: bytes) -> str | None:
    """The image type the bytes really are (the file name is not trusted)."""
    for signature, mime in _SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def confine(path: str) -> str | None:
    raw = path.strip()
    candidate = posixpath.normpath(raw if raw.startswith("/") else posixpath.join(WORKSPACE, raw))
    inside = candidate.startswith(WORKSPACE + "/")
    return candidate if inside else None


async def load_image(
    driver: SandboxDriverProtocol,
    session_id: str,
    path: str,
    queue: list[AttachmentRef] | None,
) -> str:
    """Read ``path`` and queue it for the model; returns what the agent should be told."""
    target = confine(path)
    if target is None:
        return "Error: the image must be a file inside /workspace."
    try:
        data = await read_bytes(driver, session_id, target, max_bytes=MAX_IMAGE_BYTES)
    except FileNotFoundError:
        return f"Error: no file at {target}."
    except BlobTooLargeError:
        return f"Error: {target} is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB."
    mime = sniff_mime(data)
    if mime is None:
        return f"Error: {target} is not a PNG, JPEG, GIF or WebP image."
    if queue is None:
        return "Error: images cannot be shown outside a running turn."
    reference = AttachmentRef(
        name=posixpath.basename(target),
        mime=mime,
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        workspace_path=target,
        is_image=True,
        data=data,
    )
    queue[:] = [a for a in queue if a.sha256 != reference.sha256]
    queue.append(reference)
    del queue[:-MAX_IMAGES_PER_TURN]
    return (
        f"Loaded {reference.name} ({mime}, {len(data)} bytes). It is attached to the "
        "conversation for the rest of this turn: describe what you see and continue."
    )
