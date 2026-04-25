# Directory Structure

> How frontend code is organized in this project.

---

## Current Project State

No application frontend source tree exists yet. The UI requirements are described in `task.md` and the dorm electricity Trellis PRDs.

---

## Required Frontend Layout

When frontend code is introduced, use feature-oriented directories and keep page, component, data access, and state concerns separate.

```text
frontend/
├── src/
│   ├── app/ or pages/
│   ├── features/
│   │   ├── auth-room/
│   │   ├── schedule/
│   │   ├── readings-chart/
│   │   └── alerts/
│   ├── components/
│   ├── api/
│   ├── hooks/
│   ├── types/
│   └── styles/
└── tests/
```

---

## Module Organization

- `auth-room` owns login guidance and building/room selection.
- `schedule` owns interval, start time, and end time configuration UI.
- `readings-chart` owns trend visualization and empty/error states.
- `alerts` owns threshold and email recipient settings.
- `api/` owns backend request functions.
- `components/` contains reusable UI only.

---

## Naming Conventions

- Name components by purpose: `RoomSelector`, `ScheduleForm`, `ElectricityTrendChart`, `AlertSettingsForm`.
- Name hooks with `use*`, e.g. `useReadings`, `useScheduleConfig`.
- Keep domain types in `types/` or feature-local `types.ts` files.

---

## Examples

- `.trellis/tasks/04-26-dorm-electricity-auth-room/prd.md` defines login and room selection UI.
- `.trellis/tasks/04-26-dorm-electricity-trend-chart/prd.md` defines chart UI behavior.
- `.trellis/tasks/04-26-dorm-electricity-email-alerts/prd.md` defines alert settings UI.

---

## Forbidden Patterns

- Do not put all UI in a single page component.
- Do not let chart components perform low-level backend request construction.
- Do not store passwords or SMTP secrets in browser storage.
- Do not hide scheduler or alert validation errors from the user.
