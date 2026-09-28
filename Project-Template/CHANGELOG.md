# Changelog

## 2.0.2

- Separated the automatic scheduler lifetime from each report's business-day export range, preventing multi-day queries from mixing report dates, slowing JMS, and producing incomplete hourly tables.
- Added a fail-closed Excel report check that blocks image delivery when a visible hour header no longer matches the hour used by its formulas.
- Corrected the operational `AUTO PDA!T15` template formula for `Autopacking_pda11` from hour 12 to hour 04; preserved a dated backup beside the workbook.

## 2.0.1

- Fixed JMS scheduler and prewarm exports passing `datetime` objects into JSON; export ranges are now serialized before requests are sent.

## 2026-09-28

- Added `app_version.py` as the single version source for the window title, visible sidebar version, tests, and release folder name.
- Added `tools/bump_version.py` so the next patch release is one command instead of editing duplicated version strings.
- Built and smoke-tested `dist/AutoReportFeishuV2-0-0/AutoReportFeishu.exe`; the process remained running and exposed the expected `v2.0.0` window title.
- Fixed the scheduler to honor the complete Start/End datetime range across midnight and finish cleanly after the selected end time.
- Bound Dashboard ingestion, rendering, refresh, and Feishu delivery to the selected run business date.
- Prevented stale or incomplete Dashboard images from being marked done or sent after source/render failures.
- Added cooperative Dashboard cancellation and removed the Run Now double-click race.
- Replaced successfully-read metrics partitions before upsert so corrected or removed upstream rows do not remain in SQLite.
- Reloaded metrics configuration automatically when either YAML or the main INI changes.
- Preserved historical date selections across restarts and kept automatic date rollover for auto-managed ranges.
- Restored Excel attachment delivery when a selected workbook has no image card or Excel image generation is disabled.
- Made Dashboard round and shift labels follow configured hours instead of fixed 12:00 boundaries.
- Preserved operator configuration and `store.db` across onedir rebuilds.
- Added regression coverage for scheduler boundaries, stale partitions, Dashboard failure gating, fresh-image delivery, config reload, date preservation, attachment-only delivery, and retry behavior.
- Verified 12 regression tests, query refresh resilience, Python compilation, PowerShell syntax, and responsive Dashboard rendering at 1280x800 and 390x844.

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
- Moved every non-zero Feishu chart value above its own stacked segment without changing the stack, series order, or original bar spacing.
- Verified the approved placement with synthetic round 26 and refreshed production-source data for business date 2026-09-25.
