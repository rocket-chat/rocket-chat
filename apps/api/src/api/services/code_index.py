"""Session-scoped semantic code index backing the ``code_search`` tool.

The index is built lazily from the sandbox workspace, chunked on line
boundaries, and embedded with the platform embedding pipeline (LiteLLM, with a
deterministic offline fallback). Vectors are held in memory per session; the
pgvector persistence path for cross-restart reuse is a deliberate follow-up so
this can ship without a schema migration.
"""

from __future__ import annotations

import logging
import math
import time
from array import array
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field

from api.services.knowledge import compute_embedding
from specifications.interfaces.sandbox import SandboxDriverProtocol

logger = logging.getLogger(__name__)

EXCLUDED_DIR_FRAGMENTS = (
    "/.git/",
    "/node_modules/",
    "/dist/",
    "/build/",
    "/target/",
    "/__pycache__/",
    "/.venv/",
    "/vendor/",
)

SOURCE_EXTENSIONS = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".rb",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".md",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".sql",
)

DEFAULT_MAX_FILES = 300
DEFAULT_MAX_CHUNKS = 1500
CHUNK_LINES = 60
CHUNK_OVERLAP_LINES = 10
MAX_FILE_BYTES = 200_000

# Embeddings are stored as float32 rather than Python floats: a list of 1536 Python
# floats costs ~60 MB per full session, whereas ``array("f")`` costs ~9 MB. The index
# is a *cache* of derived data, so precision beyond float32 cosine ranking is not worth
# 6x the resident memory.
EMBEDDING_TYPECODE = "f"
EMBEDDING_BYTES = 4

# Per-chunk accounting overhead (dataclass + list slot + dict entry), so the byte
# budget tracks real resident cost rather than only payload bytes.
CHUNK_OVERHEAD_BYTES = 96

# Default cache budget for the whole process. A full session is ~9 MB, so 16 sessions
# (~144 MB) is a deliberate ceiling far below a 1Gi container limit, leaving room for
# the rest of the control plane.
DEFAULT_CACHE_MAX_SESSIONS = 16
DEFAULT_CACHE_MAX_BYTES = 192 * 1024 * 1024
DEFAULT_CACHE_TTL_SECONDS = 900.0


def to_embedding_vector(values: list[float]) -> array[float]:
    """Pack an embedding into a compact float32 array."""
    return array(EMBEDDING_TYPECODE, values)


@dataclass
class CodeChunk:
    """A retrievable unit of source code."""

    path: str
    start_line: int
    text: str


@dataclass
class IndexedChunk:
    """A code chunk paired with its compact embedding vector."""

    chunk: CodeChunk
    embedding: array[float]


@dataclass
class CodeIndex:
    """A searchable snapshot of the workspace, with its own resident byte count."""

    entries: list[IndexedChunk] = field(default_factory=list)
    byte_size: int = 0

    @property
    def size(self) -> int:
        """Number of indexed chunks."""
        return len(self.entries)

    def add(self, path: str, start_line: int, text: str, vector: list[float]) -> None:
        """Append a chunk, packing the vector and updating the byte accounting."""
        embedding = to_embedding_vector(vector)
        self.entries.append(
            IndexedChunk(
                chunk=CodeChunk(path=path, start_line=start_line, text=text), embedding=embedding
            )
        )
        self.byte_size += len(embedding) * EMBEDDING_BYTES + len(text.encode("utf-8"))
        self.byte_size += len(path.encode("utf-8")) + CHUNK_OVERHEAD_BYTES

    def search(
        self, query_vector: Sequence[float], limit: int = 8
    ) -> list[tuple[float, CodeChunk]]:
        """Return the ``limit`` most similar chunks by cosine similarity."""
        scored = [(_cosine(query_vector, e.embedding), e.chunk) for e in self.entries]
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[: max(1, limit)]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity, truncating to the shorter vector on length mismatch."""
    length = min(len(a), len(b))
    if length == 0:
        return 0.0
    dot = sum(a[i] * b[i] for i in range(length))
    norm_a = math.sqrt(sum(a[i] * a[i] for i in range(length)))
    norm_b = math.sqrt(sum(b[i] * b[i] for i in range(length)))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def chunk_source(
    text: str,
    chunk_lines: int = CHUNK_LINES,
    overlap_lines: int = CHUNK_OVERLAP_LINES,
) -> list[tuple[int, str]]:
    """Split source into overlapping (start_line, text) chunks on line boundaries."""
    lines = text.splitlines()
    if not lines:
        return []
    if len(lines) <= chunk_lines:
        return [(1, text.strip())]

    step = max(1, chunk_lines - overlap_lines)
    chunks: list[tuple[int, str]] = []
    for start in range(0, len(lines), step):
        window = lines[start : start + chunk_lines]
        if not window:
            break
        body = "\n".join(window).strip()
        if body:
            chunks.append((start + 1, body))
        if start + chunk_lines >= len(lines):
            break
    return chunks


def _is_indexable(path: str) -> bool:
    """Whether a workspace path should be indexed as source."""
    lowered = f"/{path.lstrip('/')}"
    if any(fragment in lowered for fragment in EXCLUDED_DIR_FRAGMENTS):
        return False
    return lowered.endswith(SOURCE_EXTENSIONS)


async def list_source_files(
    driver: SandboxDriverProtocol, session_id: str, limit: int = DEFAULT_MAX_FILES
) -> list[str]:
    """List candidate source files in the workspace, excluding vendor directories."""
    excludes = " ".join(f"-path '*{frag}*' -prune -o" for frag in EXCLUDED_DIR_FRAGMENTS)
    command = (
        f"find /workspace {excludes} -type f -size -{MAX_FILE_BYTES}c -print 2>/dev/null "
        f"| head -n {limit}"
    )
    result = await driver.exec_command(session_id=session_id, command=command, timeout_seconds=60)
    paths = [
        line.strip().removeprefix("/workspace/")
        for line in (result.stdout or "").splitlines()
        if line.strip()
    ]
    return [path for path in paths if _is_indexable(path)]


def _budget_exhausted(index: CodeIndex, max_chunks: int, max_bytes: int) -> bool:
    """Whether either index budget has been reached."""
    if index.size >= max_chunks:
        logger.debug("Code index reached its chunk budget (%d); stopping.", max_chunks)
        return True
    if index.byte_size >= max_bytes:
        logger.info(
            "Code index reached its byte budget (%d bytes); stopping at %d chunks.",
            max_bytes,
            index.size,
        )
        return True
    return False


async def build_code_index(
    driver: SandboxDriverProtocol,
    session_id: str,
    max_files: int = DEFAULT_MAX_FILES,
    max_chunks: int = DEFAULT_MAX_CHUNKS,
    max_bytes: int = DEFAULT_CACHE_MAX_BYTES,
) -> CodeIndex:
    """Build a semantic index over the workspace source files.

    Stops as soon as the chunk or byte budget is exhausted, so a pathological
    workspace can never make the process allocate an unbounded index.
    """
    index = CodeIndex()
    for path in await list_source_files(driver, session_id, limit=max_files):
        if _budget_exhausted(index, max_chunks, max_bytes):
            break
        try:
            content = await driver.read_file(session_id=session_id, path=path)
        except Exception as err:
            logger.debug("Skipping unreadable file %s: %s", path, err)
            continue
        for start_line, body in chunk_source(content):
            if _budget_exhausted(index, max_chunks, max_bytes):
                break
            index.add(path, start_line, body, await compute_embedding(body))
    return index


async def search_code_index(
    index: CodeIndex, query: str, limit: int = 8
) -> list[tuple[float, CodeChunk]]:
    """Embed a query and retrieve the closest code chunks from the index."""
    if index.size == 0:
        return []
    query_vector = await compute_embedding(query)
    return index.search(query_vector, limit=limit)


def format_results(results: list[tuple[float, CodeChunk]]) -> str:
    """Render search results as a compact, model-friendly listing."""
    if not results:
        return ""
    lines = ["Semantic code search results:"]
    for score, chunk in results:
        stripped = chunk.text.strip()
        preview = stripped.splitlines()[0][:120] if stripped else ""
        lines.append(f"- {chunk.path}:{chunk.start_line} (score {score:.3f}) — {preview}")
    return "\n".join(lines)


class CodeIndexCache:
    """A bounded, TTL'd LRU cache of per-session code indexes.

    The index is derived data (rebuildable from the workspace at any time), so this
    class is the only thing that retains session state. It is bounded by *session
    count* and *total bytes* simultaneously: vectors dominate the footprint, and an
    unbounded process-resident cache is the most likely cause of an OOMKill in a
    long-lived pod serving many sessions.

    An index larger than the entire budget is refused rather than stored, and the
    refusal is counted so the condition is observable instead of silent.
    """

    def __init__(
        self,
        max_sessions: int = DEFAULT_CACHE_MAX_SESSIONS,
        max_bytes: int = DEFAULT_CACHE_MAX_BYTES,
        ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        name: str = "code-index",
    ) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.max_sessions = max_sessions
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self.name = name
        self._entries: OrderedDict[str, tuple[float, CodeIndex]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._refusals = 0

    @property
    def total_bytes(self) -> int:
        """Resident bytes across all cached indexes."""
        return sum(index.byte_size for _, index in self._entries.values())

    def get(self, session_id: str) -> CodeIndex | None:
        """Return a cached index, refreshing its LRU position, or ``None``."""
        entry = self._entries.get(session_id)
        if entry is None:
            self._misses += 1
            return None
        stored_at, index = entry
        if self.ttl_seconds > 0 and (time.time() - stored_at) > self.ttl_seconds:
            del self._entries[session_id]
            self._evictions += 1
            self._misses += 1
            logger.debug("%s: expired index for session %s", self.name, session_id)
            return None
        self._entries.move_to_end(session_id)
        self._hits += 1
        return index

    def put(self, session_id: str, index: CodeIndex) -> bool:
        """Cache an index, evicting least-recently-used sessions to stay in budget."""
        if index.byte_size > self.max_bytes:
            self._refusals += 1
            logger.warning(
                "%s: refusing to cache %d bytes for session %s (budget %d bytes)",
                self.name,
                index.byte_size,
                session_id,
                self.max_bytes,
            )
            return False
        self._entries.pop(session_id, None)
        self._entries[session_id] = (time.time(), index)
        self._enforce_budget()
        return True

    def _enforce_budget(self) -> None:
        """Evict least-recently-used entries until both budgets are satisfied."""
        while len(self._entries) > 1 and (
            len(self._entries) > self.max_sessions or self.total_bytes > self.max_bytes
        ):
            evicted_session, (_, evicted_index) = self._entries.popitem(last=False)
            self._evictions += 1
            logger.info(
                "%s: evicted session %s (%d bytes) to stay within %d sessions / %d bytes",
                self.name,
                evicted_session,
                evicted_index.byte_size,
                self.max_sessions,
                self.max_bytes,
            )

    def invalidate(self, session_id: str) -> bool:
        """Drop one session's index (session deleted/archived, or turn cancelled)."""
        return self._entries.pop(session_id, None) is not None

    def clear(self) -> None:
        """Drop every cached index."""
        self._entries.clear()

    def stats(self) -> dict[str, int | float]:
        """Snapshot for metrics and diagnostics."""
        return {
            "sessions": len(self._entries),
            "bytes": self.total_bytes,
            "max_sessions": self.max_sessions,
            "max_bytes": self.max_bytes,
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._evictions,
            "refusals": self._refusals,
        }
