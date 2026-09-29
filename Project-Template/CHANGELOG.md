# Changelog

## 2.1.3

- Protected Feishu webhook and plan/refresh mutation endpoints with the configured verification token while leaving read-only status available.
- Required exact JMS `staffNo` matches so a partial or ambiguous search result cannot reset the wrong user.
- Reported password-reset partial success when reset succeeds but account enabling fails, and now replies to the requester when the JMS token is invalid.
- Preserved the previous complete JMS/Realtime workbook during cancellation, invalid downloads, conversion failures, and stale queued runs by validating and atomically replacing XLSX files.
- Prevented stopped or superseded pipeline steps from being marked successful or publishing late output.
- Fixed the scheduler's final minute so a loop wake-up a few seconds after the configured end still runs that minute once.
- Expanded regression coverage to 43 passing tests before packaging.
- Passed a controlled production-like live cycle using the `C:\0DWS` workbooks and the test Feishu chat: DWS, JMS AUTO/PDA, Realtime, four Excel images, the group card, and the Realtime workbook were delivered successfully in 441.9 seconds.
- Validated all four generated XLSX archives and PNG images after the live cycle; no partial-download file, Excel process, or AutoReport process remained.

## 2.1.2

- Removed decorative emoji and the informal `⚠ FAILED` heading from all Feishu replies while preserving individual failure details and backend status logging.

## 2.1.1

- Smoothed mouse-wheel scrolling on the Settings page by coalescing wheel input and easing canvas movement without changing any settings fields or persistence behavior.
- Added a repeatable UI responsiveness profiler for Settings-page scrolling.

## 2.1.0

- Removed the redundant standalone Dashboard navigation page while preserving the BOT REPORT Dashboard step and its generation, web-server, and Feishu delivery paths.
- Removed the unused DATA EXPORT navigation page while preserving its DWS/JMS export routines for the main report pipeline.
- Added a dedicated compact `จัดการสิทธิ์รหัส` page for blocked prefixes and exact-code exemptions.
- Added multi-line Excel paste, TXT/CSV import/export, search, duplicate/invalid reporting, multi-delete, and 20-row pagination for 50–100+ entries.
- Moved blocked-prefix editing out of the JMS Bot page and added a direct policy-management link there.
- Added atomic `jms_user_policy.json` storage, one-time migration from legacy `blocked_keywords`, and preservation across onedir upgrades.
- Applied exact exemptions before prefix blocks and fail closed when policy data is missing or corrupt.
- Snapshot policy once per incoming Feishu message so a bulk command cannot use mixed rules during a concurrent edit.
- Verified the real desktop UI with 100 exemptions; pagination limits rendering to 20 rows and reduced measured render time from about 1.9s to 0.54s.
- Expanded the automated suite to 31 passing tests and built/smoke-tested `AutoReportFeishuV2-1-0` with a responsive v2.1.0 window and ports 6100/6200.

## 2.0.3

- Removed the scheduled JMS early-export control and execution path; same-run AUTO/PDA generation remains parallel and starts only at the report cutoff.
- Made shared `config.ini` writes atomic and preserved the Dashboard-owned token when the main UI saves an older in-memory configuration.
- Added regression coverage for cross-thread config ownership and literal percent signs in operator-entered secrets/URLs.
- Switched production INI readers to raw parsing so `%` in credentials or URLs is treated as data instead of interpolation syntax.
- Cleared fatal/static closure checks in the Excel export path without changing the accepted workbook or chart layout.
- Confirmed with live source reads that missing current-day JMS input now stops Dashboard output instead of creating and sending a plausible all-zero report.
- Built `dist/AutoReportFeishuV2-0-3` and smoke-tested its EXE for 12 seconds; the v2.0.3 window stayed responsive and ports 6100/6200 listened successfully.

## 2.0.2

- Separated the automatic scheduler lifetime from each report's business-day export range, preventing multi-day queries from mixing report dates, slowing JMS, and producing incomplete hourly tables.
- Added a fail-closed Excel report check that blocks image delivery when a visible hour header no longer matches the hour used by its formulas.
- Corrected the operational `AUTO PDA!T15` template formula for `Autopacking_pda11` from hour 12 to hour 04; preserved a dated backup beside the workbook.
- Prevented scheduled JMS pre-fire snapshots from being reused after their data cutoff, eliminating missing tail-minute scans while retaining same-run parallel AUTO/PDA generation.
- Blocked Dashboard rendering when a required JMS source has no rows for the requested business date.
- Bounded the DWS9-11 metrics query to one business-day window instead of scanning all later records.

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
