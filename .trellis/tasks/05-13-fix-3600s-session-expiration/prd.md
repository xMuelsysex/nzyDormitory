# Fix 3600-second scheduler session expiration

## Goal

When the scheduler interval is set to 3600 seconds, scheduled collection should not permanently fail just because the campus portal session has expired between runs. The collection path should detect expired portal sessions, recover when possible, and surface an actionable state when recovery is not possible.

## What I Already Know

- The user reported that setting the interval seconds to `3600` causes login state to drop.
- The frontend default schedule interval is `3600` seconds in `frontend/src/index.html`.
- `CollectionScheduler.run_once()` currently checks only `portal.authenticated` before calling `portal.fetch_reading(selection)`.
- `CampusPortalClient.fetch_reading()` marks `authenticated = False` when the portal returns a login page and raises `AuthenticationError`.
- Scheduled failures are recorded via `Repository.record_failure()`, but the next run will still fail while `authenticated` remains false.
- The current browser-proxied login flow does not appear to persist enough credentials to automatically re-login after a session expires.
- This repository already has SQLite persistence through `backend/app/persistence/repository.py`, including `schedule_config`, `electricity_readings`, and `collection_failures`; database work is not a clean-slate task.

## Assumptions

- The failure is caused by campus portal session/cookie expiry during long waits, not by the `threading.Timer` interval itself.
- The safest MVP is to make session expiry explicit and recoverable without storing plaintext campus passwords.
- If automatic re-login needs stored credentials, that should be a separate security-sensitive follow-up decision.

## Requirements

- Detect expired or invalid portal sessions during scheduled and manual collection.
- Record a deterministic failure category when collection fails because authentication expired.
- Keep the scheduler alive after an auth-expired failure so future successful login can resume scheduled collection without recreating the schedule.
- Expose the expired-auth state through status/API behavior so the frontend can tell the user to re-login.
- After the user reauthenticates through the existing portal login flow, the next scheduled/manual collection should succeed without requiring schedule reconfiguration.
- Avoid logging raw cookies, passwords, tokens, or full portal pages.

## Acceptance Criteria

- [ ] A test simulates a scheduler run where `fetch_reading()` returns/raises an expired-session condition and verifies that failure is recorded without stopping future scheduling.
- [ ] A test verifies that successful login after expiry restores `authenticated` status and collection can run again.
- [ ] API/status response distinguishes unauthenticated/session-expired from generic portal failure.
- [ ] Manual `run once` after session expiry returns an actionable auth error, not a misleading generic failure.
- [ ] Existing scheduler, portal parsing, and monitor service tests still pass.

## Out of Scope

- Storing campus account passwords for unattended automatic re-login.
- Building a new database schema for readings pagination.
- Frontend pagination of the readings table.
- Replacing `threading.Timer` with a full job queue.

## Technical Notes

- Relevant files inspected:
  - `backend/app/scheduler/collection_scheduler.py`
  - `backend/app/integrations/campus_portal.py`
  - `backend/app/persistence/repository.py`
  - `frontend/src/app.js`
  - `frontend/src/index.html`
- Existing database path setting: `backend/app/config/settings.py` has `database_path=data_dir / "dorm_electricity.sqlite3"`.
- Existing persisted failure table: `collection_failures(failed_at, error_code, message)`.
- Current auth expiry behavior is hidden inside `CampusPortalClient.fetch_reading()`: diagnosis `login_page` sets `authenticated = False` and raises `AuthenticationError`.

## Definition of Done

- Tests added/updated for session-expiry scheduler behavior.
- Lint/typecheck or project test command passes.
- No sensitive auth artifacts are logged or persisted.
- Follow-up database pagination tasks remain separate.
