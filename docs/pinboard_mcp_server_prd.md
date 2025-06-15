# Pinboard MCP Server – Product Requirements Document (PRD)

## 1. Document Meta

| Field | Value |
| ----- | ----- |
|       |       |

| **Project Name** | Pinboard MCP Server                                    |
| ---------------- | ------------------------------------------------------ |
| **Version**      | 1.0.0                                                  |
| **Authors**      |                                                        |
| **Last Updated** |                                                        |
| **Stakeholders** | Product • Engineering • QA • DevOps • Security • Legal |

## 2. Purpose & Background

Large‑Language‑Model (LLM) chat sessions benefit from personalised context. This project delivers a **local read‑only MCP server** that surfaces a user’s Pinboard.in bookmarks—via the Pinboard v1 API and the `pinboard.py` wrapper—so an LLM can search, filter and retrieve metadata (URL, tags, description, save‑date) at inference time.

Motivations:

- Let LLMs silently enrich answers with historical links the user has already deemed useful.
- Enable “what was I researching this week?” style prompts.

FastMCP 2.0 provides the scaffolding (Tool abstraction, async FastAPI server, JSON‑Schema validation) while `pinboard.py` simplifies API calls and error handling.

**Pinboard field nomenclature** – The upstream API re-uses the vestigial Delicious vocabulary: `description` is the bookmark title and `extended` is the free-form notes. The `pinboard.py` wrapper preserves these names. Throughout this PRD we map them to the more intuitive `title` and `notes` fields returned by the MCP.

## 3. Personas & Use Cases

| Persona                    | Need                                                                                                                                          | Example Flow                                                                                       |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| **Context Enrichment**     | Ask an LLM about *Synology NAS* and get relevant bookmarks from any time in the past (tagged `synology`, `nas`, or text match), including the date each link was saved. | Prompt → LLM client → **Pinboard MCP Server** → Pinboard API → Matching bookmarks → LLM → Response |
| **Conversational Starter** | Kick‑off a chat based on recent interests. Pull bookmarks from the last 7 days, summarise themes, then continue the conversation.             | LLM client requests `listRecentBookmarks(days=7)` before greeting the user                         |

## 4. Goals & Non‑Goals

### 4.1 Goals

- Expose **read‑only** Pinboard data via MCP tools (`searchBookmarks`, `listRecentBookmarks`, `listBookmarksByTags`, `listTags`).
- P50 latency < 250 ms for cached responses; < 600 ms cold (single‑user localhost).
- Respect Pinboard's unofficial guideline of **≤ 1 request every 3 s**; implement caching + `posts/update` polling.
- Provide OpenAPI 3.1 docs for every endpoint.
- Ship CI (lint, type‑check, tests, container) and observability hooks.

### 4.2 Non‑Goals

- Creating, editing or deleting bookmarks/tags.
- Full‑text indexing (initial release performs in‑memory filtering).

## 5. Functional Requirements

### 5.1 Tool Catalogue

| Tool                  | Verb  | Description                                                 | Params                                                        | Returns |
| --------------------- | ----- | ----------------------------------------------------------- | ------------------------------------------------------------- | ------- |
| `searchBookmarks`     | `GET` | Keyword search across description, extended notes and tags. | `query` (str), `limit` (int≤100, default 20)                  | List    |
| `listBookmarksByTags` | `GET` | Fetch bookmarks filtered by up to 3 tags.                   | `tags[]` (list [str] 1‑3), `from_date?`, `to_date?`, `limit?` | List    |
| `listRecentBookmarks` | `GET` | Return bookmarks saved in the last *n* days (default 7).    | `days` (int 1‑30), `limit?`                                   | List    |
| `listTags`            | `GET` | Retrieve all tags with counts.                              | —                                                             | List    |

### 5.1.1 Implementation Notes

- `searchBookmarks` is served from an in-memory cache seeded by `posts/all`, reducing Pinboard traffic at the cost of RAM (see § 12.4).
- Cache invalidation relies on `posts/update`, a lightweight endpoint that returns the timestamp of the last change; if unchanged, we skip re-download.
- Field-mapping reminder: Pinboard's `description` → `title`, `extended` → `notes`.
- v0.1 is deliberately **read-only**. Write operations (`posts/add`, `posts/delete`, tag rename, etc.) are deferred to a future MCP release (see Appendix D).

### 5.2 Authentication & Authorisation

- Users supply a **Pinboard API token** (format `username:hex`) via `PINBOARD_TOKEN` environment variable or macOS keychain.
- Server rejects requests if token absent or invalid → HTTP 401.

### 5.3 Session & Context Handling

- Optional `session_id` from MCP client is logged for traceability.
- The service keeps an **LRU cache** of the last 1000 query results keyed by `(search query, tags, date range)` to be able to give more complete responses given the rate limiting.

### 5.4 Caching & Rate Limiting

- When making a call to Pinboard, obey at least **3 s** between requests and back‑off on HTTP 429.

## 6. Non‑Functional Requirements

| Category                 | Requirement                                                                   |
| ------------------------ | ----------------------------------------------------------------------------- |
| **Performance**          | P50 < 250 ms cached; P95 < 600 ms cold; throughput 30 RPS burst (single user) |
| **Reliability**          | ≥ 99 % uptime (local daemon)                                                  |
| **Security**             | Token never logged; transport over HTTPS                 |
| **Privacy**              | No bookmark content leaves machine except to Pinboard servers                 |
| **Observability**        | JSON logs, Prometheus `/metrics`, OpenTelemetry traces                        |
| **Internationalisation** | UTF‑8; returns dates in ISO‑8601 Zulu                                         |

## 7. API Contract & Schemas

### 7.1 Bookmark

```json
{
  "type": "object",
  "required": ["id", "url", "title", "tags", "saved_at"],
  "properties": {
    "id": {"type": "string", "format": "uuid"},
    "url": {"type": "string", "format": "uri"},
    "title": {"type": "string"},
    "tags": {"type": "array", "items": {"type": "string"}},
    "notes": {"type": "string"},
    "saved_at": {"type": "string", "format": "date-time"}
  }
}
```

### 7.2 TagCount

```json
{
  "type": "object",
  "required": ["tag", "count"],
  "properties": {
    "tag": {"type": "string"},
    "count": {"type": "integer"}
  }
}
```

## 8. Error Handling

| Condition      | HTTP Status | Example Message                          |
| -------------- | ----------- | ---------------------------------------- |
| Missing token  |  401        | "PINBOARD\_TOKEN env var not set"        |
| Pinboard 429   |  429        | "Upstream rate limit hit – retry in 5 s" |
| Invalid param  |  400        | "days must be between 1 and 30"          |
| Upstream error |  502        | "Pinboard service unavailable"           |
| Internal error |  500        | "Unexpected server error"                |

## 9. Testing Requirements

- **Unit**: Mock `pinboard.Pinboard` with `pytest‑monkeypatch`; ≥ 90 % coverage.
- **Integration**: Spin up server, use `vcr.py` cassettes to replay real API samples.
- **Load**: k6 to 30 RPS, ensure < 1 % errors.

## 10. Metrics & Success

| Metric            | Target              |
| ----------------- | ------------------- |
| P50 / P95 Latency | < 250 ms / < 600 ms |
| Cache Hit Ratio   | ≥ 80 %              |
| 4xx Rate          | < 1 %               |
| 5xx Rate          | < 0.1 %             |

## 11. Dependencies & Tech Stack

- **FastMCP 2.0** – MCP scaffolding.
- **pinboard.py ≥ 2.0.0** – Pinboard client wrapper.
- **FastAPI**, **Uvicorn/Gunicorn**, **Poetry**.
- **pytest, pytest‑asyncio, responses, vcr.py** – testing.
- **redis‑lite** (optional) – cache backend.
- **ruff, mypy**, **OpenTelemetry**.

## 12. Open Questions & Risks

1. Should we implement local full‑text index (Whoosh/Lunr) for faster multi‑keyword search?
2. Pinboard API v2 is draft—risk of breaking changes.
3. Handling private vs public bookmarks—should privacy flag be exposed to LLM?
4. Memory footprint: `posts/all` may return 100 k+ bookmarks for power users.

## 13. Appendices

- **A. Example Pinboard Calls** – `posts/recent`, `tags/get`, `posts/all?fromdt=…`.
- **B. Sequence Diagram** – Prompt → MCP client → Pinboard MCP → Cache? → Pinboard API.
- **C. Rate‑Limit Table** – 1 req / 3 s guideline; 429 back‑off strategy.
- **D. Field Mapping & Future Write Tools** – Mapping of Pinboard to MCP fields and roadmap items.
