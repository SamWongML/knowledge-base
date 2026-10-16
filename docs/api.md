# HTTP API contract

Base URL: `http://127.0.0.1:8787` (behind Caddy in production). All JSON. Pydantic models live in
`backend/src/kb/schemas.py` and are the source of truth; FastAPI publishes them at `/openapi.json`.
Write endpoints require `Authorization: Bearer $KB_API_TOKEN` when the server has a token set.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/health` | – | `Health` (db, embedder, reranker, llm) |
| GET | `/api/stats` | – | `Stats` |
| GET | `/api/collections` | – | `Collection[]` |
| POST | `/api/collections` | `CollectionIn` | `Collection` (201) |
| DELETE | `/api/collections/{slug}` | – | 204 |
| GET | `/api/documents?collection=&status=&tag=&q=&limit=&offset=` | – | `Page[DocumentSummary]` (`q` = title prefix/substring) |
| POST | `/api/documents` | `DocumentIn` | `IngestAccepted` (202) |
| POST | `/api/documents/upload` | multipart: `files[]`, `collection`, `tags` (comma sep) | `IngestAccepted[]` (202) |
| GET | `/api/documents/{id}` | – | `Document` |
| PATCH | `/api/documents/{id}` | `DocumentPatch` | `Document` |
| DELETE | `/api/documents/{id}` | – | 204 |
| GET | `/api/documents/{id}/chunks` | – | `{id, ord, heading_path, content, token_count}[]` |
| POST | `/api/documents/{id}/reindex` | – | `IngestAccepted` (202) |
| POST | `/api/search` | `SearchRequest` | `SearchResponse` |
| POST | `/api/ask` | `AskRequest` | `AskResponse` |
| POST | `/api/ask/stream` | `AskRequest` | SSE: `event: step` data=`AgentStep` … then `event: done` data=`AskResponse`, or `event: error` |
| GET | `/api/events` | – | SSE: `event: document.status` data=`{id, collection_id, status, title, error, chunk_count}`; `event: ping` every 15s |
| GET | `/api/queries/recent?limit=` | – | `{query, mode, created_at}[]` |
| * | `/mcp` | MCP Streamable HTTP | MCP tools `kb_search`, `kb_ask`, `kb_read`, `kb_list`, `kb_add`, `kb_update`, `kb_delete`, `kb_collections` |

Notes
- Ingestion is asynchronous: create/upload returns 202 with `status: pending`; watch `/api/events`.
- Identical content in the same collection is deduplicated (`duplicate: true`, existing id returned).
- `SearchHit.snippet` contains `<mark>` tags only; everything else is escaped.
- Errors use FastAPI's `{"detail": ...}` shape with conventional status codes.
