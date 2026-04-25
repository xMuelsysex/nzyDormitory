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
login(credentials)
saveRoomSelection(selection)
saveScheduleConfig(config)
listElectricityReadings(filter)
saveAlertConfig(config)
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
