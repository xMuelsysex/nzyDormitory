# PRD: Low Balance Email Alerts

## Goal

Notify a user-selected email recipient when electricity or balance falls below a configured threshold.

## Requirements

- Allow user to configure alert threshold.
- Allow user to configure recipient email address.
- Send an email when the latest collected value is below the threshold.
- Avoid repeated spam for the same low-balance condition through cooldown or alert-state tracking.
- Log alert delivery success/failure without exposing SMTP secrets.

## Implementation Notes

- Keep alert evaluation separate from email transport.
- Validate email address and threshold before enabling alerts.
- Store SMTP configuration securely through environment variables or deployment configuration, not hard-coded source.

## Acceptance Criteria

- Alert settings can be saved and used by the collector flow.
- A below-threshold reading triggers an email to the configured recipient.
- Repeated below-threshold readings do not cause uncontrolled email spam.
