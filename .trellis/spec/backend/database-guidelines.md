# Database Guidelines

> Database patterns and conventions for this project.

---

## Current Project State

No application database layer exists yet. The planned product requires durable storage for room selections, schedules, electricity readings, alert settings, and alert delivery state.

---

## Data Ownership

Required logical entities:

```text
RoomSelection
ScheduleConfig
ElectricityReading
AlertConfig
AlertDeliveryState
CollectionFailureLog
```

Persist data needed across process restarts. Keep transient login/session material minimal and protected.

---

## Query Patterns

- Access storage through repository/data-access functions, not from route handlers.
- Use timestamp plus measured value as the primary chart data shape.
- Add date-range filtering before history can grow large.
- Store timestamps with timezone awareness.
- Treat schedule updates atomically so duplicate active jobs are not created.

---

## Migrations

- Use versioned migrations after a database tool is selected.
- Include upgrade paths and downgrades when supported.
- Do not edit already-applied migrations after they are shared.
- Never embed secrets or environment-specific values in migrations.

---

## Naming Conventions

- Prefer domain table names: `room_selections`, `schedule_configs`, `electricity_readings`, `alert_configs`.
- Use explicit timestamp columns: `created_at`, `updated_at`, `collected_at`, `last_alert_sent_at`.
- Use explicit numeric value names such as `balance_amount` or `electricity_value`.

---

## Common Mistakes

- Storing plaintext passwords or raw session cookies without a secured design.
- Storing chart data only in memory.
- Failing to deduplicate alert sends for the same below-threshold condition.
- Mixing server timezone, user schedule timezone, and collection timestamps without normalization.
