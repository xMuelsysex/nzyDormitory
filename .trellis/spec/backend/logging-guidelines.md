# Logging Guidelines

> How logging is done in this project.

---

## Overview

No application logging library has been selected yet. Backend code must use structured, secret-safe logging suitable for an Ubuntu server deployment.

---

## Log Levels

- `debug`: local development details.
- `info`: server start, schedule updates, successful collection, and alert sent.
- `warn`: recoverable portal failures, skipped collection windows, and already-alerted low balance.
- `error`: failed collection after retries, persistence failures, email failures, or scheduler crashes.

---

## Structured Logging

Recommended fields:

```text
event
request_id or job_id
building
room
schedule_id
collected_at
error_code
```

Only include building and room when operationally useful.

---

## What to Log

- Server startup and shutdown.
- Schedule creation, update, disable, or skip.
- Successful electricity/balance sample persisted.
- Collection failure with redacted error category.
- Portal session expiration requiring user action.
- Alert threshold crossed and email delivery result.

---

## What NOT to Log

- Plaintext passwords.
- Session cookies or authorization headers.
- SMTP passwords or API keys.
- Full campus portal HTML pages.
- Full email content if it contains user-specific details.
- Stack traces in user-facing responses.

---

## Common Mistakes

- Using `print` as production logging.
- Logging generic messages without event names or error categories.
- Logging secrets while troubleshooting login or email delivery.
- Making background jobs invisible in logs.
