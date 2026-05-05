# Error Handling

> How errors are handled in this project.

---

## Overview

Error handling standards are defined for the planned dorm electricity monitoring app in `task.md` and `.trellis/tasks/04-26-dorm-electricity-monitor/prd.md`.

---

## Error Types

Use explicit categories:

- `ValidationError`: invalid schedule, threshold, email, building, or room.
- `AuthenticationError`: campus login failed or session expired.
- `PortalFetchError`: electricity portal could not be reached or returned an unexpected response.
- `PortalParseError`: remote response was received but electricity data could not be extracted.
- `PersistenceError`: database read/write failed.
- `EmailDeliveryError`: alert email could not be sent.
- `SchedulerError`: job registration, cancellation, or execution failed.

---

## Error Handling Patterns

- Validate user input at the API boundary.
- Convert low-level exceptions into domain-specific errors before crossing service boundaries.
- Scheduler jobs must catch and record failures so future runs continue.
- Log safe diagnostic details and surface user-safe messages.
- Keep retries explicit and bounded.

---

## API Error Responses

Use a stable response shape once APIs exist:

```json
{
  "error": {
    "code": "PORTAL_SESSION_EXPIRED",
    "message": "The campus portal session has expired. Please log in again."
  }
}
```

Never include passwords, cookies, SMTP credentials, raw stack traces, or full remote HTML.

---

## Common Mistakes

- Returning raw exceptions to the browser.
- Logging credentials, session cookies, or SMTP secrets.
- Treating a changed portal HTML structure as a generic network failure.
- Letting email alert failures block storage of successful electricity readings.
