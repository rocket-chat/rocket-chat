# API & Contracts Overview

Rocket Chat provides programmatic access through REST endpoints, bidirectional WebSockets, and strict Python protocol abstractions.

---

## 1. Interface Matrix

| Interface | Protocol / Format | Target Consumer | Primary Responsibilities |
| :--- | :--- | :--- | :--- |
| [**REST API**](/api-reference/rest-endpoints) | HTTP / JSON | Web UI, CI/CD, Webhooks | Session lifecycle, turn submission, GitHub App ingress, health |
| [**WebSocket Protocol**](/api-reference/websocket-protocol) | WSS / JSON Streaming | Web Mission Control, CLI | Low-latency streaming of thoughts, tool outputs, diffs, and gates |
| [**Python Protocols**](/api-reference/python-protocols) | Python `typing.Protocol` | Internal packages & plugins | Polymorphic drivers, gateways, orchestrators, and ciphers |

---

## 2. Interactive OpenAPI Documentation

When the FastAPI backend is running, complete interactive OpenAPI specifications are available at:
- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`
- **OpenAPI Schema (JSON):** `http://localhost:8000/openapi.json`
