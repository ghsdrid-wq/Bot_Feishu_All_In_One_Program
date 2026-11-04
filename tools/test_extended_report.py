"""Conservation, boundary and durable delivery tests for extended report cycles."""

import configparser
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import openpyxl

from core.report_delivery import DeliveryLedger
from core.report_staging import stage
from core.report_window import ReportWindow
from report_workbooks import calculate
from tools.test_report_workbooks import fixture, input_fixtures


class ExtendedReportTests(unittest.TestCase):
    def test_next_automatic_cycle_does_not_recount_extended_hours(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DeliveryLedger(Path(tmp) / "delivery.json")
            window = ReportWindow(
                datetime(2026, 11, 3, 12),
                datetime(2026, 11, 4, 14),
                datetime(2026, 11, 4, 14),
            )
            ledger.hourly_delivered(window, "chat")
            start, end = ledger.align_recurring_start(
                datetime(2026, 11, 4, 12), datetime(2026, 11, 5, 14)
            )
            self.assertEqual(start, window.end)
            self.assertEqual(end, datetime(2026, 11, 5, 14))

    def test_summary_runtime_cache_works_without_custom_paths(self):
        import daily_summary

        settings = daily_summary.settings_from_config(configparser.RawConfigParser())
        settings.options["custom_paths"] = "false"
        settings.options["runtime_autopacking_dir"] = "dated-local-cache"
        self.assertEqual(settings.autopacking_folder, "dated-local-cache")

    def test_restart_recovers_cycle_before_final_hour_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DeliveryLedger(Path(tmp) / "delivery.json")
            window = ReportWindow(
                datetime(2026, 11, 3, 12),
                datetime(2026, 11, 4, 14),
                datetime(2026, 11, 4, 13),
            )
            ledger.remember_cycle(window, "chat")
            restarted = DeliveryLedger(ledger.path)
            self.assertEqual(
                restarted.recover_cycle(window.cutoff),
                (window.start.date(), window.end.date()),
            )
            self.assertIsNone(restarted.claim(window.end))
            with self.assertRaises(ValueError):
                restarted.hourly_delivered(window, "chat")
            final = ReportWindow(window.start, window.end, window.end)
            restarted.hourly_delivered(final, "chat")
            self.assertIsNotNone(restarted.claim(window.end))

    def test_actual_summary_timer_waits_for_delivery_and_runs_after_14(self):
        import bot_main

        with tempfile.TemporaryDirectory() as tmp:
            ledger = DeliveryLedger(Path(tmp) / "delivery.json")
            app = bot_main.App.__new__(bot_main.App)
            app.config = configparser.RawConfigParser()
            app.config["DAILY_SUMMARY"] = {"auto_send": "true"}
            app.running, app.ds_busy, app.scheduler_running = False, False, False
            app.summary_ledger = lambda: ledger
            now = datetime(2026, 11, 4, 14, 5)
            app.daily_summary_now = lambda: now
            app.daily_summary_settings = lambda _: SimpleNamespace(
                chat_id="changed-destination"
            )
            app.daily_summary_option_path = lambda _: ""
            app.after = lambda *args: None
            app.write_log = lambda *args, **kw: self.fail(str(args))
            app.set_daily_summary_status = lambda *args: None
            calls = []
            app.run_daily_summary_in_background = lambda *args, **kw: calls.append(
                (args, kw)
            )
            app.daily_summary_tick()
            self.assertEqual(calls, [])
            window = ReportWindow(
                datetime(2026, 11, 3, 15),
                datetime(2026, 11, 4, 14),
                datetime(2026, 11, 4, 14),
            )
            ledger.hourly_delivered(window, "original-destination")
            app.running = True
            app.daily_summary_tick()
            self.assertEqual(calls, [])
            app.running = False
            app.daily_summary_tick()
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0][0].chat_id, "original-destination")
            self.assertEqual(calls[0][1]["cycle_key"], window.key)
            app.daily_summary_tick()
            self.assertEqual(len(calls), 1)

    def test_end_columns_and_totals_for_both_real_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            paths = input_fixtures(folder)
            for end_hour in (12, 13, 14):
                window = ReportWindow(
                    datetime(2026, 10, 20, 15),
                    datetime(2026, 10, 21, end_hour),
                    datetime(2026, 10, 21, end_hour),
                )
                for filename, sheetname in (
                    ("1AUTOPACKING_BOT.xlsx", "Autoformat"),
                    ("1DWS_&_PDA_v1.8.3 Bot.xlsx", "DWSREALTIME"),
                ):
                    output = folder / filename
                    result = calculate(
                        "Excel/" + filename,
                        str(output),
                        window.start.date(),
                        window.cutoff,
                        paths,
                        start_hour=15,
                        window=window,
                        log=lambda x: None,
                    )
                    book = openpyxl.load_workbook(output, data_only=True)
                    sheet = book[sheetname]
                    cols = result["layouts"][sheetname]["hour_columns"]
                    self.assertEqual(len(cols), 9 + end_hour)
                    self.assertEqual(
                        sheet.cell(3, cols[-1]).value, f"{end_hour - 1}点-{end_hour}点"
                    )
                    if sheetname == "Autoformat":
                        self.assertEqual(
                            [sheet.cell(4, c).value for c in cols], [201] * len(cols)
                        )
                        total_col = next(
                            c
                            for c in range(1, sheet.max_column + 1)
                            if sheet.cell(3, c).value == "合计"
                        )
                        self.assertEqual(
                            sheet.cell(4, total_col).value, 201 * len(cols)
                        )
                        self.assertEqual(sheet.cell(4, total_col + 1).value, len(cols))
                        self.assertEqual(sheet.cell(4, total_col + 2).value, 201)
                    else:
                        self.assertEqual(sum(sheet.cell(4, c).value for c in cols), 201)
                        self.assertEqual(sheet.cell(4, cols[-1]).value, 0)
                        self.assertEqual(book["AUTO PDA"].cell(5, 3).value, 203)
                    self.assertFalse(
                        any(c.data_type == "f" for s in book for row in s for c in row)
                    )
                    book.close()

    def test_repeated_hour_has_separate_data_and_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            paths = input_fixtures(folder)
            window = ReportWindow(
                datetime(2026, 10, 20, 12),
                datetime(2026, 10, 21, 14),
                datetime(2026, 10, 21, 14),
            )
            fixture(
                folder / "DWS1.xlsx",
                ["单号", "出秤时间"],
                [[str(i), "2026/10/20 12:05:00"] for i in range(201)]
                + [[str(i), "2026/10/21 12:05:00"] for i in range(202)]
                + [["end", "2026/10/21 14:00:00"]],
            )
            evidence = calculate(
                "Excel/1DWS_&_PDA_v1.8.3 Bot.xlsx",
                str(folder / "report.xlsx"),
                window.start.date(),
                window.end,
                paths,
                start_hour=12,
                window=window,
                log=lambda x: None,
            )
            self.assertEqual(len(set(window.slots)), 26)
            book = openpyxl.load_workbook(folder / "report.xlsx", data_only=True)
            cols = evidence["layouts"]["DWSREALTIME"]["hour_columns"]
            self.assertIn("10/20", book["DWSREALTIME"].cell(3, cols[0]).value)
            self.assertIn("10/21", book["DWSREALTIME"].cell(3, cols[24]).value)
            self.assertEqual(book["DWSREALTIME"].cell(4, cols[0]).value, 201)
            self.assertEqual(book["DWSREALTIME"].cell(4, cols[24]).value, 202)
            book.close()

    def test_cutoff_clamps_delayed_final_run_and_partial_hours(self):
        start, end = datetime(2026, 11, 3, 15), datetime(2026, 11, 4, 14)
        window = ReportWindow.capture(start, end, end + timedelta(minutes=5))
        self.assertEqual(window.cutoff, end)
        self.assertEqual(window.slots[-1], end - timedelta(hours=1))
        partial = ReportWindow.capture(
            start, end, start + timedelta(hours=1, minutes=5)
        )
        self.assertEqual(partial.slots, (start,))

    def test_dated_cache_survives_reset_without_reading_source(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("core.dws_mirror.plan", return_value=[]),
        ):
            cfg = configparser.RawConfigParser()
            cfg["REPORT_CALCULATION"] = {"auto_cache": tmp}
            window = ReportWindow(
                datetime(2026, 11, 3, 12),
                datetime(2026, 11, 4, 14),
                datetime(2026, 11, 4, 14),
            )
            for slot in window.slots:
                target = (
                    Path(tmp)
                    / "slots"
                    / slot.strftime("%Y-%m-%d")
                    / f"{slot.hour:02d}.xlsx"
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(slot.isoformat().encode())
            paths = {f"{i:02d}": "unreachable" for i in range(24)}
            result = stage(
                paths,
                cfg,
                window.start.date(),
                window.cutoff,
                12,
                lambda: True,
                lambda x: None,
                window=window,
            )
            self.assertNotEqual(
                result[window.slots[0].isoformat()],
                result[window.slots[24].isoformat()],
            )

    def test_summary_requires_final_delivery_and_is_not_reclaimed_on_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DeliveryLedger(Path(tmp) / "delivery.json")
            window = ReportWindow(
                datetime(2026, 11, 3, 15),
                datetime(2026, 11, 4, 14),
                datetime(2026, 11, 4, 14),
            )
            self.assertIsNone(ledger.claim(window.end))
            ledger.hourly_delivered(window, "test-chat")
            self.assertIsNone(ledger.claim(window.end - timedelta(seconds=1)))
            key, item = ledger.claim(window.end)
            self.assertEqual(item["destination"], "test-chat")
            self.assertIsNone(
                DeliveryLedger(ledger.path).claim(window.end + timedelta(hours=1))
            )
            ledger.finish(key, False)
            self.assertIsNone(ledger.claim(window.end + timedelta(minutes=5)))
            self.assertIsNotNone(ledger.claim(window.end + timedelta(minutes=10)))
            ledger.finish(key, True)
            ledger.hourly_delivered(window, "test-chat")
            self.assertIsNone(ledger.claim(window.end + timedelta(hours=1)))


if __name__ == "__main__":
    unittest.main()
