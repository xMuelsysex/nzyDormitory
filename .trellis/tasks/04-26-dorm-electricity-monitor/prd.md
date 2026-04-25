# PRD: Dorm Electricity Monitoring Web App

## Background

Build a web application deployed on an Ubuntu server to help users monitor dormitory electricity balance from the campus self-service electricity purchase system.

Source requirement: `task.md`.

## Goals

- Let users log in through the campus portal at `http://10.80.34.137:92/Default.aspx` before using the app.
- After login, let users select their building and room.
- Periodically read current dorm electricity or balance information from the self-service electricity purchase project at `http://10.80.34.137:92/web/auths/index.aspx`.
- Allow users to configure schedule start time, end time, and recurring interval.
- Persist each sampled electricity or balance reading for trend analysis.
- Visualize electricity usage or balance trends over time.
- Send an email alert when the value is below a user-defined threshold.

## Scope

### In scope

- Full-stack web app suitable for Ubuntu server deployment.
- User login workflow that depends on the campus portal session.
- Building and room selection flow after successful login.
- Scheduled collection job controlled by user-configured timing.
- Historical readings storage.
- Trend chart UI for sampled readings.
- User-configurable low-balance threshold and recipient email address.
- Mail sending for threshold alerts.

### Out of scope for first implementation

- Automatic online payment or purchase operations.
- Bypassing or weakening the campus system login/security flow.
- Multi-tenant account isolation beyond what is necessary for a first usable local deployment, unless implementation discovers existing auth requirements.

## Functional Requirements

1. Login and room setup
   - User must authenticate against the campus portal before data collection.
   - The app must guide the user to select building and room after login.
   - The app must preserve only the minimum session/configuration state required for authorized polling.

2. Scheduling
   - User can set polling interval, start time, and end time.
   - Polling only runs inside the configured active window.
   - The scheduler must tolerate temporary fetch failures and record or log errors without crashing the server.

3. Data collection
   - The collector reads dorm electricity or balance information from the self-service electricity purchase project.
   - Each successful reading stores timestamp, building, room, and balance/electricity fields.
   - Failed readings store enough diagnostic information for troubleshooting without exposing credentials.

4. Trend visualization
   - The UI displays time-series history after scheduled collection has produced data.
   - Users can identify consumption or balance trends clearly from the chart.
   - Empty states should explain that data appears after the first successful scheduled reading.

5. Email alerts
   - User can configure threshold and recipient email address.
   - When the latest reading is below the threshold, an alert email is sent.
   - Duplicate alert spam should be avoided by recording alert state or applying a cooldown.

## Non-functional Requirements

- Deployment target: Ubuntu server.
- Security: do not log passwords, session cookies, or SMTP secrets.
- Reliability: scheduler and polling failures must be recoverable.
- Maintainability: keep collector, scheduler, persistence, alerting, and UI concerns separated.
- Observability: log scheduling events, successful samples, failed samples, and alert sends at appropriate levels.

## Suggested Subtasks

- `04-26-dorm-electricity-auth-room`: login flow and room selection.
- `04-26-dorm-electricity-scheduler-collector`: scheduled collection and persistence.
- `04-26-dorm-electricity-trend-chart`: trend visualization UI.
- `04-26-dorm-electricity-email-alerts`: threshold and email notifications.

## Acceptance Criteria

- A Trellis implementation task can build the app from this PRD without needing to re-read `task.md`.
- The task hierarchy contains independent implementation units for auth/room setup, collection, charting, and alerts.
- `implement.jsonl` and `check.jsonl` validate successfully.
