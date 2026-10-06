# AI & Agent Integration Guide

Rocket Chat is built from the ground up for seamless interaction with autonomous AI agents, coding assistants (Cursor, Copilot, Antigravity, Claude Code), and automated LLM evaluators.

---

## 1. Machine-Readable Documentation: `llms.txt` & `llms-full.txt`

To enable frontier language models to ingest the complete Rocket Chat documentation with zero friction, the documentation site automatically exposes two standardized endpoints conforming to the [llmstxt.org](https://llmstxt.org) standard:

1. **`/llms.txt` ([Summary Index](/ai/llms-index)):** A lightweight, structured markdown manifest describing each section of the documentation, curated specifically for LLM context windows.
2. **`/llms-full.txt`:** A complete, consolidated markdown export containing all pages across the documentation site. Agents can fetch this single file to achieve instant, zero-latency RAG without crawling multiple HTML endpoints.

---

## 2. Ingesting Rocket Chat into Your Agent

To provide your local AI agent or IDE with full architectural context on Rocket Chat:

=== "Curl / CLI"
    ```bash
    # Download complete documentation for LLM ingestion
    curl -s https://rocket-chat.dev/llms-full.txt > /tmp/rocket-chat-docs.txt
    ```

=== "Python Agent / LangChain / LlamaIndex"
    ```python
    import httpx

    response = httpx.get("https://rocket-chat.dev/llms-full.txt")
    full_docs_markdown = response.text
    # Load directly into context window or vector store
    ```

=== "Cursor / Windsurf / IDE Rules"
    Add to your `.cursorrules` or `.windsurfrules`:
    ```text
    Reference Rocket Chat documentation directly via https://rocket-chat.dev/llms.txt
    ```

---

## 3. Direct Markdown Access in Repository

When working directly in the Rocket Chat repository, AI agents can read all raw markdown files under `doc/` without needing to parse HTML or start a local web server:

- [Architecture Overview](file:///Users/nperriolat/Dev/rocket-chat/doc/architecture/index.md)
- [ReAct State Machine](file:///Users/nperriolat/Dev/rocket-chat/doc/architecture/agent-engine.md)
- [Dual Sandboxes](file:///Users/nperriolat/Dev/rocket-chat/doc/architecture/sandbox-runtimes.md)
- [PostgreSQL RLS](file:///Users/nperriolat/Dev/rocket-chat/doc/architecture/multi-tenancy.md)
- [Python Protocols](file:///Users/nperriolat/Dev/rocket-chat/doc/api-reference/python-protocols.md)
