# PRD: Electricity Trend Visualization

## Goal

Build the UI that helps users understand electricity or balance changes over time from scheduled readings.

## Requirements

- Display stored readings as a time-series chart.
- Show timestamp and electricity/balance value in chart interactions or table details.
- Handle empty, loading, and error states.
- Make trends clear enough for users to understand consumption direction.
- Support data produced by the scheduler/collector task.

## Implementation Notes

- Keep chart rendering independent from data fetching/transformation.
- Prefer a simple readable chart over complex visual features.
- Ensure the UI remains usable on common desktop and mobile browser widths.

## Acceptance Criteria

- Users can view historical electricity/balance readings after collection runs.
- Empty state explains that scheduled collection must run before chart data exists.
- Chart updates when new readings are available.
