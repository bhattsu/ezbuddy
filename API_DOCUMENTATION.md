# US Legal Pro – Filing Chat API Documentation

> **Service:** `US Legal Pro filing chat`
> **Version:** `1.0.0`
> **Framework:** FastAPI (ASGI) + WebSockets
> **Interactive docs:** [`/docs`](http://localhost:8000/docs) (Swagger UI) · [`/redoc`](http://localhost:8000/redoc) (ReDoc)

This document describes every HTTP endpoint and WebSocket event exposed by the
service. The API drives three surfaces:

1. **Real-time filing assistant** over a single WebSocket (`/chatbot/ws`).
2. **HTTP APIs** for platform login, conversation history, document analysis,
   court-form field extraction, and the court-rules RAG knowledge base.
3. **Container / load-balancer probes** for liveness and readiness.

---

## Table of Contents

- [1. Base URL & Environments](#1-base-url--environments)
- [2. Authentication](#2-authentication)
- [3. Common Conventions](#3-common-conventions)
- [4. Error Format](#4-error-format)
- [5. Rate Limiting](#5-rate-limiting)
- [6. HTTP Endpoints](#6-http-endpoints)
  - [6.1 Root](#61-root)
  - [6.2 Health](#62-health)
  - [6.3 Authentication (Platform Login)](#63-authentication-platform-login)
  - [6.4 Conversations](#64-conversations)
  - [6.5 Document Analysis](#65-document-analysis)
  - [6.6 Court Form Questions](#66-court-form-questions)
  - [6.7 Court Rules Knowledge Base](#67-court-rules-knowledge-base)
- [7. WebSocket API – `/chatbot/ws`](#7-websocket-api--chatbotws)
  - [7.1 Connection Lifecycle](#71-connection-lifecycle)
  - [7.2 Envelope Format](#72-envelope-format)
  - [7.3 Client → Server Events](#73-client--server-events)
  - [7.4 Server → Client Events](#74-server--client-events)
  - [7.5 Filing Phases](#75-filing-phases)
  - [7.6 Filing Modes](#76-filing-modes)
  - [7.7 Notification Process Catalog](#77-notification-process-catalog)
  - [7.8 WebSocket Close Codes](#78-websocket-close-codes)
- [8. Shared Data Models](#8-shared-data-models)
- [9. Client Examples](#9-client-examples)

---

## 1. Base URL & Environments

| Environment | Base URL                              | WebSocket URL                          |
| ----------- | ------------------------------------- | -------------------------------------- |
| Local dev   | `http://localhost:8000`               | `ws://localhost:8000/chatbot/ws`       |
| Staging     | `https://<staging-host>`              | `wss://<staging-host>/chatbot/ws`      |
| Production  | `https://<production-host>`           | `wss://<production-host>/chatbot/ws`   |

- CORS is enabled for **all origins** (`*`), all methods and headers.
- Every HTTP response includes an `X-Request-ID` header (echoed back if the
  client supplies one, otherwise a fresh UUIDv4).
- During graceful shutdown the service returns **`503 Service Unavailable`**
  with `Retry-After: 30`. WebSockets are unaffected by the drain middleware.

---

## 2. Authentication

End-user authentication for the chat product is handled via the US Legal Pro
platform. Clients call [`POST /auth/login`](#63-authentication-platform-login)
with the customer's US Legal Pro credentials. The service authenticates
against the upstream platform, persists `auth_token` and `session_id` in
`operational.users`, and returns a stable **`user_id`**. That `user_id` is
the value the client sends in the WebSocket `session.init` event.

---

## 3. Common Conventions

- **Content type:** JSON request/response bodies use `application/json` unless
  explicitly documented otherwise. File uploads use `multipart/form-data`.
- **Timestamps:** ISO-8601 UTC (e.g. `2026-09-11T15:30:00Z`).
- **IDs:** `user_id`, `conversation_id`, `message_id`, `document_id` are UUIDs
  serialized as strings.
- **Character encoding:** UTF-8 throughout, including WebSocket text frames.
- **File uploads:** Max size is controlled by `MAX_FILE_SIZE_MB` (default
  `100`). Only PDF and DOCX files are accepted by the analysis endpoints.
- **Request tracing:** Provide an `X-Request-ID` header to correlate logs
  across services; it is echoed on the response.

---

## 4. Error Format

Errors follow FastAPI's default envelope:

```json
{
  "detail": "Human-readable message"
}
```

Court-rules and legal-filing endpoints may return the richer
`ErrorResponse` schema:

```json
{
  "error": "validation_error",
  "detail": "Uploaded file is empty.",
  "status_code": 400
}
```

Standard status codes used by the service:

| Status | Meaning                                                                 |
| ------ | ----------------------------------------------------------------------- |
| `200`  | OK                                                                      |
| `400`  | Validation failure (bad input, unsupported file type, empty payload).   |
| `401`  | Auth required or invalid credentials.                                   |
| `404`  | Requested resource (user, case, index) not found.                       |
| `429`  | Rate limit exceeded (see below).                                        |
| `500`  | Unexpected server error.                                                |
| `502`  | Upstream provider (US Legal Pro, S3, Bedrock) unreachable / bad reply.  |
| `503`  | Server draining, dependency unavailable, or misconfigured.              |

---

## 5. Rate Limiting

- Controlled by `RATE_LIMIT_ENABLED` (default `false`) and
  `RATE_LIMIT_PER_MINUTE` (default `60`).
- Keying strategy:
  - If `AUTH_METHOD=api_key`, requests are grouped by the `X-API-Key` value.
  - Otherwise the client IP (`X-Forwarded-For` aware) is used.
- Exceeding the limit returns **`429 Too Many Requests`**.
- Currently applied to: `POST /auth/login`,
  `POST /api/conversations/user-messages`, `POST /api/analyze`, and
  `POST /api/court-form/questions`.

---

## 6. HTTP Endpoints

### 6.1 Root

#### `GET /`

Returns the endpoint index and links to interactive documentation.

**Response 200**

```json
{
  "message": "US Legal Pro filing chat",
  "docs": "/docs",
  "redoc": "/redoc",
  "endpoints": {
    "health": {
      "health": "/api/health/",
      "liveness": "/api/health/live",
      "readiness": "/api/health/ready"
    },
    "legal_filing_chatbot": {
      "login": "/auth/login",
      "websocket": "/chatbot/ws",
      "test_ui": "chatbot_test.html (project root — open in browser)"
    },
    "conversations": {
      "user_messages": "/api/conversations/user-messages"
    },
    "document_analysis": {
      "analyze": "/api/analyze"
    },
    "court_form_questions": {
      "questions": "/api/court-form/questions"
    }
  }
}
```

---

### 6.2 Health

All health endpoints are grouped under `/api/health`. Both `/api/health` and
`/api/health/` return the same body (the un-slashed variant is aliased so
load-balancer probes do not receive a 307 redirect).

#### `GET /api/health/` — Liveness (aliased)

```json
{
  "status": "healthy",
  "service": "us-legal-pro-filing-chat",
  "version": "1.0.0"
}
```

#### `GET /api/health/live` — Liveness probe

Returns `200 OK` if the process is up. Performs no dependency checks.

```json
{ "status": "ok" }
```

#### `GET /api/health/ready` — Readiness probe

Runs configurable dependency checks (extractor factory, and — when enabled
via `HEALTH_CHECK_S3` / `HEALTH_CHECK_BEDROCK` — S3 and Bedrock). Returns
`200` when every check passes.

**Response 200**

```json
{
  "status": "ready",
  "checks": {
    "config": "ok",
    "extractor": "ok",
    "s3": "ok",
    "bedrock": "ok"
  }
}
```

**Response 503**

```json
{
  "detail": {
    "status": "not_ready",
    "checks": { "config": "ok", "extractor": "ok", "s3": "AccessDenied" },
    "failed": ["s3"]
  }
}
```

---

### 6.3 Authentication (Platform Login)

#### `POST /auth/login`

Authenticate against the US Legal Pro platform. On success, the service
stores `auth_token` and `session_id` in `operational.users` and returns the
internal `user_id` that must be supplied when opening a WebSocket session.

**Request body — `LoginRequest`**

| Field      | Type   | Required | Description                                        |
| ---------- | ------ | -------- | -------------------------------------------------- |
| `username` | string | yes      | Account email / username.                          |
| `password` | string | yes      | Plaintext password (min length 1).                 |
| `state`    | string | no       | US state code used in the upstream path. Default `ca`. |

```json
{
  "username": "jane.doe@example.com",
  "password": "•••••••",
  "state": "tx"
}
```

**Response 200 — `LoginResponse`**

```json
{
  "user_id": "8b3d3a5e-2c98-4e88-9b0c-8a3f0d6c9b21",
  "email": "jane.doe@example.com",
  "session_id": "sess_01HZ8ZQK3R6H2Q7...",
  "auth_token": "eyJhbGciOi..."
}
```

**Error responses**

| Status | Condition                                             |
| ------ | ----------------------------------------------------- |
| `401`  | Invalid credentials.                                  |
| `429`  | Rate limit exceeded.                                  |
| `502`  | Upstream US Legal Pro API unreachable.                |
| `503`  | RDS not configured or unavailable.                    |

---

### 6.4 Conversations

#### `POST /api/conversations/user-messages`

Return every message the user has sent, across all conversations. The user
is resolved by `(email, session_id)` against `operational.users`.

**Request body — `UserMessagesRequest`**

| Field        | Type   | Required | Description                            |
| ------------ | ------ | -------- | -------------------------------------- |
| `email`      | string | yes      | User email from `operational.users`.   |
| `session_id` | string | yes      | Platform session id returned by login. |

**Response 200 — `UserMessagesResponse`**

```json
{
  "user_id": "8b3d3a5e-2c98-4e88-9b0c-8a3f0d6c9b21",
  "email": "jane.doe@example.com",
  "session_id": "sess_01HZ8ZQK3R6H2Q7...",
  "messages": [
    {
      "message_id": "b1e7a5f0-...-3f7f",
      "conversation_id": "e9a2b8c4-...-04c2",
      "message": "I want to file for divorce in Texas",
      "created_at": "2026-09-11T15:22:14.483Z",
      "conversation_session_id": "d2c1b6a3-...-27a9",
      "conversation_status": "active"
    }
  ]
}
```

**Errors**

| Status | Condition                                               |
| ------ | ------------------------------------------------------- |
| `404`  | No user matches the supplied `email`/`session_id`.      |
| `503`  | RDS not configured or unavailable.                      |

---

### 6.5 Document Analysis

#### `POST /api/analyze`

Upload a court PDF or DOCX. PDFs are visually analyzed page-by-page by a
Vision LLM to identify filled values and every blank form field. DOCX files
are extracted textually and normalized by an LLM.

**Request — `multipart/form-data`**

| Part   | Type | Required | Description                              |
| ------ | ---- | -------- | ---------------------------------------- |
| `file` | file | yes      | Court document (`.pdf` or `.docx`).      |

**Response 200 — `DocumentAnalysisResponse`**

```json
{
  "document_id": "a76b2e9a-...-1f6c",
  "file_name": "petition.pdf",
  "extracted_fields": {
    "petitioner_name": "Jane Doe",
    "case_number": null,
    "filing_date": "2026-09-01"
  },
  "user_details": {
    "petitioner_name": "Jane Doe",
    "respondent_name": "John Doe"
  },
  "missing_fields": ["case_number", "county"],
  "document_classification": "Original Petition for Divorce",
  "case_type": "family",
  "sub_case_type": "divorce",
  "raw_textract": null,
  "message": "Document analyzed. Review extracted details and provide any missing fields.",
  "metadata": {}
}
```

**Errors**

| Status | Condition                                     |
| ------ | --------------------------------------------- |
| `400`  | Empty upload or unsupported file type.        |
| `401`  | Auth required by `AUTH_METHOD` and missing.   |
| `429`  | Rate limit exceeded.                          |
| `500`  | Analysis pipeline failure.                    |

---

### 6.6 Court Form Questions

#### `POST /api/court-form/questions`

Extract every form field on a court form as a **question** with a simple
string answer. Provide **either** a file upload **or** an S3 URL — not both.

**Request — `multipart/form-data`**

| Part     | Type   | Required                        | Description                                    |
| -------- | ------ | ------------------------------- | ---------------------------------------------- |
| `file`   | file   | one-of `file` / `s3_url` (exactly one) | Court form PDF.                                 |
| `s3_url` | string | one-of `file` / `s3_url` (exactly one) | `s3://bucket/key` or HTTPS S3 URL.              |

**Response 200 — `CourtFormQuestionsResponse`**

```json
{
  "document_id": "de2a3b91-...-9c72",
  "file_name": "OCA_Divorce_Petition.pdf",
  "source": "upload",
  "questions": [
    {
      "question": "What is the petitioner's full legal name?",
      "answer": "",
      "field": "Petitioner Name",
      "page": 1
    },
    {
      "question": "What is the county of filing?",
      "answer": "Travis",
      "field": "County",
      "page": 1
    }
  ],
  "message": "Court form fields extracted. Each item is a question with a simple string answer."
}
```

**Errors**

| Status | Condition                                                       |
| ------ | --------------------------------------------------------------- |
| `400`  | Both or neither of `file`/`s3_url` provided; empty file; non-PDF. |
| `429`  | Rate limit exceeded.                                            |
| `502`  | S3 download failed.                                             |
| `500`  | Textract / LLM pipeline error.                                  |

---

### 6.7 Court Rules Knowledge Base

RAG over an OpenSearch index (`court_rules` by default). All routes are
prefixed with `/api/court-rules`.

#### `POST /api/court-rules/ingest`

Upload a `.txt` or `.docx` court-rules document. The service chunks the
text, embeds each chunk with Bedrock Titan, and writes to OpenSearch.
Re-uploading the same logical source **replaces** prior chunks when
`replace_existing=true`.

**Request — `multipart/form-data`**

| Part               | Type    | Required | Default | Description                                                                       |
| ------------------ | ------- | -------- | ------- | --------------------------------------------------------------------------------- |
| `file`             | file    | yes      | —       | `.txt` or `.docx`.                                                                |
| `state_code`       | string  | no       | `TX`    | Two-letter US state code.                                                         |
| `case_type`        | string  | no       | `divorce` | Case type identifier.                                                            |
| `doc_type`         | string  | no       | —       | `faq` \| `standard_rules` \| `statewide_rule` \| `court_rule`.                    |
| `replace_existing` | boolean | no       | `true`  | If true, deletes prior chunks for the same source before writing new ones.        |

**Response 200 — `CourtRulesIngestResponse`**

```json
{
  "success": true,
  "collection_name": "court_rules",
  "vector_store": "opensearch",
  "source_key": "TX/divorce/faq/TX_FAQ.txt",
  "source_file": "TX_FAQ.txt",
  "state_code": "TX",
  "case_type": "divorce",
  "doc_type": "faq",
  "total_documents": 1,
  "total_chunks": 132,
  "vectors_stored": 132,
  "failed_chunks": 0,
  "vector_dim": 1024,
  "metadata": {}
}
```

#### `DELETE /api/court-rules/index`

Drop the `court_rules` OpenSearch index. Use when the index was
auto-created with an incompatible mapping (embedding not `knn_vector`).
Re-ingest afterwards.

**Response 200 — `CourtRulesResetIndexResponse`**

```json
{
  "collection_name": "court_rules",
  "vector_store": "opensearch",
  "existed": true,
  "deleted": true,
  "previous_embedding_type": "float"
}
```

#### `POST /api/court-rules/retrieve`

Semantic search only — returns the top-k chunks without invoking the LLM.

**Request body — `CourtRulesRetrieveRequest`**

| Field         | Type    | Required | Description                                                       |
| ------------- | ------- | -------- | ----------------------------------------------------------------- |
| `query`       | string  | yes      | Search text, min length 2.                                        |
| `state_code`  | string  | no       | Filter by state (e.g. `TX`).                                      |
| `case_type`   | string  | no       | Filter by case type (e.g. `divorce`).                             |
| `top_k`       | integer | no       | 1–20. Defaults to `COURT_RULES_TOP_K` (typically 3).              |

**Response 200 — `CourtRulesRetrieveResponse`**

```json
{
  "query": "How long is the waiting period for divorce in Texas?",
  "top_k": 3,
  "chunk_count": 3,
  "collection_name": "court_rules",
  "vector_store": "opensearch",
  "filters": { "state_code": "TX", "case_type": "divorce" },
  "chunks": [
    {
      "rank": 1,
      "id": "TX/divorce/faq/TX_FAQ.txt#12",
      "score": 0.82,
      "content": "Texas requires a 60-day waiting period from the date...",
      "source_file": "TX_FAQ.txt",
      "source_key": "TX/divorce/faq/TX_FAQ.txt",
      "section_title": "Waiting Period",
      "state_code": "TX",
      "case_type": "divorce",
      "doc_type": "faq",
      "chunk_index": 12,
      "metadata": {}
    }
  ]
}
```

#### `POST /api/court-rules/query`

Retrieve relevant chunks **and** generate a grounded LLM answer.

**Request body — `CourtRulesQueryRequest`**

| Field        | Type    | Required | Description                                          |
| ------------ | ------- | -------- | ---------------------------------------------------- |
| `question`   | string  | yes      | Question text, min length 2.                         |
| `state_code` | string  | no       | Filter by state.                                     |
| `case_type`  | string  | no       | Filter by case type.                                 |
| `top_k`      | integer | no       | 1–20. Defaults to `COURT_RULES_TOP_K`.               |

**Response 200 — `CourtRulesQueryResponse`**

```json
{
  "answer": "In Texas, there is a mandatory 60-day waiting period...",
  "sources": [
    { "source_file": "TX_FAQ.txt", "section_title": "Waiting Period", "score": 0.82 }
  ],
  "used_rag": true,
  "collection_name": "court_rules",
  "vector_store": "opensearch",
  "filters": { "state_code": "TX", "case_type": "divorce" }
}
```

---

## 7. WebSocket API – `/chatbot/ws`

The filing assistant runs entirely over a single WebSocket. The server is
event-driven: after the client sends `session.init`, every subsequent
client action produces one or more typed events describing the assistant's
reply, phase transitions, checklist updates, and background progress
toasts (`notification`).

### 7.1 Connection Lifecycle

```
Client                                Server
  |                                      |
  |----- WS handshake ------------------->|
  |<---- 101 Switching Protocols ---------|
  |                                      |
  |----- {"type":"session.init", ...} --->|
  |                                      |--- create/resume conversation
  |<---- {"type":"notification", ...} ----|   (0..N toast frames)
  |<---- {"type":"session.started", ...} -|
  |                                      |
  |----- {"type":"user.message", ...} --->|
  |<---- {"type":"notification", ...} ----|
  |<---- {"type":"assistant.message", ...}|
  |             ...                       |
  |----- {"type":"user.upload", ...} ---->|
  |<---- {"type":"notification", ...} ----|
  |<---- {"type":"analysis.complete", ...}|
  |<---- {"type":"assistant.message", ...}|
  |             ...                       |
  |------- WS close --------------------->|
```

**Rules of engagement**

1. The first frame from the client **must** be `session.init`. Any other
   event before it is a no-op.
2. Every subsequent client event **must** include the `conversation_id`
   assigned in `session.started`.
3. Text frames are UTF-8 JSON. Binary frames are decoded as UTF-8 JSON and
   handled identically; there is no separate binary protocol.
4. Invalid JSON → single `error` event with `message: "Invalid JSON payload"`.
5. Unknown `type` → single `error` event describing the unknown type.

### 7.2 Envelope Format

All frames share a common envelope:

```json
{
  "type": "<event.type>",
  "conversation_id": "<uuid, present on all post-session events>",
  "payload": { "...": "event-specific fields" }
}
```

- The `payload` object is always present on server events (except `error`
  where it is still present and contains the `ErrorPayload`).
- Client events do not use `payload`; fields are top-level on the envelope.

### 7.3 Client → Server Events

#### `session.init`

Initialises or resumes a conversation for the given user.

| Field             | Type              | Required | Description                                                       |
| ----------------- | ----------------- | -------- | ----------------------------------------------------------------- |
| `type`            | `"session.init"`  | yes      | Fixed literal.                                                    |
| `user_id`         | string (UUID)     | yes      | Internal user id returned by `POST /auth/login`.                  |
| `conversation_id` | string (UUID)     | no       | Resume an existing conversation. Omit / `null` to start fresh.    |
| `case_id`         | string            | no       | Optional pre-selected case id (used by "existing case" flows).    |

```json
{
  "type": "session.init",
  "user_id": "8b3d3a5e-2c98-4e88-9b0c-8a3f0d6c9b21",
  "conversation_id": null
}
```

#### `user.message`

Free-text turn from the user.

| Field             | Type              | Required | Description                                            |
| ----------------- | ----------------- | -------- | ------------------------------------------------------ |
| `type`            | `"user.message"`  | yes      | Fixed literal.                                         |
| `conversation_id` | string (UUID)     | yes      | Conversation returned by `session.started`.            |
| `content`         | string            | yes      | User utterance (may be empty when only using selects). |

```json
{
  "type": "user.message",
  "conversation_id": "e9a2b8c4-...-04c2",
  "content": "Texas, Travis County"
}
```

#### `user.upload`

Upload one or more court documents (PDF or DOCX). The service supports
either the legacy single-file shape (`file_name` + `content_base64` at the
top level) **or** an array under `files`. When `files` is provided, it
takes precedence.

| Field             | Type                | Required                    | Description                                          |
| ----------------- | ------------------- | --------------------------- | ---------------------------------------------------- |
| `type`            | `"user.upload"`     | yes                         | Fixed literal.                                       |
| `conversation_id` | string (UUID)       | yes                         | Conversation returned by `session.started`.          |
| `file_name`       | string              | one-of single-file / `files` | File name (single-file shape).                        |
| `content_base64`  | string              | one-of single-file / `files` | Base64-encoded bytes (single-file shape).             |
| `files`           | `UploadFileItem[]`  | one-of single-file / `files` | Multiple files (see below).                          |

Each `UploadFileItem`:

```json
{ "file_name": "petition.pdf", "content_base64": "JVBERi0xLj..." }
```

Constraints:

- Only PDF and DOCX are accepted. Any other detected file type produces
  an `error` event (`"Only PDF/DOCX uploads are supported (got <type>)"`).
- Empty uploads produce an `error` event and the file is skipped.

```json
{
  "type": "user.upload",
  "conversation_id": "e9a2b8c4-...-04c2",
  "files": [
    { "file_name": "petition.pdf", "content_base64": "JVBERi0xLj..." }
  ]
}
```

### 7.4 Server → Client Events

Every server event is delivered inside the standard envelope shown in
[7.2](#72-envelope-format). The tables below describe the `payload` of
each event.

#### `notification`

Emitted while a step is in progress or on completion. Clients typically
render it as a single top-right toast that stays until the next
notification replaces it.

**Payload — `NotificationPayload`**

| Field     | Type                                | Description                                     |
| --------- | ----------------------------------- | ----------------------------------------------- |
| `message` | string                              | Short human-readable status text.               |
| `process` | string                              | Stable key from the [Process Catalog](#77-notification-process-catalog). |
| `level`   | `"info" \| "success" \| "error"`    | Visual severity. Defaults to `info`.            |

```json
{
  "type": "notification",
  "conversation_id": "e9a2b8c4-...-04c2",
  "payload": {
    "message": "Loading the counties",
    "process": "loading_counties",
    "level": "info"
  }
}
```

Duplicate consecutive `process` keys are deduplicated by the server on the
same turn.

#### `session.started`

Emitted once per WebSocket, immediately after `session.init` succeeds.

**Payload — `SessionStartedPayload`**

| Field               | Type                       | Description                                                 |
| ------------------- | -------------------------- | ----------------------------------------------------------- |
| `message`           | string                     | Assistant greeting.                                         |
| `conversation_id`   | string (UUID)              | Conversation id (also on envelope).                         |
| `user_id`           | string (UUID)              | Echoed user id.                                             |
| `phase`             | `FilingPhase`              | Starting phase (usually `greeting`).                        |
| `mode`              | `FilingMode`               | Starting mode (usually `unset`).                            |
| `history`           | array<object>              | Prior turns for a resumed conversation (may be empty).      |
| `chat_context`      | `ChatTurn[]`               | Rolling `(request, response)` window used by the LLM.       |
| `selection_options` | `SelectionOptionsPayload?` | Present when the phase expects a dropdown/text selection.   |
| `metadata`          | object                     | Additional orchestration metadata (opaque).                 |

#### `assistant.message`

Default assistant reply — used for every phase transition that doesn't
warrant a specialised event.

**Payload — `AssistantMessagePayload`**

| Field               | Type                       | Description                                                                     |
| ------------------- | -------------------------- | ------------------------------------------------------------------------------- |
| `message`           | string                     | Rendered assistant text.                                                        |
| `phase`             | `FilingPhase`              | Current phase.                                                                  |
| `mode`              | `FilingMode`               | Current mode.                                                                   |
| `selections`        | object                     | User selections captured so far (state, county, case category, etc.).           |
| `collected_data`    | object                     | Workflow form answers keyed by field name.                                      |
| `checklist`         | `ChecklistPayload?`        | Progress on the workflow checklist.                                             |
| `selection_options` | `SelectionOptionsPayload?` | Dropdown/text prompt for the next answer.                                       |
| `chat_context`      | `ChatTurn[]`               | Rolling `(request, response)` window used by the LLM.                           |
| `metadata`          | object                     | Additional orchestration metadata (opaque).                                     |

#### `workflow.complete`

All workflow questions have been answered. Sent immediately before the
document generation phase.

**Payload — `WorkflowCompletePayload`**

| Field             | Type              | Description                              |
| ----------------- | ----------------- | ---------------------------------------- |
| `conversation_id` | string (UUID)     | Conversation id.                         |
| `collected_data`  | object            | All final workflow answers.              |
| `selections`      | object            | Case-scoping selections.                 |
| `checklist`       | `ChecklistPayload?` | Final checklist state (all `answered`). |

#### `case.located`

Emitted in the **existing case** flow when the case has been found on the
US Legal Pro platform.

**Payload — `CaseLocatedPayload`**

| Field             | Type          | Description                                     |
| ----------------- | ------------- | ----------------------------------------------- |
| `conversation_id` | string (UUID) | Conversation id.                                |
| `case_metadata`   | object        | Full case record from the platform.             |
| `selections`      | object        | Selections that led to the match.               |

#### `analysis.complete`

Uploaded documents have been analysed and any prefilled fields are ready.
The server always follows this with an `assistant.message` event to
resume the conversation.

**Payload — `AnalysisCompletePayload`**

| Field              | Type              | Description                                                   |
| ------------------ | ----------------- | ------------------------------------------------------------- |
| `conversation_id`  | string (UUID)     | Conversation id.                                              |
| `analysis`         | object            | Raw analysis blob (identical shape to `DocumentAnalysisResponse`). |
| `message`          | string            | Assistant summary of the analysis.                            |
| `prefilled_fields` | object            | Fields auto-populated into the workflow checklist.            |
| `checklist`        | `ChecklistPayload?` | Checklist after prefill.                                     |
| `files`            | array<object>     | Per-file summaries (name, type, size, template match, etc.). |

#### `documents.offer`

The workflow is complete and the assistant is offering optional supporting
uploads before generating filing documents.

**Payload — `DocumentsOfferPayload`**

| Field                | Type              | Description                              |
| -------------------- | ----------------- | ---------------------------------------- |
| `conversation_id`    | string (UUID)     | Conversation id.                         |
| `message`            | string            | Assistant prompt.                        |
| `phase`              | `FilingPhase`     | Usually `offering_documents`.            |
| `required_documents` | array<object>     | Documents the court requires.            |
| `checklist`          | `ChecklistPayload?` | Current checklist state.                |

#### `documents.ready`

Filing documents have been generated and are available for download or
inline preview.

**Payload — `DocumentsReadyPayload`**

| Field                | Type                          | Description                                                     |
| -------------------- | ----------------------------- | --------------------------------------------------------------- |
| `conversation_id`    | string (UUID)                 | Conversation id.                                                |
| `message`            | string                        | Assistant summary.                                              |
| `collected_data`     | object                        | Final workflow answers used for generation.                     |
| `selections`         | object                        | Final case selections.                                          |
| `checklist`          | `ChecklistPayload?`           | Checklist snapshot.                                             |
| `required_documents` | array<object>                 | Documents required by the court (with `is_required` flags).    |
| `documents`          | `GeneratedDocumentPayload[]`  | Generated / skipped / errored documents (see below).            |

Each `GeneratedDocumentPayload`:

| Field                      | Type      | Description                                                        |
| -------------------------- | --------- | ------------------------------------------------------------------ |
| `template_code`            | string    | Internal template code.                                            |
| `template_name`            | string    | Human-readable template name.                                      |
| `file_name`                | string    | Output file name (e.g. `case_document.pdf`).                       |
| `html_content`             | string?   | Inline HTML preview, if produced.                                  |
| `ftl_content`              | string?   | Freemarker template snapshot, if produced.                         |
| `download_url`             | string?   | Signed URL for downloading the artifact.                           |
| `skipped_because_uploaded` | boolean   | `true` when the user already uploaded a matching document.         |
| `error`                    | string?   | Populated when generation failed for this template.                |

#### `error`

Emitted for validation failures, invalid client events, or orchestration
errors. When followed by `close`, the code will be `1011`.

**Payload — `ErrorPayload`**

| Field     | Type    | Description                                       |
| --------- | ------- | ------------------------------------------------- |
| `code`    | string  | Machine-readable code. Defaults to `"error"`.     |
| `message` | string  | Human-readable message.                           |
| `detail`  | string? | Optional additional context.                      |

```json
{
  "type": "error",
  "conversation_id": "e9a2b8c4-...-04c2",
  "payload": { "code": "error", "message": "Invalid JSON payload" }
}
```

### 7.5 Filing Phases

`FilingPhase` marks where the assistant is in the flow. It is included on
`session.started` and every `assistant.message`.

| Value                              | Description                                             |
| ---------------------------------- | ------------------------------------------------------- |
| `greeting`                         | Initial hand-off from the assistant.                    |
| `intent_pending`                   | Waiting for the user to choose new vs existing case.    |
| `selecting_state`                  | Selecting the filing state.                             |
| `selecting_county`                 | Selecting the county.                                   |
| `selecting_jurisdiction`           | Selecting the court.                                    |
| `selecting_case_category`          | Selecting the case category.                            |
| `selecting_case_type`              | Selecting the case type.                                |
| `selecting_case_parties`           | Selecting the filing party type(s).                     |
| `selecting_filing_code`            | Selecting the filing code.                              |
| `selecting_document_type`          | Selecting the document type to file.                    |
| `offering_documents`               | Offering optional supporting-document uploads.          |
| `awaiting_document_upload`         | Waiting for the user to upload documents.               |
| `collecting_workflow_answers`      | Collecting form answers for the workflow.               |
| `generating_documents`             | Building the filing document(s).                        |
| `verifying_platform_payment`       | Verifying US Legal Pro payment.                         |
| `verifying_court_payment`          | Loading court payment accounts.                         |
| `confirming_efile`                 | Reviewing the e-file request.                           |
| `existing_lookup_method`           | Choosing how to find an existing case.                  |
| `existing_selecting_state`         | State selection for existing-case flow.                 |
| `existing_selecting_jurisdiction`  | Court selection for existing-case flow.                 |
| `existing_enter_case_number`       | Manually entering a case number.                        |
| `existing_search_party`            | Searching an existing case by party name.               |
| `existing_search_date`             | Searching an existing case by filing date.              |
| `existing_case_confirm`            | Confirming the matched existing case.                   |
| `existing_upload_analysis`         | Analysing an uploaded case document.                    |
| `complete`                         | Filing complete.                                        |

### 7.6 Filing Modes

`FilingMode` describes the high-level path the user is on.

| Value              | Description                                             |
| ------------------ | ------------------------------------------------------- |
| `unset`            | Not yet decided.                                        |
| `generic`          | General legal Q&A (no filing).                          |
| `filing_new`       | Filing a new case.                                      |
| `filing_existing`  | Continuing / interacting with an existing case.         |

### 7.7 Notification Process Catalog

The `process` field on a `notification` payload is a stable key that the
UI can use to render icons, colours, and localised text. The service will
never emit unlisted keys for a happy-path flow.

| Process key                       | Default message                             | Default level |
| --------------------------------- | ------------------------------------------- | ------------- |
| `session_started`                 | Filing chat is ready                        | success       |
| `processing_request`              | Processing your request                     | info          |
| `greeting`                        | Starting the filing chat                    | info          |
| `loading_states`                  | Loading the states                          | info          |
| `selecting_state`                 | Select the filing state                     | info          |
| `intent_pending`                  | Choose new case or existing case            | info          |
| `loading_counties`                | Loading the counties                        | info          |
| `selecting_county`                | Select the county                           | info          |
| `loading_courts`                  | Loading the courts                          | info          |
| `selecting_jurisdiction`          | Select the court                            | info          |
| `loading_case_categories`         | Loading the case categories                 | info          |
| `selecting_case_category`         | Select the case category                    | info          |
| `loading_case_types`              | Loading the case types                      | info          |
| `selecting_case_type`             | Select the case type                        | info          |
| `loading_party_types`             | Loading the filing parties                  | info          |
| `selecting_case_parties`          | Select the filing party                     | info          |
| `loading_filing_codes`            | Loading the filing codes                    | info          |
| `selecting_filing_code`           | Select the filing code                      | info          |
| `loading_document_types`          | Loading the document types                  | info          |
| `selecting_document_type`         | Select the document to file                 | info          |
| `extracting_template`             | Extracting the questions                    | info          |
| `extracting_questions`            | Extracting the questions                    | info          |
| `loading_questions`               | Loading the questions                       | info          |
| `questions_ready`                 | Form questions are ready                    | success       |
| `matching_workflow`               | Matching the case workflow                  | info          |
| `collecting_workflow_answers`     | Collecting form answers                     | info          |
| `offering_documents`              | You can upload supporting documents         | info          |
| `awaiting_document_upload`        | Waiting for document upload                 | info          |
| `analyzing_upload`                | Analyzing the uploaded document             | info          |
| `prefilling_answers`              | Filling answers from the upload             | info          |
| `analysis_complete`               | Document analysis finished                  | success       |
| `generating_documents`            | Building the form JSON                      | info          |
| `mapping_fields`                  | Mapping answers onto template fields        | info          |
| `generating_court_document`       | Generating case_document.pdf                | info          |
| `documents_ready`                 | case_document.pdf is ready                  | success       |
| `verifying_platform_payment`      | Verifying US Legal Pro payment              | info          |
| `verifying_court_payment`         | Loading court payment accounts              | info          |
| `confirming_efile`                | Review the e-file request                   | info          |
| `submitting_efile`                | Submitting your filing to the court         | info          |
| `checking_envelope_status`        | Checking envelope status                    | info          |
| `existing_lookup_method`          | Choose how to find the existing case        | info          |
| `existing_selecting_state`        | Select the filing state                     | info          |
| `existing_selecting_jurisdiction` | Select the court                            | info          |
| `existing_enter_case_number`      | Enter the existing case number              | info          |
| `searching_existing_case`         | Searching for the existing case             | info          |
| `loading_case_details`            | Loading the case details                    | info          |
| `existing_search_party`           | Searching by party name                     | info          |
| `existing_search_date`            | Searching by filing date                    | info          |
| `existing_case_confirm`           | Confirm the existing case                   | info          |
| `existing_upload_analysis`        | Reading the uploaded case document          | info          |
| `case_located`                    | Existing case found                         | success       |
| `answering_legal_question`        | Answering your legal question               | info          |
| `generic_legal`                   | Legal question answered                     | success       |
| `complete`                        | Filing is complete                          | success       |
| `error`                           | Something went wrong                        | error         |

### 7.8 WebSocket Close Codes

| Code   | Meaning                                                                 |
| ------ | ----------------------------------------------------------------------- |
| `1000` | Normal closure (client-initiated).                                      |
| `1001` | Server going away (deploy / shutdown).                                  |
| `1011` | Internal server error. Preceded by an `error` event when possible.      |

---

## 8. Shared Data Models

These models appear across multiple events and endpoints.

### `ChatTurn`

Rolling (request, response) window used by the LLM.

```json
{ "request": "Texas", "response": "Great — which county?" }
```

### `ChecklistPayload`

Workflow progress. Emitted on `assistant.message`, `analysis.complete`,
`documents.offer`, `documents.ready`, and `workflow.complete`.

```json
{
  "total": 12,
  "answered": 7,
  "pending": 4,
  "skipped": 1,
  "items": [
    {
      "field_name": "petitioner_name",
      "label": "Petitioner's full legal name",
      "required": true,
      "sort_order": 1,
      "status": "answered",
      "value": "Jane Doe"
    }
  ]
}
```

`status` is one of `"pending" | "answered" | "skipped"`.

### `SelectionOptionsPayload`

Rendered when the assistant expects a discrete choice (dropdown) or a
short text answer.

```json
{
  "phase": "selecting_state",
  "type": "dropdown",
  "prompt": "Which state are you filing in?",
  "total": 3,
  "options": [
    { "label": "Texas",      "value": "TX", "code": "TX" },
    { "label": "California", "value": "CA", "code": "CA" },
    { "label": "New York",   "value": "NY", "code": "NY" }
  ]
}
```

`type` is one of `"dropdown" | "text"`. When `type=text`, `options` is
empty and `prompt` describes the expected free-text answer.

### `UploadFileItem`

Single file inside a `user.upload` batch.

```json
{ "file_name": "petition.pdf", "content_base64": "<base64>" }
```

---

## 9. Client Examples

### 9.1 cURL — Login

```bash
curl -X POST https://api.example.com/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "jane.doe@example.com",
    "password": "secret",
    "state": "tx"
  }'
```

### 9.2 cURL — Analyze a document

```bash
curl -X POST https://api.example.com/api/analyze \
  -H "X-API-Key: $API_KEY" \
  -F "file=@./petition.pdf"
```

### 9.3 cURL — Court-rules ingest

```bash
curl -X POST https://api.example.com/api/court-rules/ingest \
  -F "file=@./TX_FAQ.txt" \
  -F "state_code=TX" \
  -F "case_type=divorce" \
  -F "doc_type=faq" \
  -F "replace_existing=true"
```

### 9.4 JavaScript — WebSocket flow

```javascript
const ws = new WebSocket("wss://api.example.com/chatbot/ws");
let conversationId = null;

ws.onopen = () => {
  ws.send(JSON.stringify({
    type: "session.init",
    user_id: "8b3d3a5e-2c98-4e88-9b0c-8a3f0d6c9b21",
    conversation_id: null,
  }));
};

ws.onmessage = (ev) => {
  const evt = JSON.parse(ev.data);

  if (evt.type === "notification") {
    showToast(evt.payload); // { message, process, level }
    return;
  }

  if (evt.conversation_id) conversationId = evt.conversation_id;

  switch (evt.type) {
    case "session.started":
      renderGreeting(evt.payload.message);
      renderSelectionOptions(evt.payload.selection_options);
      break;

    case "assistant.message":
      renderAssistant(evt.payload.message);
      renderSelectionOptions(evt.payload.selection_options);
      renderChecklist(evt.payload.checklist);
      break;

    case "analysis.complete":
      renderPrefilledFields(evt.payload.prefilled_fields);
      renderChecklist(evt.payload.checklist);
      break;

    case "documents.ready":
      evt.payload.documents.forEach(renderDocument);
      break;

    case "error":
      showError(evt.payload.message);
      break;
  }
};

function sendMessage(text) {
  ws.send(JSON.stringify({
    type: "user.message",
    conversation_id: conversationId,
    content: text,
  }));
}

async function uploadFile(file) {
  const base64 = await fileToBase64(file); // strip "data:...;base64," prefix
  ws.send(JSON.stringify({
    type: "user.upload",
    conversation_id: conversationId,
    files: [{ file_name: file.name, content_base64: base64 }],
  }));
}
```

### 9.5 Python — WebSocket flow (`websockets`)

```python
import asyncio, base64, json
import websockets

async def run():
    async with websockets.connect("wss://api.example.com/chatbot/ws") as ws:
        await ws.send(json.dumps({
            "type": "session.init",
            "user_id": "8b3d3a5e-2c98-4e88-9b0c-8a3f0d6c9b21",
        }))

        conversation_id = None

        async for raw in ws:
            evt = json.loads(raw)
            conversation_id = evt.get("conversation_id") or conversation_id

            if evt["type"] == "session.started":
                await ws.send(json.dumps({
                    "type": "user.message",
                    "conversation_id": conversation_id,
                    "content": "Texas",
                }))

            elif evt["type"] == "assistant.message":
                print("assistant:", evt["payload"]["message"])

            elif evt["type"] == "documents.ready":
                for doc in evt["payload"]["documents"]:
                    if doc.get("download_url"):
                        print("download:", doc["download_url"])
                break

asyncio.run(run())
```

---

**Support:** For issues or schema changes, refer to the interactive docs at
[`/docs`](http://localhost:8000/docs) — the OpenAPI schema is generated
from the same Pydantic models cited in this document.
