# Quality Guidelines

> Code quality standards for backend development.

---

## Overview

Backend work must prioritize security, reliability, and separation of concerns because the app handles login sessions, scheduled jobs, persisted readings, and email delivery.

---

## Forbidden Patterns

- Hard-coded credentials, SMTP secrets, usernames/passwords, thresholds, or room identifiers.
- One-file implementations combining routing, scraping, parsing, scheduling, database writes, and email.
- Unbounded retries or loops in scheduled jobs.
- Duplicate background jobs after schedule updates.
- Raw exception responses to the browser.
- Tests covering only happy paths for parser/scheduler behavior.

---

## Required Patterns

- Keep portal integration behind a dedicated client/parser boundary.
- Keep scheduling separate from collection and persistence.
- Validate interval, start time, end time, threshold, and email address.
- Redact secrets in logs and errors.
- Persist readings before evaluating alert delivery.
- Design for Ubuntu: environment-based config, documented dependencies, and restart-safe persistence.

---

## Testing Requirements

When application code exists, test schedule validation, parser behavior, reading persistence, portal failure handling, alert cooldown, and invalid API inputs.

---

## Code Review Checklist

- Are secrets kept out of source, logs, and committed files?
- Can a failed scheduled run recover on the next run?
- Is portal integration isolated from services and routes?
- Are timestamps and schedule windows handled consistently?
- Does alert logic avoid email spam?
- Are backend changes reflected in frontend contracts and Trellis PRDs?

---

## Scenario: Dorm Electricity Monitoring Cross-Layer Contract

### 1. Scope / Trigger

- Trigger: the planned feature spans room selection UI, schedule API, background collection, database persistence, chart data, and email alerts.
- Applies when implementing any `04-26-dorm-electricity-*` task.

### 2. Signatures

Backend endpoints or equivalent command handlers must expose these logical operations:

```text
GET  /api/status
GET  /api/readings
POST /api/room-selection
POST /api/schedule-config
POST /api/alert-config
POST /api/collection/run-once
```

Database or repository operations must support:

```text
saveRoomSelection(selection)
saveScheduleConfig(config)
insertElectricityReading(reading)
listElectricityReadings(filter)
saveAlertConfig(config)
markAlertSent(alertState)
recordCollectionFailure(failure)
```

### 3. Contracts

Required request/response fields:

```text
RoomSelection: building:string, room:string
ScheduleConfig: intervalSeconds:number>0, startTime:string, endTime:string, enabled:boolean
ElectricityReading: collectedAt:ISO datetime, building:string, room:string, numericValue:number, unit?:string
AlertConfig: threshold:number, recipientEmail:string, enabled:boolean, cooldownSeconds?:number
```

Environment keys must be read from deployment configuration, not source code:

```text
SMTP_HOST
SMTP_PORT
SMTP_USERNAME
SMTP_PASSWORD
SMTP_FROM
APP_TIMEZONE
CAMPUS_LOGIN_URL
CAMPUS_ELECTRICITY_URL
```

### 4. Validation & Error Matrix

| Condition | Error |
|---|---|
| Missing login/session | `AuthenticationError` |
| Empty building or room | `ValidationError` |
| `intervalSeconds <= 0` | `ValidationError` |
| Start time after end time | `ValidationError` |
| Invalid email address | `ValidationError` |
| Portal unavailable | `PortalFetchError` |
| Portal response shape changed | `PortalParseError` |
| SMTP delivery failed | `EmailDeliveryError` |

### 5. Good/Base/Bad Cases

- Good: user logs in, selects room, sets schedule, collection persists readings, chart displays readings, alert sends once below threshold.
- Base: no readings exist yet; chart shows an empty state and scheduler waits for the next active window.
- Bad: portal session expires; collection stops retry spam and asks the user to log in again.

### 6. Tests Required

- Unit: validate schedule interval, time range, threshold, and email format.
- Unit: parse representative portal response into `ElectricityReading`.
- Integration: run one collection cycle and assert reading persistence.
- Integration: below-threshold reading sends one email and records cooldown state.
- UI: chart empty state and chart-with-readings state.

### 7. Wrong vs Correct

#### Wrong

```text
Route handler logs in, scrapes HTML, writes database rows, sends email, and returns chart data in one function.
```

#### Correct

```text
Route -> Service -> Portal Client/Parser -> Repository -> Alert Evaluator -> Email Transport
```

Each boundary receives validated data and returns a typed/domain-specific result or error.

---

## Scenario: Campus Portal Proxy Login Contract

### 1. Scope / Trigger

- Trigger: users must authenticate on the real campus `Default.aspx` page instead of submitting credentials through the app UI.
- Applies when editing `CampusPortalClient`, `/portal/login`, `read_form_body`, or authenticated collection session handling.

### 2. Signatures

Backend browser flow:

```text
GET  /portal/login
  response: rewritten campus login HTML

POST /portal/login
  request: application/x-www-form-urlencoded campus form fields + __portal_action
  success: 303 Location: /?login=success
  failure: 200 rewritten campus login HTML
```

Integration operations:

```python
CampusPortalClient.load_login_page() -> str
CampusPortalClient.submit_login_page(fields: dict[str, str]) -> tuple[bool, str]
CampusPortalClient.fetch_reading(selection: RoomSelection) -> ElectricityReading
```

### 3. Contracts

- `GET /portal/login` must fetch `CAMPUS_LOGIN_URL` through the backend `CookieJar` and rewrite the selected campus `<form>` to submit to local `POST /portal/login`.
- The rewritten form must include `__portal_action` with the original absolute campus form action so the backend can submit to the correct upstream endpoint.
- The proxy must not inject custom banners, headers, scripts, or styling into campus HTML; users should see the campus page as-is except for rewritten URLs.
- Relative and absolute campus `src`/`href` attributes must be rewritten to `/portal/proxy?url=<encoded absolute campus URL>`.
- CSS `url(...)` references returned through the proxy must also be rewritten to `/portal/proxy`.
- `/portal/proxy` must only fetch URLs whose host matches `CAMPUS_LOGIN_URL`; it must not become an open proxy.
- Dynamic portal paths under `/portal/<path>` that are requested by campus JavaScript must map to the same campus host through the same safe resource fetcher.
- HTML and CSS returned through `/portal/proxy` or `/portal/<path>` must be recursively rewritten; binary assets must preserve the upstream content type.
- `POST /portal/login` must submit all received form fields except `__portal_action` to the upstream campus action using the same `CookieJar`.
- `CAMPUS_LOGIN_URL` defaults to the WebVPN login/auth page. `CAMPUS_ELECTRICITY_URL` must default to the same WebVPN gateway path for `/Web/Student/FeeElect.aspx`, the self-service electricity page, not the login page or authenticated portal shell.
- `fetch_reading()` must query FeeElect by first `GET`ing the electricity page with the authenticated `CookieJar`, preserving WebForms hidden fields such as `__VIEWSTATE` and `__EVENTVALIDATION`, then `POST`ing back with `ZoneID`, `txtHouse`, `txtRoom`, `btnQuery=查询电量`, and `FeeAmtTxt` defaulting to `10` when blank.
- C-zone room selection maps `C20`/`c20` to `ZoneID=1` and `txtHouse=20`; raw numeric building input such as `20` is also treated as C-zone house number input for the current dorm workflow.
- `fetch_reading()` must never submit `btkOK` when reading electricity data because that starts the purchase flow.
- FeeElect query results must parse `span#lblRoomMoney` values such as `20.93 元` into `ElectricityReading.numeric_value=20.93` and `unit="元"`.
- Real source portal research recorded in `.trellis/tasks/04-26-dorm-electricity-portal-research/research-findings.jsonl` verifies `Default.aspx` fields: `__VIEWSTATE`, `__EVENTVALIDATION`, `UserName`, `UserPwd`, `InputCode`, and `imgBtn`.
- The source portal has captcha (`InputCode`); invalid credentials/captcha return HTTP `200` with the login form still present and no redirect.
- `submit_login_page()` must never treat HTTP `200` or absence of generic failure text as success.
- Login success requires login/captcha fields to disappear and an authenticated-page signal such as `管理中心`, `安全退出`, `退出登录`, `退出`, `注销`, `自助购电`, `业务办理`, or `服务大厅` to be present.
- Login-page detection must key on structural login form signals such as `UserPwd`, `InputCode`, password inputs, or `__EVENTVALIDATION` paired with login controls; do not reject authenticated pages just because visible navigation text contains generic password-management words such as `密码` or `修改密码`. `__EVENTVALIDATION` alone is not enough because authenticated WebForms pages such as the electricity page also include it.
- A successful login redirects back to the app with `303 /?login=success`; failed login returns the rewritten campus login page and must not redirect back to the app.
- Passwords and cookies must never be logged, persisted, returned as JSON, or rendered by the app shell.
- `fetch_reading()` must classify the returned portal HTML with `diagnose_portal_response()` before parsing values; do not report a generic parse failure until login/home/error pages are ruled out.
- `diagnose_portal_response()` must never include raw HTML, cookies, passwords, or full upstream responses in user-facing errors.

### 4. Validation & Error Matrix

| Condition | Error/Response |
|---|---|
| `CAMPUS_LOGIN_URL` unavailable | `PortalFetchError` |
| Campus form has hidden WebForms fields | Preserve and relay them through `/portal/login` |
| Campus page has relative `src`/`href` resources | Rewrite to same-host `/portal/proxy` URL |
| Campus CSS has `url(...)` assets | Rewrite to same-host `/portal/proxy` URL |
| Dynamic campus JavaScript requests `/portal/web/...` assets | Fetch same-host campus resource and preserve content type |
| `/portal/proxy` targets a different host | `PortalFetchError` |
| Invalid credentials or captcha returns HTTP `200` with login fields still present | `200` rewritten login page and `authenticated=False` |
| Campus login succeeds with authenticated-page signal | `303 Location: /?login=success` and `authenticated=True` |
| Electricity page renders login page after prior auth | `AuthenticationError` and `authenticated=False` |
| Electricity URL returns portal home page | `PortalParseError` saying login likely succeeded but electricity URL/navigation target is wrong |
| Electricity URL returns access denied page | `PortalParseError` with access-denied diagnosis |
| Electricity URL returns portal error page | `PortalParseError` with portal-error diagnosis |
| Electricity HTML shape changes after electricity page is confirmed | `PortalParseError` |

### 5. Good/Base/Bad Cases

- Good: user clicks the app login button, sees the actual campus login page with intact images/styles/scripts through same-host proxy URLs, completes authentication, returns to the app only after upstream login succeeds, and collection errors distinguish login page, portal home page, access denial, portal error, and confirmed electricity parse failure.
- Base: login fails or captcha is still required; user stays on the proxied campus login page to retry with all resources still loading.
- Bad: the app asks for campus username/password in its own form, injects custom UI into the campus page, breaks portal images/styles by failing to proxy resources, or redirects back to the app after a failed campus login.

### 6. Tests Required

- Unit: `rewrite_login_page()` rewrites the first login form to `action="/portal/login"`, injects `__portal_action`, rewrites `src`/`href` resources to `/portal/proxy`, and does not inject custom banner text.
- Unit: `rewrite_css_urls()` rewrites CSS `url(...)` assets to same-host `/portal/proxy` URLs.
- Unit: `proxy_target_from_path()` accepts same-host campus URLs and rejects different hosts.
- Unit: `diagnose_portal_response()` distinguishes login page, portal home page, and electricity-related page before parsing.
- Unit: `parse_electricity_value()` still raises `PortalParseError` for unknown confirmed electricity shapes.
- Unit: FeeElect building mapping converts C-zone and numeric building input to `ZoneID`/`txtHouse`.
- Unit: FeeElect POST field construction preserves WebForms hidden fields, submits `btnQuery`, and omits `btkOK`.
- Unit: `fetch_reading()` performs GET then POST against FeeElect and parses `span#lblRoomMoney`.
- Unit/API smoke: `GET /portal/login` returns HTML from `load_login_page()`.
- Unit: real-source login form detector matches `UserName`, `UserPwd`, `InputCode`, `__VIEWSTATE`, and `__EVENTVALIDATION` semantics without treating authenticated password-management navigation as a login form.
- Unit/live-safe: invalid source portal login attempt must return `success=False`, `authenticated=False`, login form still present, and authenticated signal absent.
- Unit/API smoke: successful `POST /portal/login` responds with `303 /?login=success`.
- Browser: Playwright must load `/portal/login` on a fresh process and confirm portal assets, including dynamic `/portal/web/...` requests, return `200` except unrelated browser `favicon.ico`.
- Manual authorized integration: on campus network, click login, authenticate on the proxied campus page, return to the app, then run one collection.

### 7. Wrong vs Correct

#### Wrong

```text
App page -> username/password form -> POST /api/login -> guess campus fields -> return success/failure JSON
```

#### Correct

```text
App page -> GET /portal/login -> exact proxied campus login page with same-host asset proxy -> POST /portal/login -> 303 /?login=success only after upstream success
```

---

## Scenario: Static Frontend Serving Contract

### 1. Scope / Trigger

- Trigger: backend serves the browser UI directly from the repository frontend source tree.
- Applies when editing `backend/app/main.py` static file routing, frontend file locations, or deployment packaging.

### 2. Signatures

Backend static handler behavior:

```text
GET /              -> frontend/src/index.html
GET /<asset-path>  -> frontend/src/<asset-path>
```

Implementation path anchor:

```python
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "src"
```

### 3. Contracts

- Static root must resolve inside this repository, not the parent directory of the repository.
- `/` and empty paths must map to `index.html`.
- Asset paths must be resolved and constrained under `FRONTEND_DIR` before reading.
- API routes keep the `/api/*` prefix and must not be shadowed by static serving.

### 4. Validation & Error Matrix

| Condition | Response |
|---|---|
| `frontend/src/index.html` exists and `GET /` is requested | `200 text/html` |
| Asset exists under `frontend/src` | `200` with guessed MIME type |
| Asset is missing | `404 {"error":{"code":"NOT_FOUND","message":"File not found"}}` |
| Path resolves outside `frontend/src` | same `NOT_FOUND` response |

### 5. Good/Base/Bad Cases

- Good: running the backend from the repository root returns the UI at `http://localhost:8000/` and assets such as `/app.js` return `200`.
- Base: a missing asset returns stable JSON `NOT_FOUND` without exposing filesystem paths.
- Bad: using `Path(__file__).resolve().parents[2].parent` points at the parent repository directory and makes `/` return `File not found`.

### 6. Tests Required

- Unit: assert `FRONTEND_DIR / "index.html"` exists.
- Unit: call the static handler with `/` and assert status `200` plus HTML body content.
- Unit: call the static handler with a missing asset and assert the stable `NOT_FOUND` JSON response.
- Smoke: start a temporary HTTP server and assert `/` and `/app.js` both return `200`.

### 7. Wrong vs Correct

#### Wrong

```python
FRONTEND_DIR = Path(__file__).resolve().parents[2].parent / "frontend" / "src"
```

#### Correct

```python
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "src"
```
