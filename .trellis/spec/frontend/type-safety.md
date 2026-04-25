# Type Safety

> Type safety patterns in this project.

---

## Overview

No frontend language/tooling has been selected yet. If TypeScript is used, domain contracts must be explicit and shared consistently between API calls, forms, and chart components.

---

## Type Organization

Define domain types for:

```ts
type RoomSelection = { building: string; room: string }
type ScheduleConfig = { intervalSeconds: number; startTime: string; endTime: string }
type ElectricityReading = { collectedAt: string; value: number; unit?: string }
type AlertConfig = { threshold: number; recipientEmail: string; enabled: boolean }
```

Keep feature-local types near the feature until they are reused.

---

## Validation

- Runtime validation is required for user input and API responses once a validation library is selected.
- Validate schedule order, positive interval, numeric threshold, and email format.
- Treat remote portal data as untrusted; validate before charting or alert evaluation.

---

## Common Patterns

- Prefer inferred types from validation schemas when available.
- Use discriminated unions for async state when helpful.
- Use explicit nullable states for missing readings or unauthenticated sessions.

---

## Forbidden Patterns

- Do not use `any` for API responses or chart data.
- Do not assert types for unvalidated remote portal data.
- Do not use ambiguous fields like `value` without domain context at API boundaries.
- Do not let frontend and backend contracts drift without updating specs.
