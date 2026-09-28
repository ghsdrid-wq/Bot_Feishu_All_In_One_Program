from __future__ import annotations

import configparser
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot_main  # noqa: E402
import card_report  # noqa: E402
import app_version  # noqa: E402
import Createphoto  # noqa: E402
from dashboard import notify, pipeline, render  # noqa: E402
from metrics import core  # noqa: E402


class SchedulerRegressionTests(unittest.TestCase):
    def test_version_drives_title_and_release_folder(self) -> None:
        self.assertEqual(bot_main.APP_VERSION, app_version.APP_VERSION)
        self.assertIn(f"v{app_version.APP_VERSION}", bot_main.APP_TITLE)
        self.assertEqual(app_version.release_folder(),
                         "AutoReportFeishuV" +
                         app_version.APP_VERSION.replace(".", "-"))

    def make_app(self) -> bot_main.App:
        app = bot_main.App.__new__(bot_main.App)
        cfg = configparser.RawConfigParser()
        cfg["TIME"] = {
            "run_minute": "1", "start_hour": "16", "end_hour": "23",
            "prefire_enabled": "true", "prefire_lead": "5",
        }
        cfg["DWS_JMS"] = {
            "start_date": "2026-09-27", "end_date": "2026-09-28",
        }
        app.config = cfg
        app.ui_thread_id = -1
        app.last_run_minute = None
        return app

    def test_scheduler_uses_full_datetime_range_across_midnight(self) -> None:
        app = self.make_app()
        self.assertTrue(app.in_send_window(datetime(2026, 9, 28, 2, 1)))
        self.assertTrue(app.in_send_window(datetime(2026, 9, 28, 23, 0)))
        self.assertFalse(app.in_send_window(datetime(2026, 9, 29, 0, 0)))
        self.assertEqual(
            app.get_next_scheduler_run_time(datetime(2026, 9, 28, 2, 30)),
            datetime(2026, 9, 28, 3, 1),
        )
        self.assertIsNone(
            app.get_next_scheduler_run_time(datetime(2026, 9, 28, 23, 30)))

    def test_attachment_only_block_is_not_dropped(self) -> None:
        app = self.make_app()
        app.get_feishu_group_names = lambda: []
        app.get_selected_excel_files_for_feishu = (
            lambda group="", include_generated=None: ["report.xlsx"]
            if group == "" else [])
        with mock.patch.object(bot_main.Botmessage, "get_send_file_names",
                               return_value=[]):
            blocks, image_blocks = app.get_feishu_delivery_blocks(False)
        self.assertEqual(blocks, [""])
        self.assertEqual(image_blocks, [])

    def test_historical_dates_are_not_overwritten(self) -> None:
        app = self.make_app()

        class DateWidget:
            def __init__(self, value):
                self.value = value

            def get_date(self):
                return self.value

            def set_date(self, value):
                self.value = value

        app.ui_thread_id = __import__("threading").get_ident()
        app.start_date = DateWidget(datetime(2026, 9, 20).date())
        app.end_date = DateWidget(datetime(2026, 9, 21).date())
        app._dates_user_selected = True
        app.shift_date_range = lambda: (
            datetime(2026, 9, 27).date(), datetime(2026, 9, 28).date())
        app.apply_shift_dates()
        self.assertEqual(app.start_date.get_date(), datetime(2026, 9, 20).date())
        self.assertEqual(app.end_date.get_date(), datetime(2026, 9, 21).date())

    def test_export_time_range_serializes_scheduler_snapshot(self) -> None:
        app = self.make_app()
        app.active_run_settings = {
            "time_range": (
                datetime(2026, 9, 27, 16, 0),
                datetime(2026, 9, 28, 23, 0),
            )
        }
        self.assertEqual(
            app.get_time_range(),
            ("2026-09-27 16:00:00", "2026-09-28 23:00:00"),
        )

    def test_report_range_is_separate_from_scheduler_lifetime(self) -> None:
        app = self.make_app()
        self.assertEqual(
            app.get_report_time_range(datetime(2026, 9, 28, 2, 1, 9)),
            ("2026-09-27 16:00:00", "2026-09-28 02:01:09"),
        )
        self.assertEqual(
            app.get_report_time_range(datetime(2026, 9, 28, 23, 0, 4)),
            ("2026-09-28 16:00:00", "2026-09-28 23:00:04"),
        )

    def test_jms_payload_never_contains_datetime_objects(self) -> None:
        app = self.make_app()
        app.stop_requested = False
        app.log = lambda *_args, **_kwargs: None

        class Response:
            @staticmethod
            def json():
                return {}

        with mock.patch.object(app, "_jms_post", return_value=Response()) as post:
            app._jms_fire_export(
                object(), "https://example.invalid", {},
                datetime(2026, 9, 27, 16, 0),
                datetime(2026, 9, 28, 23, 0),
                "scan-type",
            )
        payload = post.call_args.args[2]
        self.assertEqual(payload["startTimeStr"], "2026-09-27 16:00:00")
        self.assertEqual(payload["endTimeStr"], "2026-09-28 23:00:00")
        self.assertIsInstance(payload["startTimeStr"], str)
        self.assertIsInstance(payload["endTimeStr"], str)


class MetricsRegressionTests(unittest.TestCase):
    def test_config_cache_reloads_after_file_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.yaml"
            path.write_text("cache_probe: 12\nbusiness_day:\n  start_hour: 12\n",
                            encoding="utf-8")
            core.clear_config_cache()
            self.assertEqual(core.load_config(str(path))["cache_probe"], 12)
            time.sleep(0.01)
            path.write_text("cache_probe: 16\nbusiness_day:\n  start_hour: 16\n",
                            encoding="utf-8")
            self.assertEqual(core.load_config(str(path))["cache_probe"], 16)
            core.clear_config_cache()

    def test_replace_partition_removes_only_requested_scope(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE fact_hourly (
                business_date TEXT, source TEXT, station TEXT, hour_start INTEGER)
        """)
        conn.executemany(
            "INSERT INTO fact_hourly VALUES (?,?,?,?)",
            [("2026-09-27", "DWS", "DWS1", 16),
             ("2026-09-27", "DWS", "DWS2", 16),
             ("2026-09-28", "DWS", "DWS1", 16)],
        )
        core.replace_fact_partition(
            conn, "fact_hourly", "2026-09-27", "DWS", station="DWS1")
        rows = conn.execute(
            "SELECT business_date, station FROM fact_hourly ORDER BY 1,2"
        ).fetchall()
        self.assertEqual(rows, [("2026-09-27", "DWS2"),
                                ("2026-09-28", "DWS1")])

    def test_card_ingest_failure_retries_in_same_hour(self) -> None:
        summary = {"business_date": "2026-09-27", "sources": []}
        card_report._last_ingest_slot = None
        with (mock.patch.object(card_report, "refresh_autopacking"),
              mock.patch.object(card_report, "load_summary", return_value=summary),
              mock.patch.object(card_report, "_stale_sources", return_value=True),
              mock.patch.object(pipeline, "run_cycle",
                                side_effect=[RuntimeError("temporary"),
                                             {"status": "ok", "rows": 1}]) as run_cycle):
            card_report.ensure_summary("2026-09-27")
            card_report.ensure_summary("2026-09-27")
        self.assertEqual(run_cycle.call_count, 2)


class ReportHourAlignmentTests(unittest.TestCase):
    def test_hour_header_parser_supports_report_formats(self) -> None:
        self.assertEqual(Createphoto.extract_hour_header("16点-17点"), 16)
        self.assertEqual(Createphoto.extract_hour_header("16:00-17:00"), 16)
        self.assertIsNone(Createphoto.extract_hour_header("合计"))

    def test_formula_hour_parser_reads_literal_count_criteria(self) -> None:
        formula = '=COUNTIFS(raw!$A:$A,AI11&" 16*",raw!$B:$B,"ok")'
        self.assertEqual(Createphoto.extract_formula_hours(formula), [16])
        self.assertEqual(Createphoto.extract_formula_hours("=SUM(A1:A2)"), [])

    def test_shifted_formula_is_blocked_before_report_export(self) -> None:
        class Dimension:
            def __init__(self, count):
                self.Count = count

        class AuditRange:
            Value2 = (("16点-17点", "17点-18点"), (None, None))
            Formula = (("16点-17点", "17点-18点"),
                       ('=COUNTIF(raw!A:A," 17*")',
                        '=COUNTIF(raw!A:A," 17*")'))

        class UsedRange:
            Row = 1
            Column = 1
            Rows = Dimension(2)
            Columns = Dimension(2)

        class Worksheet:
            def __init__(self, used_range, audit_range):
                self.UsedRange = used_range
                self.audit_range = audit_range

            @staticmethod
            def Cells(row, column):
                return row, column

            def Range(self, _first, _last):
                return self.audit_range

        class Workbook:
            def __init__(self, worksheet):
                self.worksheet = worksheet

            def Worksheets(self, _name):
                return self.worksheet

        with self.assertRaisesRegex(RuntimeError, "header 16:00 uses formula"):
            Createphoto.validate_hour_formula_alignment(
                Workbook(Worksheet(UsedRange(), AuditRange())),
                ["DWSREALTIME"], 16)


class DashboardRegressionTests(unittest.TestCase):
    def test_stop_is_honored_before_ingest(self) -> None:
        result = pipeline.run_cycle(should_stop=lambda: True, render=False)
        self.assertEqual(result["status"], "stopped")
        self.assertTrue(result["stopped"])

    def test_render_uses_configured_round_and_shift_labels(self) -> None:
        dates = []
        if os.path.exists(core.DEFAULT_DB_PATH):
            conn = core.read_connect()
            try:
                dates = list(core.available_dates(conn).values())
            finally:
                conn.close()
        business_date = next((value for value in reversed(dates) if value), None)
        if not business_date:
            self.skipTest("No dashboard fixture data")
        cfg = core.load_config()["business_day"]
        html = render.render_html(business_date)
        start = int(cfg["start_hour"]) % 24
        shift_a = int(cfg["shift_a_start"]) % 24
        shift_b = int(cfg["shift_b_start"]) % 24
        self.assertIn(f"รอบ {start:02d}:00–{start:02d}:00", html)
        self.assertIn(f"{shift_a:02d}:00–{shift_b:02d}:00", html)
        self.assertIn(f"{shift_b:02d}:00–{start:02d}:00", html)

    def test_source_failure_blocks_render_and_marks_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = {
                "enabled": True, "render_png": True, "png_dir": tmp,
                "hide_empty_png": False, "tables_only_png": True,
            }
            ok = {"rows": 1, "warnings": []}
            with (mock.patch.object(pipeline, "_settings", return_value=settings),
                  mock.patch.object(pipeline.ingest_autopacking, "ingest_folder",
                                    side_effect=RuntimeError("source down")),
                  mock.patch.object(pipeline.ingest_dws, "ingest_all", return_value=ok),
                  mock.patch.object(pipeline.ingest_dws_db, "ingest", return_value=ok),
                  mock.patch.object(pipeline.ingest_jms, "ingest_all", return_value=ok),
                  mock.patch.object(render, "render_all_tabs") as render_tabs):
                result = pipeline.run_cycle(
                    business_date="2026-09-27",
                    db_path=str(Path(tmp) / "store.db"), log=lambda *_args, **_kw: None)
            self.assertEqual(result["status"], "error")
            self.assertFalse(render_tabs.called)
            self.assertNotIn("png", result)

    def test_missing_fresh_png_is_rejected_before_feishu_api(self) -> None:
        cfg = {
            "send_enabled": True, "chat_id": "oc_test", "send_tabs": ["overview"],
            "send_link": False, "public_url": "", "token": "",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with (mock.patch.object(notify, "settings", return_value=cfg),
                  mock.patch.object(notify.feishu_api, "get_token") as get_token):
                result = notify.send_dashboard(
                    "2026-09-27", tmp, expected_files=[], min_mtime=time.time(),
                    log=lambda *_args, **_kw: None)
        self.assertIn("error", result)
        self.assertFalse(get_token.called)


if __name__ == "__main__":
    unittest.main()
