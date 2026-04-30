# Quality Guidelines

> Code quality standards for frontend development.

---

## Overview

Frontend work must make the dorm electricity monitoring flow clear: login first, choose room, configure schedule, view trends, and configure alerts.

---

## Forbidden Patterns

- Components that mix login, schedule form, chart rendering, and alert settings in one file.
- Hard-coded backend URLs scattered across components.
- Passwords, cookies, or SMTP secrets stored in browser-visible code or storage.
- Charts without empty/error states.
- Threshold warnings communicated only by color.

---

## Required Patterns

- Provide clear empty states before the first reading exists.
- Validate forms before sending requests.
- Keep backend API calls centralized.
- Keep chart rendering separate from data fetching.
- Make user-action states explicit: login required, room required, schedule inactive, alert disabled.

---

## Testing Requirements

When frontend code exists, test form validation, room selection flow, schedule settings, chart empty state, chart rendering with readings, alert settings validation, and session-expired UI.

---

## Code Review Checklist

- Does the UI prevent scheduling before login and room selection?
- Are loading, empty, and error states handled?
- Are inputs labeled and validation messages accessible?
- Is chart data transformed in a testable layer?
- Are secrets absent from browser-visible code?
- Does mobile layout remain usable?

---

## Scenario: Dorm Electricity Monitoring UI Contract

### 1. Scope / Trigger

- Trigger: the UI spans login, room selection, scheduling, charting, and alert configuration.
- Applies when implementing frontend work for any `04-26-dorm-electricity-*` task.

### 2. Signatures

Frontend features must call these logical API operations through a centralized API layer:

```text
navigateToPortalLogin()
getStatus()
saveRoomSelection(selection) -> POST /api/room-selection
saveScheduleConfig(config) -> POST /api/schedule-config
listElectricityReadings() -> GET /api/readings
saveAlertConfig(config) -> POST /api/alert-config
runCollectionOnce() -> POST /api/collection/run-once
```

### 3. Contracts

```text
RoomSelection: building:string, room:string
ScheduleConfig: intervalSeconds:number, startTime:string, endTime:string, enabled:boolean
ElectricityReading: collectedAt:string, numericValue:number, unit?:string
AlertConfig: threshold:number, recipientEmail:string, enabled:boolean
```

UI states must be explicit:

```text
loginRequired
roomRequired
scheduleInactive
readingsEmpty
readingsLoaded
alertDisabled
alertActive
```

### 4. Validation & Error Matrix

| Condition | UI behavior |
|---|---|
| Not logged in | Block schedule/chart setup and show login action |
| No room selected | Block schedule setup and show room selector |
| Invalid interval/time window | Show field-level validation |
| No readings yet | Show empty chart state |
| Session expired | Show re-login action |
| Alert email invalid | Show field-level validation |

### 5. Good/Base/Bad Cases

- Good: user completes login, selects room, saves schedule, sees chart update, and configures alert email.
- Base: schedule saved but no reading has run; chart explains that data appears after collection.
- Bad: invalid email or time window; form blocks submit and explains the issue.

### 6. Tests Required

- Component: room selection form validation.
- Component: schedule form validation.
- Component: chart empty and loaded states.
- Component: alert settings invalid email and valid save flow.
- Integration/UI: login-required state blocks downstream configuration.

### 7. Wrong vs Correct

#### Wrong

```text
A single page component owns all forms, polling calls, chart transformation, and alert state.
```

#### Correct

```text
Page container -> feature components -> hooks -> centralized API layer
```

Components remain focused and receive clear props for data, loading, errors, and actions.

---

## Scenario: Proxied Campus Login UI Contract

### 1. Scope / Trigger

- Trigger: users must authenticate on the real campus login page, not in an app-owned credential form.
- Applies when editing login UI, session-expired UI, or `/portal/login` navigation.

### 2. Signatures

Frontend navigation:

```text
<a href="/portal/login">进入校园门户登录</a>
```

Backend browser flow:

```text
GET  /portal/login -> rewritten campus login HTML
POST /portal/login -> 303 /?login=success only after successful upstream login
```

### 3. Contracts

- The app UI must not render campus username/password inputs.
- The app UI must provide a clear button/link to `/portal/login`.
- After returning with `?login=success`, the UI may show a success message and refresh status.
- Failed login must remain on the proxied campus login page; the app UI should not display false success.
- The UI must not store passwords, cookies, or copied portal session data.

### 4. Validation & Error Matrix

| Condition | UI behavior |
|---|---|
| User has not logged in | Show `/portal/login` action |
| `?login=success` present | Show local success message and refresh status |
| Campus login fails | No app return; user stays on proxy login page |
| Later session expires | Show login action again |

### 5. Good/Base/Bad Cases

- Good: user clicks the login button, completes campus authentication, returns to the app, and continues room selection.
- Base: user mistypes credentials; they stay on the campus login page and retry.
- Bad: app asks for campus credentials directly or accepts pasted Cookie headers in the UI.

### 6. Tests Required

- Static/UI: home page contains link to `/portal/login` and no campus password field.
- JS syntax: app script handles `?login=success` without breaking initial status refresh.
- Manual authorized: completed campus login returns to app and status becomes authenticated.

### 7. Wrong vs Correct

#### Wrong

```html
<input name="username" />
<input name="password" type="password" />
```

#### Correct

```html
<a class="button-link" href="/portal/login">进入校园门户登录</a>
```
