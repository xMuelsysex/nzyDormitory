# Hook Guidelines

> How hooks are used in this project.

---

## Overview

No frontend framework has been selected yet. If React is used, hooks must isolate data loading and stateful UI logic from presentational components.

---

## Custom Hook Patterns

- Use hooks for reusable stateful flows such as reading history, schedule config, alert config, and room selection.
- Keep hooks small and domain-specific.
- Return explicit states: `data`, `isLoading`, `error`, and action functions.

---

## Data Fetching

- Centralize backend requests in `api/` and call them from hooks or page containers.
- Do not duplicate endpoint URLs across components.
- Handle session expiration as a user-action state requiring re-login.

---

## Naming Conventions

- Hook names must start with `use`.
- Use domain names: `useElectricityReadings`, `useScheduleConfig`, `useAlertSettings`, `useRoomSelection`.

---

## Common Mistakes

- Triggering uncontrolled polling from multiple components.
- Keeping backend request logic inside chart or form components.
- Ignoring cleanup for intervals or subscriptions.
- Treating empty readings as an error state.
