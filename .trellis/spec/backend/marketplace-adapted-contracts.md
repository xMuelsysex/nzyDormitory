# Backend Contracts Adapted From Marketplace Specs

## Purpose

Use this spec for backend work that touches dorm electricity portal integration, login/session handling, room selection, scheduled collection, alerts, APIs, persistence, or server-side data shaping.

This file adapts useful ideas from the marketplace `nextjs-fullstack` backend/shared/guides specs without requiring this project to use Next.js, oRPC, Drizzle, or PostgreSQL.

## Boundary Contract Rule

Every backend entry point must have an explicit contract:

| Entry point type | Contract to define | Required checks |
| --- | --- | --- |
| HTTP/API handler | Request fields, response fields, status/error shape | Auth context, input validation, serialization, idempotency where relevant |
| Service function | Parameter object, return object, thrown/returned error categories | Null/empty handling, timeout behavior, retry behavior |
| Scheduler/collector job | Trigger source, frequency, dedupe key, write target | Locking/dedupe, portal timeout, partial failure, observability |
| Email alert path | Recipient source, threshold source, template inputs | Duplicate suppression, sensitive data redaction, send failure handling |
| Portal adapter | Login/session state, room identifier, balance payload | Credential secrecy, HTML/API drift handling, explicit parse errors |

## Payload Shape Requirements

- Prefer one request/response DTO per boundary and reuse it across service, API, and tests.
- Do not let UI-only labels become backend enum values. Backend values should be stable identifiers.
- Date/time fields must specify timezone and serialization shape.
- Numeric money/balance fields must specify units and precision.
- Optional fields must distinguish:
  - Not configured
  - Portal returned empty data
  - Collection failed
  - User has no permission

## Error Taxonomy

Backend code should classify errors before they cross the boundary:

| Category | Meaning | Caller behavior |
| --- | --- | --- |
| `validation` | Caller sent invalid or incomplete data | Show actionable validation message; do not retry automatically |
| `auth` | Login/session/permission problem | Ask user to reauthenticate or check room binding |
| `portal_unavailable` | Campus portal request failed or timed out | Retry with backoff if safe; preserve last known value |
| `portal_parse` | Portal shape changed or data is malformed | Log sanitized evidence and surface maintenance state |
| `rate_limited` | Upstream or local throttling | Delay retry; do not fan out more requests |
| `internal` | Unexpected local bug | Log with request/job context; show generic message |

Do not log raw passwords, tokens, cookies, room credentials, or full portal responses that may contain personal data.

## Persistence And Freshness

For dorm electricity readings, persist enough metadata to explain the value later:

| Field class | Required detail |
| --- | --- |
| Reading identity | Dorm/room binding id, reading source, collected-at timestamp |
| Balance value | Numeric value, unit, precision rule, raw-to-normalized conversion point |
| Freshness | Last successful collection, last attempted collection, stale threshold |
| Failure context | Sanitized error category, retryable flag, attempt count when available |

When updating a stored reading, decide whether the operation is append-only history, latest snapshot replacement, or both. Trend views and alert dedupe usually need history; dashboard cards usually need latest snapshot.

## Scheduled Collector Rules

- Make collection idempotent for the same room and collection window.
- Prevent overlapping runs for the same room/account.
- Use explicit per-room failure isolation so one failing room does not block all rooms.
- Treat portal calls as unreliable network dependencies with timeouts.
- Alert only after a successful fresh reading unless the feature explicitly supports stale-data alerts.
- Record enough job context for diagnosis without exposing credentials.

## Scenario: Campus Portal Session Expiry

### 1. Scope / Trigger
- Trigger: any backend change touching campus portal login/session handling, scheduled collection, or `/api/status` authentication fields.

### 2. Signatures
- `CampusPortalClient.authentication_status: str` uses stable values: `authenticated`, `unauthenticated`, `session_expired`.
- `GET /api/status` returns both `authenticated: bool` and `authenticationStatus: str`.
- `POST /api/collection/run-once` may fail with `SESSION_EXPIRED` and HTTP 401.
- Scheduled collection records failures through `Repository.record_failure(failed_at, error_code, message)`.

### 3. Contracts
- `authenticated` remains the backward-compatible boolean gate for controls.
- `authenticationStatus` is the actionable state for UI copy and retry decisions.
- A portal response diagnosed as `login_page` during electricity collection means the previous session expired; do not collapse it into a generic portal parse/fetch error.
- Successful login or cookie import must set `authenticated = True` and `authentication_status = "authenticated"`.

### 4. Validation & Error Matrix
- No room selection -> `SCHEDULER_ERROR`.
- Never logged in -> `AUTHENTICATION_ERROR`.
- Previously logged in, portal returns login page during collection -> `SESSION_EXPIRED`.
- Portal unreachable -> `PORTAL_FETCH_ERROR`.
- Portal page shape changed -> `PORTAL_PARSE_ERROR`.

### 5. Good/Base/Bad Cases
- Good: session expires between 3600-second scheduler ticks; failure row uses `SESSION_EXPIRED`; timer schedules the next run.
- Base: user reauthenticates through existing login flow; next manual or scheduled collection succeeds without changing schedule config.
- Bad: code sets only `authenticated = False` on expiry and loses the reason, leaving UI/status unable to tell the user to re-login.

### 6. Tests Required
- Scheduler test: expired session failure is recorded with `SESSION_EXPIRED` and rescheduling still happens.
- Portal adapter test: login-page diagnosis during `fetch_reading()` sets `authentication_status = "session_expired"`.
- Service/API test: status payload exposes `authenticationStatus` separately from `authenticated`.
- Recovery test: successful login after expiry restores `authentication_status = "authenticated"` and collection can run again.

### 7. Wrong vs Correct
#### Wrong
```python
self.authenticated = False
raise AuthenticationError("Campus portal login is required before collection.")
```

#### Correct
```python
self.authenticated = False
self.authentication_status = "session_expired"
raise SessionExpiredError("Campus portal session expired. Please log in again.")
```

## API/Service Quality Gate

Before finishing backend changes:

- [ ] Existing service/adapter pattern was searched and reused where possible.
- [ ] Input and output contracts are named and tested.
- [ ] Auth/session assumptions are explicit.
- [ ] Portal/network failures have deterministic categories.
- [ ] Logs are structured and redact sensitive fields.
- [ ] Cross-layer consumers know stale, loading, empty, and failed states.
- [ ] Tests cover success, validation failure, auth/session failure, and portal failure paths when the code owns those behaviors.

## Anti-Patterns

- Creating a second DTO that mirrors an existing backend contract.
- Returning raw upstream portal payloads directly to the frontend.
- Treating a parse failure as zero balance.
- Sending low-balance email from stale data without clear product intent.
- Adding a scheduler without lock/dedupe behavior.
- Catching all errors and returning a generic success-shaped response.

