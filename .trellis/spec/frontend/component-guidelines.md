# Component Guidelines

> How components are built in this project.

---

## Overview

Frontend components must be simple, accessible, and aligned with the planned dorm electricity workflows.

---

## Component Structure

- Keep components focused on one responsibility.
- Put data loading in hooks or page-level containers; keep presentational components mostly prop-driven.
- Extract repeated form fields only after duplication is clear.
- Keep chart data transformation separate from chart rendering.

---

## Props Conventions

- Props should describe domain intent, not implementation details.
- Prefer explicit handler names such as `onScheduleSave`, `onRoomSelected`, and `onAlertConfigChange`.
- Components that render remote data must accept loading, empty, and error states.

---

## Styling Patterns

No styling framework has been selected yet. Once selected, document it here. Until then:

- Keep layout responsive for common desktop and mobile widths.
- Use consistent spacing and form grouping.
- Make status, validation, and threshold warnings visually clear.

---

## Accessibility

- Every input must have a label.
- Validation errors must be associated with the relevant input.
- Buttons must describe the action clearly.
- Charts must provide a text/table fallback or summary of latest value and trend.

---

## Common Mistakes

- Combining all forms and chart rendering in one component.
- Hiding empty states when no scheduled reading exists yet.
- Making threshold warnings depend on color alone.
- Forgetting mobile layout for schedule and alert forms.
