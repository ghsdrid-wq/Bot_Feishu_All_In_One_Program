# Extended report hours implementation plan

Date: 2026-11-03 (Asia/Bangkok)
Status: implemented for offline testing on 2026-11-03; live acceptance pending. See CURRENT-WORK for source/build evidence and limits.
Baseline: feature/python-report-calculation, published checkpoint 4804acc.

## Outcome

Use the selected reporting cycle's actual start and end datetimes to generate hourly columns. A cycle ending at 12:00 ends with 11-12; ending at 13:00 adds 12-13; ending at 14:00 adds both 12-13 and 13-14. Extra columns remain present when they contain zero. Preserve accepted report styling, row definitions, and source-language aliases. Keep accumulated pre-noon Auto output when the exporter resets.

## Evidence and limitations in the current implementation

- Createphoto.run_create passes a business date derived from the current clock and datetime.now to prepare, rather than the complete selected run window. This must change for historical/manual runs and delayed final runs.
- Inputs.autopacking and report_staging.stage assign dates using hour < 12. stage rejects any source modified after the next noon. This deliberately protects old data today but cannot represent the proposed extension.
- report_workbooks.calculate evaluates fixed template cells and helper sheets, with fixed heading controls/column offsets. Writing values alone does not create dynamic columns.
- daily_summary.make_window uses a configured day_end_hour and hour-only keys; repeated hours in a cycle over 24 hours are ambiguous. Existing newer-main behavior must be reviewed before reusing or changing it.
- Source files are numbered 00-23. Current mapping associates NN.xlsx with hour NN-1; validate that mapping around reset with a controlled source fixture/live exporter evidence.
- The existing build has not passed complete live-share/image/send acceptance. New functionality must not be described as production-ready based on existing unit tests.

## Design decisions

### Confirmed delivery order (user clarification, 2026-11-03)

- Automatic final summary is anchored to the selected cycle end datetime, not an independent fixed daily send time. An end of next-day 12:00 means process the final 11-12 output on that day; after the final hourly report has been successfully delivered, send the cycle summary. Apply the same ordering for 13:00/14:00 ends.
- End time is the data cutoff, not a promise of immediate delivery at that exact minute. Preserve the configured final-run delay/minute where appropriate; queue the summary behind completion and successful delivery of the last hourly report. Do not use a fixed sleep as a completion signal.
- Track final-hour delivery and summary delivery separately per cycle. Failures/retries/restarts must not send a premature summary or duplicate an already successful summary. If final-hour delivery fails, retain a pending summary with a visible reason and retry policy. Do not silently roll the pending cycle into the next cycle.
- Retain and adapt the existing manual preview/send-summary controls rather than create duplicate actions. Display the selected cycle and data cutoff. A request before the cycle ends must be visibly an interim summary; it must not mark the final automatic summary as delivered. The existing send confirmation/destination review remains available.
- Replace/migrate the independent auto-summary send_time behavior explicitly; retain old settings for legacy mode where necessary, and explain the new end-linked behavior in the UI.

1. One immutable report window: cycle ID, start datetime, scheduled end datetime, calculation cutoff and capture timestamp. Data boundaries use [start, end); capture time does not extend the data window merely because the final run occurs at HH:05. Manual historical runs use their explicitly selected cycle. Preserve the current policy for partial hours explicitly rather than silently changing it; define/test cutoff behavior before implementation.
2. Generate ordered slots with start/end datetimes. Use full timestamps as internal keys, not hour-of-day or filenames. Labels can still be 12-13; repeated labels require a date marker so separate days cannot be confused. No modulo-based 24-hour truncation. Validate allowed duration explicitly; never silently wrap a long cycle.
3. Scheduled end controls the maximum layout. During execution show completed slots up to the cutoff; the final report includes all full slots through the selected end even when zero. Do not infer the end from the last nonzero output. Keep any existing optional trailing-low-volume title policy separate from the actual data window and review its interaction with the user's new expectation.
4. Snapshot identity includes source, calendar date/hour and capture/reset generation. Business-cycle membership is separate: extending a cycle past noon must not reassign it to the next cycle, and the next cycle must not count the same records unintentionally.
5. Preserve prior successful immutable Auto snapshots before reset; append post-noon slots from the new generation to the original cycle. Never combine two cumulative snapshots by summing whole files. First establish whether each source file is hourly-only or cumulative, and validate internal timestamps where present. Filesystem mtime alone cannot prove content provenance. If a reset destroys data before it was captured, fail/mark incomplete explicitly; no algorithm can reconstruct missing source data reliably.
6. Treat DWS offline status independently of output. Preserve dated successful snapshots, show source age/completeness, and make missing data distinguishable from a verified zero in the report/delivery status. Do not label access-denied or signing failures as confirmed power-off.
7. Use one slot sequence for AutoPacking, DWS, PDA, totals, titles, and capture ranges. Identify which Realtime QC metrics are snapshots rather than hourly additive measures; do not add hourly columns or sum snapshots indiscriminately.
8. Compute totals from the selected slots; compute averages/percentages from their original numerators/denominators, not averages of hourly ratios. Preserve documented template-specific semantics, including the existing single-unique-error quirk, unless separately approved.
9. Generate report cells using explicit row/style definitions extracted from the accepted template. Place hourly columns before totals, move merged headings/totals as needed, and derive capture boundaries from the generated layout. Avoid shifting arbitrary old formulas/relationships and assuming that styles/merges/chart references repaired themselves. Inputs and operator templates remain untouched.

## Execution sequence

### 1. Integrate the correct baseline

Fetch and inspect main updates since the migration checkpoint. Save the tested baseline and merge relevant current behavior on a development branch, resolving scheduler/header/export changes before adding the extension. Preserve operator config/ignored Excel files. Run the baseline regression/UI checks. Do not resurrect the unrelated wrong-chat quick-date/seconds changes.

### 2. Add the shared window/slot model

Introduce a pure module used by scheduler run snapshots, manual exports, report preparation and aggregation. Pass explicit datetimes from bot_main through Createphoto. Implement boundary/duplicate-hour tests first, with fixed clock fixtures. Ensure an active extended cycle is not rolled/reset at noon by another timer; decide concurrent/overlapping cycle policy explicitly.

### 3. Make staging reset-aware

Replace the fixed noon rejection rule with dated generation-aware snapshots plus atomic manifests. Capture all required pre-reset slots while they exist, retain them after restart, and select post-reset files only for their matching slots. Check stability/ZIP integrity/cancellation, preserve the last accepted output on failure, and bound network operations so offline stations cannot hang the report indefinitely. Record provenance and completeness without raw parcel or credential logs.

### 4. Generate dynamic report layouts and aggregate

Inventory row definitions, totals, rates, merged cells and export ranges of both actual operator templates. Implement explicit slot-driven numeric tables, retaining original visual style. Keep QC snapshot behavior separate. Derive totals/headers/capture bounds from the same layout result. Keep the legacy fallback available; do not edit the original XLSX files.

### 5. Verify and package

Run synthetic parity and visual QA, then controlled live validation for one coherent cycle. Package a new test version after checks pass; keep 2.2.0 as fallback. Send a live test only to a user-authorized test destination; do not treat the implementation request as authorization to message production groups.

## Acceptance matrix

- End at next-day 12/13/14: final hourly delivery completes before summary starts. Delayed processing, failure, retry, restart, overlapping next cycle and duplicate completion callbacks do not produce premature or duplicate final summaries. Manual interim summaries do not consume the automatic final-send state.
- Ends at 12/13/14: correct last label and exactly zero/one/two additional columns; matching overall/shift/feeder/station totals, rates and image bounds.
- Start hours supported by the existing templates, midnight transition, historical dates, restart, repeated-hour cycles over 24 hours and cancellation. No hour-key collisions or hidden truncation.
- Boundary records immediately before, exactly at, and immediately after slot/cycle boundaries. Each eligible event belongs to exactly one slot. Final processing delay does not admit records after the selected end.
- Verified-zero extra hour remains visible. Missing/offline input is visibly incomplete, not an unqualified zero. DWS coming back online replaces the relevant partition rather than accumulating duplicates.
- Auto reset before/at/after noon, source changes during copy, missing pre-noon cache, stale/fresh mtime with misleading contents, duplicate snapshot retries, and process restart. Never replace pre-reset totals with reset zeroes or count cumulative totals twice.
- Conservation checks: overall additive total equals slot totals and corresponding row/station totals; percentages use correct denominators; zero denominators and existing error-count semantics match the baseline.
- Exact midnight and 12:00-13:00/13:00-14:00 interval mapping from NN.xlsx is demonstrated with exporter evidence. Unresolved source semantics block live-ready claims.
- All accepted output views rendered/reopened; titles, merges, totals, rates, fonts and capture ranges remain readable. Increased width must not clip columns or produce unusably small report text.
- Existing regression/UI suites pass. Benchmark elapsed time and peak memory using representative local inputs; report measurements rather than assuming improvements.

## Delivery evidence

Record source commit, tests, fixture window and expected totals, source manifest/completeness, screenshots, build checksum and live verification status in CURRENT-WORK. Keep secrets/raw personal records out of versioned artifacts. Update this plan when source behavior changes the implementation decision.

## Next action

Implementation update (2026-11-03): explicit windows, dynamic columns/rates/capture bounds, calendar-hour Auto cache, persisted end-linked summaries and automatic-cycle overlap prevention are implemented. Validation: 135 tests, real Tk, 2,742 baseline numeric comparisons and native Excel opening. Legacy timer retained. Source reset semantics, coherent live totals, target Office capture and authorized delivery still require operator-machine acceptance; mtime alone cannot certify exporter contents. The next action is live acceptance with the 2.3.0 test package.

Integration completed on 2026-11-03: merged e1d4699 into the migration branch, preserving runtime changes from both branches. Baseline validation: 126 regression tests plus real Tk Sources/switch checks. The offline test harness now suppresses upstream bot autostart. No new executable or live-delivery verification. Proceed with the shared window/slot model and boundary tests; fixed auto-summary cutoff is still baseline behavior until the planned delivery-state change is implemented.

Upstream inspection on 2026-11-03: origin/main is e1d4699 (latest commit 2026-10-29); five commits were added after the previously inspected dfbc0da. They include recall, newly-started-cycle summary protection, fresh summary-specific raw exports, automatic/manual summary separation and card refresh after changing round start. Current upstream automatic summaries stop at 13:00 and offer send hours only through 12, directly conflicting with the requested end-linked 14:00 final summary. Preserve manual datetime range support (including repeated hours over 24h), source date checks, recall and fresh downloads; replace the incompatible automatic cutoff behavior in the new mode deliberately. Neither these upstream changes nor the extended-hours feature have been merged/implemented locally by this read-only GitHub check. The feature branch remains published at 4804acc.

Inspect and integrate upstream main, then implement the shared datetime slot model and its boundary tests. The user authorized planning first; this document is the concrete implementation plan, not a claim that the feature has been built.
