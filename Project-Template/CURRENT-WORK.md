# Current Work

## Objective

- Improve the readability of stacked-bar values in Feishu's narrow card layout.

## Status

- Updated Feishu VChart plus web dashboard chart and table colors.
- Light, dark, desktop, and mobile rendering checks passed with synthetic dashboard data.
- Feishu value labels now use compact, bold, series-matched dark text above each non-zero stacked segment.
- Hour labels now rotate and thin out responsively instead of merging into one unreadable line on mobile.
- The accepted stacked-bar baseline keeps the original tightly adjacent bars and moves only the values above their own segment boundaries.
- The evaluated line-chart alternative is retained in `modules/feishu_line_chart_variant.py` but is not active in production.
- Synthetic round 26 and refreshed live data were accepted by Feishu with the original stack and spacing preserved; only label placement changed.

## Next Action

- Build and deliver the onedir Windows release from the approved stacked-label baseline.
