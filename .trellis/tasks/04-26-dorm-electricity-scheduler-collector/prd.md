# PRD: Scheduled Electricity Data Collection

## Goal

Implement scheduled polling of dorm electricity or balance data from the self-service electricity purchase project.

## Requirements

- Read from `http://10.80.34.137:92/web/auths/index.aspx` after the user has logged in.
- Allow user-defined schedule interval, start time, and end time.
- Run only during the configured active window.
- Store every successful sample with timestamp, building, room, and electricity/balance fields.
- Record failed collection attempts without crashing the app.
- Make collected history available to the trend chart and alert system.

## Implementation Notes

- Isolate scheduler logic from fetch/parser logic.
- Isolate parser logic so source page changes are easier to fix.
- Use durable storage appropriate for an Ubuntu server deployment.
- Avoid uncontrolled duplicate jobs when schedule settings are changed.

## Acceptance Criteria

- A configured schedule produces repeated readings during the active time window.
- Invalid schedule inputs are rejected with useful validation messages.
- Collection failures are logged safely and the next scheduled run can still execute.
