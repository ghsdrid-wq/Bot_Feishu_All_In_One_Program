# Decisions

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
