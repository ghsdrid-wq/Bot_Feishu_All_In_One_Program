# Decisions

## 2026-09-29 - Separate scheduler lifetime from report data

- Scheduler Start/End datetimes control only when automatic execution is allowed.
- Each automatic report uses a fresh current-business-day export window.
- Manual DATA EXPORT continues to use the exact range selected by the operator.
- Treat a header/formula hour mismatch as a hard report-integrity failure; never send a plausible-looking image with shifted hourly totals.

## 2026-09-28 - Dashboard correctness gates

- Treat the selected Start/End range as the scheduler lifetime; report and Dashboard business dates come from each run's current business-day window.
- Replace a metrics partition only after its upstream source was read successfully; retain last-known data on read failure.
- Do not render or send a fresh-looking Dashboard when a required source or PNG render fails.
- Require Dashboard files to be produced by the current run before Feishu delivery.
- Preserve user-owned config and database files during onedir rebuilds.

## 2026-09-28 - Single-source semantic versioning

- Keep the semantic version only in `app_version.py`.
- Derive the window title, visible sidebar version, and release folder `AutoReportFeishuVx-y-z` from that value.
- Increment patch/minor/major with `tools/bump_version.py`; never duplicate a release number in build logic.

## 2026-09-26 - Preserve a clean upstream checkout

- Use `origin/main` as the synchronization baseline.
- Use fast-forward-only pulls to avoid accidental merge commits.

## 2026-09-26 - Report chart palette

- Use `#C62828`, `#F4B6C2`, `#D9D9D9`, and `#F57573` in series order.
- Reserve `#2F2F2F` for text instead of chart data.
- Apply the same series mapping to Feishu cards and the web dashboard.

## 2026-09-26 - Report table palette

- Use red for primary headers, coral for shift headers, pink for totals, and a pale pink stripe for row tracking.
- Keep green, yellow, and red status cells because they carry operational meaning.
- Use dark-theme adaptations of the palette instead of copying light backgrounds directly.
- Use layered charcoal grays instead of near-black dark surfaces to keep dense tables readable.

## 2026-09-26 - Feishu chart labels

- Preserve the original stacked bars and place each non-zero value immediately above its own segment boundary.
- Use 8 px bold labels in darker red, pink, gray, and coral counterparts of each series, plus a thin white outline.
- Keep all non-zero static values visible; exact values also remain available in the chart tooltip.
- Preserve the existing stacked chart and tables; make only the time axis responsive with 45/60-degree rotation, parity hiding, and minimum spacing.
- Preserve the original tightly adjacent bars; do not add band padding between hourly columns.
- Keep the tested line-chart implementation dormant in `modules/feishu_line_chart_variant.py` for possible later reuse.
