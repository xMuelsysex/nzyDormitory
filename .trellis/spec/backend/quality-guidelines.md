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
POST /session/login
POST /room-selection
PUT /schedule-config
GET /electricity-readings
PUT /alert-config
POST /collection/run-once
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
