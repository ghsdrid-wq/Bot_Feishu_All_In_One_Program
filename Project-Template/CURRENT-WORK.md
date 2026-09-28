# Current Work

- Automatic and Run Now reports now calculate a fresh current-business-day data window at execution time; manual DATA EXPORT continues to honor its explicitly selected range.
- Hourly Excel reports are validated after start-hour column trimming; a shifted header/formula pair now fails the pipeline before any incorrect image can be sent.
- The operational DWS/PDA workbook was checked across the exported ranges; 40 DWS/PDA hourly formula columns align after correcting `AUTO PDA!T15`. Backup: `C:\0DWS\1DWS_&_PDA_v1.8.3 Bot.before-hour-fix-20260929.xlsx`.

- Version 2.0.1 fixes JMS automatic and prewarm export ranges by normalizing them to JSON-safe strings at both the shared range helper and the JMS request boundary.

## Objective

- Stabilize the upgraded Scheduler, Dashboard, metrics ingestion, and Feishu delivery pipeline without regressing the accepted chart layout.

## Status

- Scheduler now evaluates full datetimes across midnight and stops after the selected end datetime.
- Dashboard uses the selected business date and refuses to send stale/incomplete artifacts.
- Metrics ingestion replaces successful source partitions, reloads changed config, and preserves last-known data on source failure.
- Historical date selections and attachment-only Feishu delivery are preserved.
- Configured round/shift times render consistently on desktop and mobile without changing the approved chart layout.
- Automated regression suite passes 12 tests; responsive browser QA passes with no console/page errors or horizontal overflow.
- Application version is `2.0.0`; the release folder is derived automatically as `dist/AutoReportFeishuV2-0-0`.
- Future releases use `python tools/bump_version.py patch` before building; both UI and release folder update from the same source.
- The complete onedir release was built and passed a 10-second launch smoke test with config, metrics config, `_internal`, and bundled Playwright present.
- Excel `CopyPicture` still fails in the local Office environment in both the old and new versions; cleanup succeeds with no leftover Excel process, so it is not a regression from this change.

## Next Action

- Run one controlled live DWS/JMS/Feishu cycle on the production network, then commit and push when requested.
