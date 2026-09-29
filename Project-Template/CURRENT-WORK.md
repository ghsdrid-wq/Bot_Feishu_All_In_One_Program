# Current Work

- Version 2.1.3 hardens authenticated inbound control, exact JMS user selection, partial reset reporting, final-minute scheduling, cancellation isolation, and atomic XLSX downloads.
- Version 2.1.2 removes decorative emoji and the user-visible `⚠ FAILED` heading from Feishu replies; individual failure details and internal status logging are preserved.
- Version 2.1.1 smooths Settings-page mouse-wheel scrolling; operator acceptance confirms the full page now takes about 3-4 wheel rotations instead of 5-6.
- Version 2.1.0 adds a dedicated JMS access-policy page with blocked prefixes, exact-code exemptions, bulk paste/import/export, search, multi-delete, and pagination.
- The standalone Dashboard and DATA EXPORT navigation pages are removed from the desktop UI. Dashboard remains available as a BOT REPORT step, and the underlying DWS/JMS export routines remain intact for the main pipeline.
- JMS commands now snapshot one atomic JSON policy per message; exact exemptions override matching prefixes, while unreadable policy data blocks modifying commands.
- Automatic and Run Now reports calculate a fresh current-business-day data window at execution time; the retained internal manual-export helper still honors an explicitly supplied range.
- Hourly Excel reports are validated after start-hour column trimming; a shifted header/formula pair now fails the pipeline before any incorrect image can be sent.
- The operational DWS/PDA workbook was checked across the exported ranges; 40 DWS/PDA hourly formula columns align after correcting `AUTO PDA!T15`. Backup: `C:\0DWS\1DWS_&_PDA_v1.8.3 Bot.before-hour-fix-20260929.xlsx`.
- Scheduled early JMS snapshots are no longer created or reused because they cannot contain scans from the remaining minutes; AUTO/PDA generation still starts in parallel inside the actual run.
- Dashboard now fails closed when a required JMS file has no data for the requested business date, and DWS9-11 aggregation reads only the requested business-day window.
- Shared `config.ini` updates are atomic inside the application; main-UI saves preserve the Dashboard-owned access token.
- The misleading scheduled pre-fire control and scheduler path have been removed. Same-run JMS AUTO/PDA requests still start together.

- Version 2.0.1 fixes JMS automatic and prewarm export ranges by normalizing them to JSON-safe strings at both the shared range helper and the JMS request boundary.

## Objective

- Stabilize the upgraded Scheduler, Dashboard, metrics ingestion, and Feishu delivery pipeline without regressing the accepted chart layout.

## Status

- Scheduler now evaluates full datetimes across midnight and stops after the selected end datetime.
- Dashboard uses the selected business date and refuses to send stale/incomplete artifacts.
- Metrics ingestion replaces successful source partitions, reloads changed config, and preserves last-known data on source failure.
- Historical date selections and attachment-only Feishu delivery are preserved.
- Configured round/shift times render consistently on desktop and mobile without changing the approved chart layout.
- Automated regression suite passes 43 tests, including inbound-token rejection, exact-user matching, partial reset replies, final-minute scheduling, cancellation isolation, atomic download replacement, policy handling, and backend preservation.
- Application version is `2.1.3`; the release folder is derived automatically as `dist/AutoReportFeishuV2-1-3`.
- Future releases use `python tools/bump_version.py patch` before building; both UI and release folder update from the same source.
- The complete `AutoReportFeishuV2-1-0` onedir release passed a 12-second launch smoke test with the v2.1.0 title, ports 6100/6200, config, metrics config, `_internal`, and bundled Playwright present.
- The complete `AutoReportFeishuV2-1-1` onedir release passed a 12-second launch smoke test with the v2.1.1 title, a responsive window, ports 6100/6200, config, metrics config, `_internal`, and bundled Playwright present.
- The complete `AutoReportFeishuV2-1-2` onedir release passed a 12-second launch smoke test with the v2.1.2 title, a responsive window, ports 6100/6200, config, metrics config, `_internal`, and bundled Playwright present.
- The complete `AutoReportFeishuV2-1-3` onedir release passed a 12-second launch smoke test with the v2.1.3 title, a responsive window, ports 6100/6200, config, metrics config, `_internal`, and bundled Playwright present.
- Excel `CopyPicture` still fails in the local Office environment in both the old and new versions; cleanup succeeds with no leftover Excel process, so it is not a regression from this change.

## Remaining live acceptance

- Run one controlled live DWS/JMS/Excel/Feishu cycle with the production token and chat configuration. The repository and carried-forward build config do not contain those operator secrets.
- Rotate the current default-like `VERIFY_TOKEN` in both the program and Feishu Event Subscription before exposing either HTTP port outside the trusted LAN.
- Local Office still rejects `Range.CopyPicture` for the synthetic cleanup workbook; the test confirms forced cleanup leaves no Excel process behind, but a production-machine Excel-image run is still required.
