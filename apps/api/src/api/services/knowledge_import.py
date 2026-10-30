"""Bringing documents into a knowledge base from outside: a web page, a documentation site (a
bounded crawl of its own pages), a PDF, or a repository's docs folder.

Every fetch the platform makes here goes through the SSRF-safe client, and every source is
bounded (pages, files, characters) before anything is embedded.
"""

from __future__ import annotations

import base64
import io
import re
from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from agent_core.tools.safe_http import UnsafeURLError, safe_get
from pypdf import PdfReader

MAX_SITE_PAGES = 25
MAX_PAGE_BYTES = 2_000_000
MAX_PDF_BYTES = 30_000_000
MAX_PDF_PAGES = 500
MAX_REPOSITORY_FILES = 200
MAX_TOTAL_CHARS = 2_000_000
DOC_EXTENSIONS = (".md", ".mdx", ".rst", ".txt", ".adoc")
GITHUB_TIMEOUT_SECONDS = 20.0


class KnowledgeImportError(ValueError):
    """A source that cannot be imported; the message says why."""


@dataclass(frozen=True)
class ImportedDocument:
    title: str
    content: str
    source: str


class _TextExtractor(HTMLParser):
    """The readable text of a page, its title and its links (scripts and styles dropped)."""

    _SKIPPED = frozenset({"script", "style", "noscript", "svg", "nav", "footer"})
    _BLOCKS = frozenset({"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "tr", "br"})

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self.links: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIPPED:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIPPED and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)

    @property
    def text(self) -> str:
        joined = "".join(self.parts)
        return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", joined)).strip()


async def _fetch(url: str) -> tuple[str, bytes]:
    try:
        response, body = await safe_get(url, max_bytes=MAX_PAGE_BYTES)
    except UnsafeURLError as err:
        raise KnowledgeImportError(str(err)) from err
    except httpx.HTTPError as err:
        raise KnowledgeImportError(f"Could not fetch {url}: {err}") from err
    if response.status_code >= 400:
        raise KnowledgeImportError(f"{url} answered HTTP {response.status_code}")
    return response.headers.get("content-type", ""), body


def _page(url: str, body: bytes) -> tuple[ImportedDocument, list[str]]:
    parser = _TextExtractor()
    parser.feed(body.decode("utf-8", errors="replace"))
    title = " ".join(parser.title.split()) or url
    return ImportedDocument(title[:200], parser.text, url), parser.links


async def import_url(url: str) -> list[ImportedDocument]:
    """One page (or a PDF the URL serves)."""
    content_type, body = await _fetch(url)
    if "pdf" in content_type or url.lower().endswith(".pdf"):
        return [pdf_document(body, url.rsplit("/", 1)[-1] or url, url)]
    return [_page(url, body)[0]]


async def import_site(url: str, max_pages: int = MAX_SITE_PAGES) -> list[ImportedDocument]:
    """The page and the pages it links to under the same address, breadth first."""
    start = urlparse(url)
    prefix = url.rsplit("/", 1)[0] + "/" if not url.endswith("/") else url
    queue, seen = deque([url]), {url}
    documents: list[ImportedDocument] = []
    total = 0
    while queue and len(documents) < min(max_pages, MAX_SITE_PAGES) and total < MAX_TOTAL_CHARS:
        current = queue.popleft()
        try:
            content_type, body = await _fetch(current)
        except KnowledgeImportError:
            continue
        if "html" not in content_type:
            continue
        document, links = _page(current, body)
        if document.content:
            documents.append(document)
            total += len(document.content)
        for link in links:
            target = urldefrag(urljoin(current, link))[0]
            parsed = urlparse(target)
            if parsed.netloc == start.netloc and target.startswith(prefix) and target not in seen:
                seen.add(target)
                queue.append(target)
    if not documents:
        raise KnowledgeImportError(f"No readable page found at {url}")
    return documents


def pdf_document(data: bytes, title: str, source: str) -> ImportedDocument:
    """The text of a PDF, page by page."""
    if len(data) > MAX_PDF_BYTES:
        raise KnowledgeImportError(f"The PDF is larger than {MAX_PDF_BYTES // 1_000_000} MB")
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages[:MAX_PDF_PAGES]]
    except Exception as err:
        raise KnowledgeImportError(f"Not a readable PDF: {err}") from err
    text = "\n\n".join(page.strip() for page in pages if page.strip())
    if not text:
        raise KnowledgeImportError("The PDF has no text (a scanned PDF needs OCR first)")
    return ImportedDocument(title[:200], text[:MAX_TOTAL_CHARS], source)


def pdf_from_base64(content: str, title: str) -> ImportedDocument:
    try:
        data = base64.b64decode(content, validate=True)
    except ValueError as err:
        raise KnowledgeImportError("The PDF upload is not valid base64") from err
    return pdf_document(data, title or "document.pdf", title or "upload")


async def import_repository_docs(
    repository: str, path: str, token: str | None, api_url: str = "https://api.github.com"
) -> list[ImportedDocument]:
    """The documentation files (Markdown, reStructuredText, text) under ``path`` of a GitHub
    repository's default branch, read with the organization's read access."""
    if not re.match(r"^[\w.-]+/[\w.-]+$", repository):
        raise KnowledgeImportError("Name the repository as owner/name")
    folder = path.strip("/")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=GITHUB_TIMEOUT_SECONDS, base_url=api_url) as client:
        meta = await client.get(f"/repos/{repository}", headers=headers)
        if meta.status_code != 200:
            raise KnowledgeImportError(f"{repository} could not be read (HTTP {meta.status_code})")
        branch = str(meta.json().get("default_branch") or "main")
        tree = await client.get(
            f"/repos/{repository}/git/trees/{branch}", params={"recursive": "1"}, headers=headers
        )
        paths = [
            str(item.get("path"))
            for item in (tree.json().get("tree") or [])
            if item.get("type") == "blob"
            and str(item.get("path")).lower().endswith(DOC_EXTENSIONS)
            and (not folder or str(item.get("path")).startswith(f"{folder}/"))
        ][:MAX_REPOSITORY_FILES]
        return await _read_files(client, repository, branch, paths, headers)


async def _read_files(
    client: httpx.AsyncClient,
    repository: str,
    branch: str,
    paths: list[str],
    headers: dict[str, str],
) -> list[ImportedDocument]:
    documents: list[ImportedDocument] = []
    total = 0
    raw = {**headers, "Accept": "application/vnd.github.raw+json"}
    for path in paths:
        if total >= MAX_TOTAL_CHARS:
            break
        response = await client.get(
            f"/repos/{repository}/contents/{path}", params={"ref": branch}, headers=raw
        )
        if response.status_code != 200 or not response.text.strip():
            continue
        documents.append(
            ImportedDocument(f"{repository}: {path}", response.text, f"{repository}/{path}")
        )
        total += len(response.text)
    if not documents:
        raise KnowledgeImportError(f"No documentation files found in {repository}")
    return documents


def describe_sources(documents: list[ImportedDocument]) -> dict[str, Any]:
    return {"documents": len(documents), "characters": sum(len(d.content) for d in documents)}
