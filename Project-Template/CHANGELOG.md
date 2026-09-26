# Changelog

## 2026-09-26

- Cloned the GitHub repository and verified access to `origin/main`.
- Added the project continuity note structure.
- Updated Feishu card and web dashboard chart colors to the approved palette.
- Verified rendered colors, tab interaction, console health, and mobile overflow behavior.
- Updated table headers, shift headers, alternating rows, and total rows to the same palette.
- Verified the table in light, dark, and mobile table views.
- Lightened the dark theme from near-black to layered charcoal gray and rechecked desktop and mobile tables.
- Moved Feishu chart values inside readable stack segments, reduced them to 10 px, strengthened contrast, and suppressed labels that cannot fit without overlap.
- Restored per-series label colors with darker matching shades instead of one shared gray label color.
- Prevented Feishu mobile hour labels from collapsing into a continuous line while preserving the desktop stacked-chart layout.
- Replaced the subtle AutoPacking offset with the accepted two-lane placement while preserving the original tightly adjacent bars.
- Preserved the evaluated full-label line-chart design as a dormant project module for future reuse.
- Added a regression assertion for the approved four-series color order and sent a balanced all-series synthetic card for client review.
- Removed the experimental band padding after confirming that the accepted reference keeps adjacent bars tightly packed.
