# Directory Structure

> How backend code is organized in this project.

---

## Current Project State

This repository is in the planning/bootstrap stage. There is no application backend source tree yet. Requirements are captured in `task.md` and the `04-26-dorm-electricity-*` Trellis PRDs.

---

## Required Backend Layout

When backend code is introduced, use a feature-oriented layout with clear boundaries between API, integration, scheduling, persistence, and alerting logic.

```text
backend/
├── app/
│   ├── main.*
│   ├── config/
│   ├── routes/
│   ├── services/
│   ├── integrations/
│   ├── scheduler/
│   ├── persistence/
│   ├── alerts/
│   └── shared/
└── tests/
    ├── unit/
    └── integration/
```

If the chosen stack uses `src/`, keep the same internal separation under that root.

---

## Module Organization

- `routes/` validates input, calls services, and returns responses only.
- `services/` owns use cases such as room selection, schedule updates, reading history, and alert settings.
- `integrations/` owns communication with `http://10.80.34.137:92/Default.aspx` and `http://10.80.34.137:92/web/auths/index.aspx`.
- `scheduler/` owns intervals, active windows, and duplicate-job prevention.
- `persistence/` owns durable storage and migrations.
- `alerts/` owns threshold evaluation and email transport.

---

## Naming Conventions

- Use descriptive domain names such as `electricity_collector`, `schedule_service`, and `email_alert_service`.
- Avoid vague names such as `utils`, `common`, or `manager` unless the file is truly shared infrastructure.
- Keep portal HTML parsing separate from HTTP session handling.
- Keep configuration names explicit: `SMTP_HOST`, `SMTP_PORT`, `ALERT_THRESHOLD`, `POLL_INTERVAL_SECONDS`.

---

## Examples

- `.trellis/tasks/04-26-dorm-electricity-monitor/prd.md` defines feature boundaries.
- `.trellis/tasks/04-26-dorm-electricity-scheduler-collector/prd.md` defines scheduler and collector responsibilities.
- `.trellis/tasks/04-26-dorm-electricity-email-alerts/prd.md` defines alerting responsibilities.

---

## Forbidden Patterns

- Do not combine login, scraping, scheduling, persistence, and email sending in one file.
- Do not hard-code credentials, SMTP secrets, room identifiers, or thresholds.
- Do not let route handlers directly parse remote HTML or send emails.
- Do not bypass the campus portal login flow.
