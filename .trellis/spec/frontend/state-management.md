# State Management

> How state is managed in this project.

---

## Overview

No global state library has been selected yet. Start with the simplest state model and promote state only when multiple features need it.

---

## State Categories

- Local UI state: form fields, validation messages, modal visibility.
- Server state: login/session status, room selection, schedule config, readings, alert config.
- Derived state: latest reading, below-threshold status, chart trend direction.
- URL state: optional filters such as date ranges if added later.

---

## When to Use Global State

Use global state only when the same state is consumed across multiple distant features, such as current room selection or session status. Otherwise keep state local or server-backed.

---

## Server State

- Treat persisted backend data as the source of truth.
- Refresh chart data after collection runs or on user request.
- Avoid duplicating server state in multiple unrelated local stores.
- Represent session expiration explicitly so the UI can ask the user to log in again.

---

## Common Mistakes

- Creating global state before it is needed.
- Storing secrets in browser state or local storage.
- Duplicating schedule config in multiple components without synchronization.
- Computing alert status from stale readings.
