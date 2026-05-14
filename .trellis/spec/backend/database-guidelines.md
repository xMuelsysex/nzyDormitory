# Database Guidelines

> Database patterns and conventions for this project.

---

## Current Project State

The application uses SQLite through `backend/app/persistence/repository.py` for the lightweight single-node deployment path. Keep this path simple and deterministic; consider PostgreSQL only when multi-instance deployment or heavier concurrent writes become a concrete requirement.

---

## Data Ownership

Required logical entities:

```text
Room
RoomSelection
ScheduleConfig
ElectricityReading
CollectionRun
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

## Scenario: SQLite Collection History Foundation

### 1. Scope / Trigger

- Trigger: backend changes that initialize or migrate room bindings, electricity reading history, scheduled collection run status, or alert dedupe persistence.

### 2. Signatures

- `Repository.initialize() -> None` must be idempotent and safe to run on startup.
- `Repository.save_room_selection(selection: RoomSelection, updated_at: str) -> None` stores the legacy single selected room and upserts the canonical `rooms` row.
- `Repository.insert_reading(reading: ElectricityReading, collection_window_start: str | None = None, collection_run_id: int | None = None) -> bool` returns whether a new history row was inserted.
- `Repository.get_latest_successful_reading() -> dict[str, Any] | None` returns the latest successful reading payload for current-balance consumers.
- `Repository.list_readings(limit: int = 200) -> list[dict[str, Any]]` returns successful reading history in ascending chart order.
- `Repository.start_collection_run(selection: RoomSelection, started_at: str, collection_window_start: str) -> int` creates a `running` run row.
- `Repository.finish_collection_run(run_id: int, finished_at: str, status: str, reading_inserted: bool = False, error_code: str | None = None, message: str | None = None) -> None` finalizes a run row.
- `Repository.get_latest_collection_run() -> dict[str, Any] | None` returns the last attempted collection status for diagnostics.

### 3. Contracts

- SQLite tables are the current contract:
  - `rooms(id, building, room, created_at, updated_at)` with `UNIQUE(building, room)`.
  - `electricity_readings(id, room_id, collection_run_id, collection_window_start, collected_at, building, room, numeric_value, unit)`.
  - `collection_runs(id, room_id, started_at, finished_at, collection_window_start, status, error_code, message, reading_inserted)`.
  - Legacy single-row tables such as `room_selection`, `schedule_config`, `alert_config`, `alert_state`, and `collection_failures` remain backward-compatible until their callers are deliberately migrated.
- `collection_window_start` is computed by the scheduler from the configured interval and stored as an ISO UTC timestamp ending in `Z`.
- `electricity_readings` must keep denormalized `building` and `room` fields so existing `/api/readings` consumers continue to work while future history queries can join by `room_id`.
- Current balance must be read from `Repository.get_latest_successful_reading()` instead of the in-memory reading returned by the portal adapter.
- History responses for trend/chart consumers must include only rows with no linked run (legacy data) or a linked `collection_runs.status = 'success'`; failed and duplicate runs are diagnostics, not chart points.

### 4. Validation & Error Matrix

- Duplicate reading for the same `room_id` and `collection_window_start` -> ignore insert, return `False`, mark collection run `duplicate`.
- Successful fresh reading -> insert history row, return `True`, mark collection run `success`.
- Expected collection error -> mark collection run `failed` with the stable `AppError.code`, then re-raise.
- Unexpected collection error -> mark collection run `failed` with `UNEXPECTED_ERROR`, then re-raise for normal error handling.
- Scheduled collection catches expected and unexpected errors, writes a concise `collection_failures` row, then reschedules if config is still enabled.

### 5. Good/Base/Bad Cases

- Good: scheduler runs twice in the same configured window for the same room; only one `electricity_readings` row exists and the second run is visible as `duplicate`.
- Base: app starts with an old SQLite file; initialization adds missing columns/tables without deleting existing readings.
- Bad: route handlers write SQL directly, or scheduler inserts readings without a room/window dedupe key.

### 6. Tests Required

- Repository initialization creates `rooms`, `electricity_readings`, and `collection_runs` plus the reading dedupe index.
- Scheduler collection writes a reading and a `success` collection run.
- Same room plus same collection window does not create a second reading row.
- Duplicate collection windows do not evaluate alerts a second time.
- Service/status payloads expose the latest successful DB reading for current balance.
- Expected auth/session failures create a `failed` collection run with the stable error code.
- Scheduled unexpected failures create both a `failed` collection run and a concise `collection_failures` row with `UNEXPECTED_ERROR`.

### 7. Wrong vs Correct

#### Wrong

```python
repository.insert_reading(reading)
alerts.evaluate(reading)
```

#### Correct

```python
run_id = repository.start_collection_run(selection, started_at, collection_window_start)
inserted = repository.insert_reading(reading, collection_window_start, run_id)
alerts.evaluate(reading) if inserted else False
repository.finish_collection_run(run_id, finished_at, "success" if inserted else "duplicate", inserted)
```
