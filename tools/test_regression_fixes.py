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
from dashboard import notify, pipeline, render, server  # noqa: E402
from metrics import core  # noqa: E402
from metrics import ingest_dws_db  # noqa: E402
from core import jms_policy  # noqa: E402


class SchedulerRegressionTests(unittest.TestCase):
    def test_version_drives_title_and_release_folder(self) -> None:
        self.assertEqual(bot_main.APP_VERSION, app_version.APP_VERSION)
        self.assertIn(f"v{app_version.APP_VERSION}", bot_main.APP_TITLE)
        self.assertEqual(app_version.release_folder(),
                         "AutoReportFeishuV" +
                         app_version.APP_VERSION.replace(".", "-"))

    def test_policy_page_is_inserted_and_retired_pages_are_removed(self) -> None:
        app = bot_main.App.__new__(bot_main.App)
        app.config = configparser.RawConfigParser()
        app.config["UI"] = {
            "nav_order": "home,workbooks,data_export,dws_plan,jms_user,dashboard,settings"
        }
        app.nav_items = {
            "home": ("", ""), "workbooks": ("", ""),
            "dws_plan": ("", ""),
            "jms_user": ("", ""), "code_policy": ("", ""),
            "settings": ("", ""),
        }
        self.assertEqual(
            app.get_nav_order(),
            ["home", "workbooks", "dws_plan", "jms_user",
             "code_policy", "settings"],
        )

    def test_dashboard_pipeline_step_remains_available_on_bot_report(self) -> None:
        self.assertIn("dashboard", [step[0] for step in bot_main.PIPELINE_STEPS])

    def test_data_export_backend_remains_available(self) -> None:
        self.assertTrue(callable(bot_main.App.run_dws_jms_task))
        self.assertTrue(callable(bot_main.App.run_dws_jms_process))

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

    def test_early_jms_snapshot_is_not_created_or_reused(self) -> None:
        app = self.make_app()
        app.running = False
        app.stop_requested = False
        app.scheduler_running = True
        app.run_generation = 8
        app.write_log = lambda *_args, **_kwargs: None
        app.prewarm_jms_exports = mock.Mock()
        app.prefire_jms_exports()
        app.prewarm_jms_exports.assert_not_called()
        self.assertFalse(app._jms_marker_usable(
            (datetime(2026, 9, 28, 16, 55), None, time.time())))


class JmsPolicyRegressionTests(unittest.TestCase):
    def test_exempt_exact_code_overrides_blocked_prefix_only_for_that_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "policy.json")
            jms_policy.save_policy({
                "blocked_prefixes": ["999004"],
                "exempt_codes": ["999004T00123"],
            }, path)
            exempt = jms_policy.evaluate_code("999004t00123", path)
            blocked = jms_policy.evaluate_code("999004T00999", path)
            self.assertTrue(exempt.allowed)
            self.assertTrue(exempt.exempt)
            self.assertEqual(exempt.matched_prefix, "999004")
            self.assertFalse(blocked.allowed)
            self.assertEqual(blocked.matched_prefix, "999004")

    def test_bulk_parser_accepts_excel_column_and_reports_bad_or_duplicate(self) -> None:
        valid, invalid, duplicates = jms_policy.parse_bulk_entries(
            "999004T00123\n999004T00456\n999004T00123\nbad!",
            "exempt_codes",
        )
        self.assertEqual(valid, ["999004T00123", "999004T00456"])
        self.assertEqual(invalid, ["BAD!"])
        self.assertEqual(duplicates, ["999004T00123"])

    def test_corrupt_policy_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text("{broken", encoding="utf-8")
            decision = jms_policy.evaluate_code("999004T00123", str(path))
            self.assertFalse(decision.allowed)
            self.assertIsNotNone(decision.error)

    def test_legacy_prefixes_migrate_only_when_policy_is_first_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "policy.json")
            first = jms_policy.ensure_policy(path, ["999004"])
            self.assertEqual(first["blocked_prefixes"], ["999004"])
            first["blocked_prefixes"] = []
            jms_policy.save_policy(first, path)
            second = jms_policy.ensure_policy(path, ["999004"])
            self.assertEqual(second["blocked_prefixes"], [])

    def test_command_handler_blocks_prefix_but_executes_exact_exemption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "policy.json")
            jms_policy.save_policy({
                "blocked_prefixes": ["999004"],
                "exempt_codes": ["999004T00123"],
            }, path)
            app = bot_main.App.__new__(bot_main.App)
            app.jms_policy_path = path
            app.jms_running = True
            app.get_feishu_config_value = lambda *_args: "BOT"
            app.jms_log = lambda *_args: None
            app.notify_it_alert = lambda *_args: None

            with (mock.patch.object(bot_main, "detect_jms_intent", return_value="APP"),
                  mock.patch.object(bot_main, "reply_feishu_message"),
                  mock.patch.object(bot_main, "write_log"),
                  mock.patch.object(bot_main, "reset_app_password", return_value="1234"),
                  mock.patch.object(bot_main, "enable_user"),
                  mock.patch.object(bot_main, "search_user",
                                    return_value={"id": "u1", "name": "Test"}) as search,
                  mock.patch.object(bot_main, "extract_staff_numbers",
                                    return_value=["999004T00999"])):
                app.handle_jms_command("รีรหัส app", "chat", "message")
                search.assert_not_called()

            with (mock.patch.object(bot_main, "detect_jms_intent", return_value="APP"),
                  mock.patch.object(bot_main, "reply_feishu_message"),
                  mock.patch.object(bot_main, "write_log"),
                  mock.patch.object(bot_main, "reset_app_password", return_value="1234"),
                  mock.patch.object(bot_main, "enable_user"),
                  mock.patch.object(bot_main, "search_user",
                                    return_value={"id": "u1", "name": "Test"}) as search,
                  mock.patch.object(bot_main, "extract_staff_numbers",
                                    return_value=["999004T00123"])):
                app.handle_jms_command("รีรหัส app", "chat", "message")
                search.assert_called_once_with("999004T00123")


class MetricsRegressionTests(unittest.TestCase):
    def test_main_config_save_preserves_dashboard_owned_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text(
                "[TIME]\nrun_minute = 5\n[DASHBOARD]\ntoken = fresh-token\n",
                encoding="utf-8",
            )
            stale = configparser.RawConfigParser()
            stale["TIME"] = {"run_minute": "7"}
            stale["DASHBOARD"] = {"token": "stale-token"}
            with mock.patch.object(Createphoto, "resource_path", return_value=str(path)):
                Createphoto.save_config(stale)
            saved = configparser.RawConfigParser()
            saved.read(path, encoding="utf-8")
            self.assertEqual(saved["TIME"]["run_minute"], "7")
            self.assertEqual(saved["DASHBOARD"]["token"], "fresh-token")

    def test_config_accepts_literal_percent_in_operator_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text(
                "[FEISHU]\nAPP_SECRET = value%with%percent\n",
                encoding="utf-8",
            )
            with mock.patch.object(Createphoto, "resource_path", return_value=str(path)):
                loaded = Createphoto.load_config()
            self.assertEqual(
                loaded["FEISHU"]["APP_SECRET"], "value%with%percent")

    def test_dashboard_token_creation_preserves_main_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text("[TIME]\nrun_minute = 9\n", encoding="utf-8")
            with (mock.patch.object(server, "CONFIG_INI", str(path)),
                  mock.patch.object(server.secrets, "token_urlsafe",
                                    return_value="generated-token")):
                settings = server.get_settings()
            saved = configparser.RawConfigParser()
            saved.read(path, encoding="utf-8")
            self.assertEqual(settings["token"], "generated-token")
            self.assertEqual(saved["TIME"]["run_minute"], "9")
            self.assertEqual(saved["DASHBOARD"]["token"], "generated-token")

    def test_dws_db_query_is_bounded_to_one_business_day(self) -> None:
        self.assertEqual(
            ingest_dws_db.query_bounds("2026-09-28", 16, 2099),
            ("2026-09-28 16:00:00", "2026-09-29 16:00:00"),
        )

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

    def test_missing_business_date_rows_block_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = {
                "enabled": True, "render_png": True, "png_dir": tmp,
                "hide_empty_png": False, "tables_only_png": True,
            }
            ok = {"rows": 1, "warnings": []}
            missing = {
                "rows": 0,
                "warnings": ["ไม่มีข้อมูลของวันรอบงาน 2026-09-28"],
            }
            with (mock.patch.object(pipeline, "_settings", return_value=settings),
                  mock.patch.object(pipeline.ingest_autopacking, "ingest_folder",
                                    return_value=ok),
                  mock.patch.object(pipeline.ingest_dws, "ingest_all", return_value=ok),
                  mock.patch.object(pipeline.ingest_dws_db, "ingest", return_value=ok),
                  mock.patch.object(pipeline.ingest_jms, "ingest_all", return_value=missing),
                  mock.patch.object(render, "render_all_tabs") as render_tabs):
                result = pipeline.run_cycle(
                    business_date="2026-09-28",
                    db_path=str(Path(tmp) / "store.db"),
                    log=lambda *_args, **_kw: None)
            self.assertEqual(result["status"], "error")
            self.assertFalse(render_tabs.called)

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
