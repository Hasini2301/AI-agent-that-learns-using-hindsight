# Architecture and operational notes

## Implementation approach

SignalOps implements one compact business workflow from the supplied hackathon brief: incident-response memory. Python's standard-library threaded HTTP server provides a small, auditable JSON API and serves static assets. `memory.py` isolates memory operations behind a provider boundary; the included deterministic local provider persists to a JSON file using atomic replacement. The frontend is dependency-free and calls only same-origin APIs. The separation makes the hackathon demo runnable offline while allowing a Hindsight-specific provider to be introduced without coupling credentials or SDK assumptions to UI code.

## Main flow

`POST /api/analyze` validates service and incident context, asks the provider for similar incidents, and returns a structured brief: confidence, recommended next steps, and recalled evidence. Recommendations explicitly instruct responders to validate precedent against live signals and favor reversible mitigations; no infrastructure changes are executed. `POST /api/incidents` records a resolution, atomically persists it, and makes it searchable. `GET /api/incidents` and `GET /api/search?q=...` render recent or matching records.

The shipped local provider uses a transparent lexical overlap scorer and seeded synthetic incidents. It is a functional demo, not semantic retrieval. The optional `HindsightMemoryProvider` in `hindsight_provider.py` uses the official documented REST endpoints: `POST /v1/default/banks/{bank_id}/memories` for retain and `/recall` for recall. Its parser maps `results[].text` into evidence, stores incident details in a single recallable memory, and labels the active provider in the UI. Configure `SIGNALOPS_MEMORY_PROVIDER=hindsight`, `HINDSIGHT_API_URL`, `HINDSIGHT_API_KEY`, and `HINDSIGHT_BANK_ID`; see `.env.example`. The API key stays server-side. Provider requests use a bounded timeout, validate the remote response shape, map upstream errors to sanitized user-facing messages, and do not retry write requests because the remote commit state can be ambiguous after a timeout. Primary references: [Hindsight API integration](https://docs.hindsight.vectorize.io/api-integration/) and [Python client quick start](https://hindsight.vectorize.io/sdks/python).

## Validation and security

- Request bodies are capped at 16 KiB; JSON object shape and all text fields are validated for type, trimming, required length, max length, and unsupported control characters.
- Static paths are resolved and constrained under `web/`; JSON is never interpreted as HTML. Frontend text is escaped before inserting markup.
- Same-origin API calls only, no client-side API keys, no third-party dependencies, `nosniff`, a restrictive CSP, and `no-store` API responses. The demo intentionally omits `frame-ancestors` so the embedded preview can render; set an explicit trusted-origin allowlist when deploying outside this preview.
- Persistent updates use a process lock and write-temp-then-replace to prevent partial-file writes within one server process. Do not expose this unauthenticated demo server to the public internet.
- Do not enter real customer data, credentials, tokens, or sensitive production incident material in this local demo.

## Error catalog

| Category / code | Trigger | Handling |
|---|---|---|
| `EMPTY_BODY` (400) | Missing body | Clear API response; user can retry with input. |
| `INVALID_LENGTH` (400) | Malformed Content-Length | Reject before reading/parsing. |
| `PAYLOAD_TOO_LARGE` (413) | Body exceeds 16 KiB | Reject without processing. |
| `INVALID_JSON` (400) | Malformed JSON or invalid encoding | Return safe validation message. |
| `INVALID_BODY` (400) | JSON is not an object | Reject; no state mutation. |
| `INVALID_FIELD` (400) | Missing, wrong type, too short/long, or control characters | Field validation message; no state mutation. |
| `NOT_FOUND` (404) | Unknown endpoint/static file | Safe not-found response. |
| `STORAGE_UNAVAILABLE` (503) | Disk permissions, full disk, or other write/read I/O failure | In local mode save is reported as not saved; diagnostic logged server-side. In Hindsight mode a remote retain may already have succeeded before the local index failed; the API returns the saved incident with an explicit `index_warning`, and the UI tells the operator not to resubmit. |
| `HINDSIGHT_AUTH_FAILED` / `HINDSIGHT_CREDITS_EXHAUSTED` / `HINDSIGHT_RESOURCE_NOT_FOUND` (503) | Invalid/revoked key, exhausted account credits, wrong bank or endpoint | Actionable sanitized message; check credentials, account, bank, and base URL. No secrets or remote bodies exposed. |
| `HINDSIGHT_RATE_LIMITED` (503) | Hindsight returns HTTP 429 | Ask operator to wait and retry; no automatic retry. |
| `HINDSIGHT_REJECTED_REQUEST` (502) | Hindsight returns HTTP 400 | Treat as configuration/API contract issue; inspect server logs/configuration, not customer data. |
| `HINDSIGHT_UPSTREAM_ERROR` (502) | Other upstream HTTP error | Generic temporary-unavailable message; do not leak response body. |
| `HINDSIGHT_NETWORK_ERROR` (503) | DNS, connection, TLS, or network failure | Safe error and manual retry guidance. |
| `HINDSIGHT_TIMEOUT` (504) | Hindsight exceeds configured 1–30 second timeout | Explicitly warn that retain may have completed; check memory before saving again. |
| `HINDSIGHT_INVALID_RESPONSE` / `HINDSIGHT_RESPONSE_TOO_LARGE` (502) | Non-JSON, malformed shape, missing results array, or >1 MiB upstream response | Reject provider result rather than silently treating it as empty memory. |
| Invalid Hindsight configuration (startup exit 2) | Unsupported mode, invalid URL/bank/timeout, insecure HTTP without opt-in | Fail startup with a precise config message; no fallback that could hide a production misconfiguration. |
| Corrupt memory JSON (surfaced as 503) | Invalid JSON or wrong top-level shape | No overwrite; fail explicitly, preserve file for operator recovery. |
| `INTERNAL_ERROR` (500) | Unexpected backend/provider exception | Generic response, stack trace only in server log; retry may be appropriate. |
| Browser network error | Server unreachable / disconnected | Inline retry guidance; existing UI remains available. |
| Browser timeout (12s) | API does not respond in time | Abort request and offer retry. Server-side operation could have completed; inspect memory before repeating a save. |
| Unexpected non-JSON response | Proxy/server misconfiguration | UI reports unreadable response. |
| Empty analysis input | Fewer than 8 chars | Client blocks request and focuses field. |
| Empty search | No matching incidents | Friendly empty state; clear search restores list. |
| Concurrent requests | Multiple threads in one process | RLock guards file operations; atomic replace prevents partial writes. |
| Port conflict / bind permission | Server startup failure | Logged exception, nonzero process exit. |
| Resource exhaustion | Disk full / memory pressure / thread overload | Disk failures mapped to storage error; no automatic retry. Configure limits and process supervision for deployment. |

## Smoke-test checklist

Verification completed: 4 automated `unittest` cases passed for Hindsight recall mapping, remote retain + local indexing, malformed provider responses, and sanitized authentication errors. HTTP smoke passed in local mode for homepage/CSP, health, journal, and analysis. A separate end-to-end run against a local mock implementing the documented Hindsight REST shapes passed Hindsight-mode health, API recall/evidence, save/retain + journal index, and upstream-auth-error mapping. This exercises the adapter contract but is not a live Hindsight Cloud test; no account credentials or live instance were provided. Browser visual/accessibility testing is not included.

## Production readiness gap / recommended follow-up

This is a high-quality hackathon prototype, not safe as-is for multi-user production. Add authentication and tenant-level authorization, CSRF protection if cookie auth is introduced, encrypted database storage with backups/retention controls, audit logging, rate limiting, request IDs, metrics/tracing, provider timeouts/circuit breaker/retry policy, schema migrations, semantic retrieval and provenance from Hindsight, privacy deletion/export controls, and a proper WSGI/ASGI deployment behind TLS. Write unit and integration tests in CI; add browser automation and accessibility checks. Protect operational advice with human review and service-specific runbooks.
