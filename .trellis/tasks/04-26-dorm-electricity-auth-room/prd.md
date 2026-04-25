# PRD: Login and Dorm Room Selection

## Goal

Implement the initial user flow for logging into the campus portal and choosing the dorm building and room used for electricity monitoring.

## Requirements

- Show a clear login/start page before any monitoring features are available.
- Support the campus login target: `http://10.80.34.137:92/Default.aspx`.
- After login succeeds, allow the user to select building and room.
- Persist the selected building and room for subsequent polling jobs.
- Do not store or log plaintext passwords.
- Surface login/session failures clearly to the user.

## Implementation Notes

- Keep campus-system integration isolated behind a service/client boundary.
- Keep UI state separate from persistence and remote scraping/fetching logic.
- Prepare for Ubuntu server deployment where browser automation or HTTP session handling may need explicit dependencies.

## Acceptance Criteria

- User cannot configure polling until login and room selection are completed.
- Selected building and room are available to the scheduler/collector task.
- Sensitive login/session material is redacted from logs and errors.
