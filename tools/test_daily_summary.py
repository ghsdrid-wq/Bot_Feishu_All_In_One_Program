from __future__ import annotations

import configparser
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import daily_summary as ds  # noqa: E402


def _scan_file(path: str, rows: list) -> str:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["หมายเลข AWB", "หมายเลขกระสอบ", "ประเภทที่สแกน", "เวลาที่สแกน",
               "สาขาที่สแกน", "พนักงานสแกน", "เวลาอัปโหลด"])
    for awb, bag, scanned, scanner in rows:
        ws.append([awb, bag, "สแกน", scanned, "KKN_GW", scanner, scanned])
        ws.cell(ws.max_row, 1).data_type = "s"  # ข้อความที่ขึ้นต้นด้วย = จาก JMS ไม่ใช่สูตร
    wb.save(path)
    return path


class ScanFileTests(unittest.TestCase):
    def test_reads_thai_headers_renamed_in_october_2026(self) -> None:
        window = ds.make_window(datetime(2026, 10, 21, 15), datetime(2026, 10, 22, 10))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "DWSPDA.xlsx")
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.append(["เลขพัสดุ", "หมายเลขกระสอบ", "ประเภทสแกน", "เวลาสแกน",
                       "สาขาที่สแกน", "จำนวนชิ้น", "ผู้สแกน", "เวลาอัปโหลด"])
            ws.append(["TH0001", "B1", "สแกนสร้างถุง", "2026-10-21 15:08:02",
                       "KKN_GW", 1, "PDA01", "2026-10-21 15:09:00"])
            wb.save(path)
            rows = ds.read_scan_file(path, window)
        self.assertEqual([(r.awb, r.bag, r.scanner) for r in rows], [("TH0001", "B1", "PDA01")])

    def test_header_match_ignores_spaces_and_the_word_thee(self) -> None:
        self.assertEqual(ds._find_column(["หมายเลข", "เวลา ที่ สแกน"], ds.COL_TIME, "x.xlsx"), 1)

    def test_missing_column_error_lists_headers_found(self) -> None:
        with self.assertRaisesRegex(ValueError, "หัวคอลัมน์ในไฟล์: ก, ข"):
            ds._find_column(["ก", "ข"], ds.COL_TIME, "x.xlsx")


class WindowTests(unittest.TestCase):
    def test_hours_run_from_start_until_day_end_and_split_shifts(self) -> None:
        window = ds.make_window(datetime(2026, 10, 18, 15, 0), datetime(2026, 10, 19, 10, 5))
        self.assertEqual(window.hours[0], 15)
        self.assertEqual(window.hours[-1], 11)
        self.assertEqual(len(window.hours), 21)
        self.assertEqual(window.shift_of(0), "A")
        self.assertEqual(window.shift_of(1), "B")
        self.assertEqual(window.labels(), {"date": "2026/10/18", "start": "15.00", "end": "10.00"})

    def test_rows_outside_window_are_ignored(self) -> None:
        window = ds.make_window(datetime(2026, 10, 18, 15), datetime(2026, 10, 19, 10))
        self.assertIsNone(window.slot_of(datetime(2026, 10, 18, 14, 59)))
        self.assertIsNone(window.slot_of(datetime(2026, 10, 19, 10, 0)))
        self.assertEqual(window.slot_of(datetime(2026, 10, 19, 0, 30)), 0)

    def test_title_labels_trim_partial_hour_columns_below_100(self) -> None:
        window = ds.make_window(datetime(2026, 10, 18, 14), datetime(2026, 10, 19, 11))
        totals = {hour: 1_000 for hour in window.hours}
        totals[14] = 35  # ชั่วโมงเริ่มต้นยังไม่เต็ม
        totals[10] = 99  # ชั่วโมงท้ายยังไม่เต็ม
        totals[11] = 0   # คอลัมน์นอกเวลาส่งรายงาน
        self.assertEqual(window.labels_for_hourly_totals(totals), {
            "date": "2026/10/18", "start": "15.00", "end": "10.00",
        })

    def test_title_end_drops_trailing_hours_totalling_at_most_1000(self) -> None:
        # 2026-10-20: 9点-10点 = 25,609 ปล่อยเต็ม, 10点-11点 = 334 เป็นยอดเกินมา -> จบ 10.00
        window = ds.make_window(datetime(2026, 10, 20, 13), datetime(2026, 10, 21, 12, 20))
        totals = {hour: 20_000 for hour in window.hours}
        totals[10], totals[11] = 334, 0
        self.assertEqual(window.labels_for_hourly_totals(totals, trailing_total=1000)["end"], "10.00")
        # ชั่วโมงท้ายหลายชั่วโมงรวมกันเกิน 1000 = ยังทำงานอยู่ ไม่ตัด
        totals[9], totals[10] = 600, 600
        self.assertEqual(window.labels_for_hourly_totals(totals, trailing_total=1000)["end"], "10.00")
        totals[9], totals[10] = 400, 500
        self.assertEqual(window.labels_for_hourly_totals(totals, trailing_total=1000)["end"], "9.00")
        totals[10] = 1_200
        self.assertEqual(window.labels_for_hourly_totals(totals, trailing_total=1000)["end"], "11.00")
        self.assertEqual(window.labels_for_hourly_totals(totals, trailing_total=0)["end"], "11.00")

    def test_window_extends_to_13_only_after_the_extra_hour_finishes(self) -> None:
        start = datetime(2026, 10, 20, 13)
        normal = ds.make_window(start, datetime(2026, 10, 21, 12, 20))
        self.assertEqual((len(normal.hours), normal.hours[-1]), (23, 11))
        extended = ds.make_window(start, datetime(2026, 10, 21, 13, 30))
        self.assertEqual((len(extended.hours), extended.hours[-1]), (24, 12))
        self.assertEqual(extended.end, datetime(2026, 10, 21, 13))
        self.assertIsNone(extended.slot_of(datetime(2026, 10, 21, 13, 10)))
        self.assertEqual(extended.slot_of(datetime(2026, 10, 21, 12, 40)), 12)
        self.assertEqual(extended.labels_for_hourly_totals({**{h: 5_000 for h in extended.hours}})["end"],
                         "13.00")

    def test_autopack_extra_hour_reads_next_folders_13_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            for name in ("2026-10-20", "2026-10-21"):
                os.makedirs(os.path.join(raw, name))
            window = ds.make_window(datetime(2026, 10, 20, 13), datetime(2026, 10, 21, 13, 30))
            self.assertEqual(ds.autopack_hour_path(raw, window, 11),
                             os.path.join(raw, "2026-10-20", "12.xlsx"))
            self.assertEqual(ds.autopack_hour_path(raw, window, 12),
                             os.path.join(raw, "2026-10-21", "13.xlsx"))
            self.assertEqual(ds.autopack_hour_path(raw, window, 13),
                             os.path.join(raw, "2026-10-20", "14.xlsx"))

    def test_title_labels_keep_configured_range_when_no_hour_reaches_100(self) -> None:
        window = ds.make_window(datetime(2026, 10, 18, 14), datetime(2026, 10, 19, 11))
        self.assertEqual(window.labels_for_hourly_totals({hour: 99 for hour in window.hours}),
                         window.labels())


class SendTimeTests(unittest.TestCase):
    def test_sends_once_at_first_run_after_send_time(self) -> None:
        start = datetime(2026, 10, 18, 14)
        self.assertEqual(ds.scheduled_send_at(start, "12:00"), datetime(2026, 10, 19, 12))
        self.assertFalse(ds.due_to_send(start, datetime(2026, 10, 19, 11, 5), "12:00", "")[0])
        self.assertTrue(ds.due_to_send(start, datetime(2026, 10, 19, 12, 0), "12:00", "")[0])
        # รอบ 12:00 พลาด รอบถัดไปของรอบงานเดียวกันส่งแทน
        self.assertTrue(ds.due_to_send(start, datetime(2026, 10, 19, 13, 5), "12:00", "")[0])
        # ส่งแล้ว ไม่ส่งซ้ำ
        self.assertFalse(ds.due_to_send(start, datetime(2026, 10, 19, 12, 5), "12:00", "2026-10-18")[0])

    def test_late_limit_stops_catch_up_after_30_minutes(self) -> None:
        start = datetime(2026, 10, 19, 16)
        self.assertTrue(ds.due_to_send(start, datetime(2026, 10, 20, 12, 50), "12:20", "", 30)[0])
        due, reason = ds.due_to_send(start, datetime(2026, 10, 20, 12, 59), "12:20", "", 30)
        self.assertFalse(due)
        self.assertTrue(reason.startswith(ds.LATE_REASON))
        self.assertIn("ส่งสรุปตอนนี้", reason)

    def test_catch_up_checkbox_controls_late_limit(self) -> None:
        self.assertEqual(ds.late_limit_minutes("true", "45"), 45)
        self.assertEqual(ds.late_limit_minutes("true", "2"), ds.ON_TIME_GRACE_MINUTES)  # ไม่ต่ำกว่าช่วงเผื่อ
        self.assertEqual(ds.late_limit_minutes("false", "45"), ds.ON_TIME_GRACE_MINUTES)
        self.assertEqual(ds.late_limit_minutes("true", ""), 30)

    def test_round_starting_just_before_send_time_waits_for_next_day(self) -> None:
        # 2026-10-27: เปลี่ยนเวลาเริ่มเป็น 12:00 (ส่ง 12:20) แล้วรอบใหม่ถูกส่งตอน 12:24 ทั้งที่เพิ่งเริ่ม
        start = datetime(2026, 10, 27, 12)
        self.assertEqual(ds.scheduled_send_at(start, "12:20"), datetime(2026, 10, 28, 12, 20))
        self.assertFalse(ds.due_to_send(start, datetime(2026, 10, 27, 12, 24), "12:20", "2026-10-26", 30)[0])
        # รอบก่อนหน้า (เริ่ม 26 12:00) ส่ง 27 12:20 ตามปกติ
        self.assertTrue(ds.due_to_send(datetime(2026, 10, 26, 12), datetime(2026, 10, 27, 12, 21),
                                       "12:20", "2026-10-25", 30)[0])

    def test_new_business_day_waits_for_its_own_send_time(self) -> None:
        start = datetime(2026, 10, 19, 14)  # รอบงานใหม่เริ่มแล้ว ต้องรอถึง 12:00 ของวันถัดไป
        self.assertFalse(ds.due_to_send(start, datetime(2026, 10, 19, 14, 5), "12:00", "2026-10-18")[0])
        self.assertEqual(ds.scheduled_send_at(start, "22:00"), datetime(2026, 10, 19, 22))


class CountTests(unittest.TestCase):
    def test_chinese_headers_match_thai_counts_with_reordered_columns(self) -> None:
        window = ds.make_window(datetime(2026, 10, 18, 15), datetime(2026, 10, 19, 10))
        with tempfile.TemporaryDirectory() as tmp:
            thai = _scan_file(os.path.join(tmp, "thai.xlsx"), [
                ("A1", "B100", "2026-10-18 15:01:00", "Autopacking_pda01"),
                ("A2", "B100", "2026-10-19 01:00:00", "Autopacking_pda01"),
                ("A3", "B200", "2026-10-18 14:00:00", "Autopacking_pda02"),
            ])
            expected = ds.read_scan_file(thai, window)
            book = openpyxl.load_workbook(thai)
            sheet = book.active
            rows = list(sheet.values)
            book.close()
            order = [5, 3, 1, 0, 6, 2, 4]
            book = openpyxl.Workbook()
            sheet = book.active
            sheet.append(["扫描员", "扫描时间", "所属包号", "运单号", "上传时间", "扫描类型", "扫描网点"])
            for row in rows[1:]:
                sheet.append([row[i] for i in order])
            chinese = os.path.join(tmp, "chinese.xlsx")
            book.save(chinese)
            book.close()
            actual = ds.read_scan_file(chinese, window)
            self.assertEqual(actual, expected)
            names = ds.machine_names("Autopacking_pda", 2)
            self.assertEqual(ds.hourly_counts(actual, names, window), ds.hourly_counts(expected, names, window))
            self.assertEqual(ds.bag_counts(actual, names), ds.bag_counts(expected, names))

    def test_waybill_header_accepts_jms_2026_10_23_name(self) -> None:
        # JMS export 2026-10-23: 运单编号 แทน 运单号 (หัวจริงจากไฟล์ DWSXAUTOPDA.xlsx / DWSPDA.xlsx)
        header = ["运单编号", "所属包号", "扫描类型", "扫描时间", "扫描网点", "上一站或下一站", "件数",
                  "重量", "物品类型", "运输方式", "寄件网点", "寄件客户", "扫描员", "派件或收件员", "备注", "上传时间"]
        self.assertEqual(ds._find_column(header, ds.COL_AWB, "scan.xlsx"), 0)

    def test_chinese_headers_take_priority_over_changed_thai_labels(self) -> None:
        header = ["เวลาที่สแกน", "扫描时间", "พนักงานสแกน", "扫描员"]
        self.assertEqual(ds._find_column(header, ds.COL_TIME, "scan.xlsx"), 1)
        self.assertEqual(ds._find_column(header, ds.COL_SCANNER, "scan.xlsx"), 3)

    def setUp(self) -> None:
        self.window = ds.make_window(datetime(2026, 10, 18, 15), datetime(2026, 10, 19, 10))
        self.rows = [
            ds.ScanRow("A1", "B100", datetime(2026, 10, 18, 15, 1), "Autopacking_pda01", ""),
            ds.ScanRow("A2", "B100", datetime(2026, 10, 18, 15, 2), "autopacking_PDA01", ""),
            ds.ScanRow("A3", "b200", datetime(2026, 10, 19, 1, 0), "Autopacking_pda01", ""),
            ds.ScanRow("A4", "P300", datetime(2026, 10, 18, 16, 0), "Autopacking_pda02", ""),
            ds.ScanRow("A5", "B400", datetime(2026, 10, 18, 16, 0), "Autopacking_KKN_WCS_01", ""),
        ]
        self.names = ds.machine_names("Autopacking_pda", 2)

    def test_hourly_counts_count_rows_case_insensitively(self) -> None:
        counts = ds.hourly_counts(self.rows, self.names, self.window)
        self.assertEqual(counts["Autopacking_pda01"][15], 2)
        self.assertEqual(counts["Autopacking_pda01"][1], 1)
        self.assertEqual(counts["Autopacking_pda02"][16], 1)
        self.assertNotIn("Autopacking_KKN_WCS_01", counts)

    def test_bag_counts_are_distinct_b_bags_per_machine(self) -> None:
        self.assertEqual(ds.bag_counts(self.rows, self.names),
                         {"Autopacking_pda01": 2, "Autopacking_pda02": 0})

    def test_shared_bag_counts_only_for_first_scanner_in_file(self) -> None:
        rows = self.rows + [
            ds.ScanRow("A6", "B100", datetime(2026, 10, 18, 17, 0), "Autopacking_pda02", ""),
            ds.ScanRow("A7", "B900", datetime(2026, 10, 18, 17, 0), "Autopacking_pda02", ""),
        ]
        self.assertEqual(ds.bag_counts(rows, self.names),
                         {"Autopacking_pda01": 2, "Autopacking_pda02": 1})
        self.assertEqual([r.bag for r in ds.unique_bag_rows(rows)],
                         ["B100", "b200", "P300", "B400", "B900"])

    def test_dws_stats_follow_excel_over_200_rules(self) -> None:
        window = ds.make_window(datetime(2026, 10, 18, 22), datetime(2026, 10, 19, 3))
        values = {h: 0 for h in window.hours}
        values.update({22: 300, 23: 150, 0: 500, 1: 250, 2: 4})
        stats = ds.dws_row_stats(values, window)
        self.assertEqual((stats["A"], stats["B"], stats["effective"], stats["peak"]), (800, 250, 3, 500))
        self.assertAlmostEqual(stats["avg"], 350)
        table = ds.DwsTable(window, [("DWS_01", "dws", values)])
        summary = ds.dws_summary_rows(table)
        self.assertEqual(summary["total"]["A"], 950)  # แถว 合计 รวมทุกค่า ไม่ตัด 200
        self.assertEqual(summary["pcs"]["hours"][2], 4)

    def test_autopack_counts_use_first_matching_throughput_row(self) -> None:
        window = ds.make_window(datetime(2026, 10, 18, 22), datetime(2026, 10, 19, 3))
        throughput = {22: [["58987-001", "1", "300"], ["58987-001", "1", "999"], ["58987-002", "2", "450"]],
                      0: [["58987-001", 1.0, 250.0]]}
        table = ds.autopack_feeder_counts(throughput, window)
        ap1, ap2 = table.lines[0][1], table.lines[1][1]
        self.assertEqual(ap1[0][1][22], 300)
        self.assertEqual(ap1[0][1][0], 250)
        self.assertEqual((ap2[1][0], ap2[1][1][22]), (14, 450))
        summary = ds.autopack_summary(table)
        self.assertEqual(summary["best"]["A"], (550, 1, 275))
        self.assertTrue(os.path.isfile(ds.render_autopack_table(
            table, os.path.join(tempfile.mkdtemp(), "ap.png"))))

    def test_color_scale_matches_excel_three_colour_rule(self) -> None:
        pick = ds.color_scale([0, 0, 5, 10])
        self.assertEqual(pick(0), ds.SCALE_LOW)
        self.assertEqual(pick(10), ds.SCALE_HIGH)
        self.assertEqual(ds.color_scale([0, 0])(0), ds.SCALE_HIGH)


class BuildTests(unittest.TestCase):
    def test_build_uses_populated_autopacking_columns_in_post_titles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = configparser.RawConfigParser()
            config["PATH"] = {"output_dir": tmp, "autopacking_dir": tmp}
            config["DWS_JMS"] = {"raw_path": tmp}
            ds.ensure_defaults(config)
            config[ds.SECTION]["sections"] = "autopacking"
            config[ds.SECTION]["send_raw_file"] = "false"
            with mock.patch("core.dws_mirror.plan", return_value=[]):
                settings = ds.settings_from_config(config)
            window = ds.make_window(datetime(2026, 10, 18, 14), datetime(2026, 10, 19, 11))
            throughput = {
                14: [["58987-001", 1, 35]],
                15: [["58987-001", 1, 1_000]],
                9: [["58987-001", 1, 1_000]],
                10: [["58987-001", 1, 99]],
            }
            with mock.patch.object(ds, "read_autopack_throughput", return_value=throughput), \
                 mock.patch.object(ds, "render_autopack_table", return_value="autopack.png"):
                # ไม่ให้เทสต์ไปเปิดไฟล์ DWS1-8 บน network share จริง
                with mock.patch.object(ds, "dws_station_paths", return_value={}):
                    result = ds.build(settings, window, os.path.join(tmp, "out"), log=lambda *a, **k: None)

            self.assertEqual(result["title"], "สรุปยอดประจำวันที่ 2026/10/18 เวลา 15.00-10.00")
            self.assertIn("เวลา 15.00-10.00", result["sections"][0].title)

    def test_build_renders_tables_and_raw_workbook_without_formulas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _scan_file(os.path.join(tmp, "DWSXAUTOPDA.xlsx"), [
                ("=1+1", "B1", "2026-10-18 15:10:00", "Autopacking_pda01"),
                ("X2", "B2", "2026-10-18 13:10:00", "Autopacking_pda01"),
            ])
            _scan_file(os.path.join(tmp, "DWSPDA.xlsx"), [
                ("Y1", "", "2026-10-18 16:10:00", "Dws_pda01"),
            ])
            config = configparser.RawConfigParser()
            config["PATH"] = {"output_dir": tmp, "autopacking_dir": os.path.join(tmp, "no-autopacking")}
            config["DWS_JMS"] = {"raw_path": tmp}
            ds.ensure_defaults(config)
            config[ds.SECTION]["sections"] = "auto_pda,pda_bag,dws_pda,autopacking"
            with mock.patch("core.dws_mirror.plan", return_value=[]):
                settings = ds.settings_from_config(config)
            window = ds.make_window(datetime(2026, 10, 18, 15), datetime(2026, 10, 19, 10))
            # ไม่ให้เทสต์ไปเปิดไฟล์ DWS1-8 บน network share จริง
            with mock.patch.object(ds, "dws_station_paths", return_value={}):
                result = ds.build(settings, window, os.path.join(tmp, "out"), log=lambda *a, **k: None)

            by_key = {s.key: s for s in result["sections"]}
            for key in ("auto_pda", "pda_bag", "dws_pda"):
                self.assertTrue(os.path.isfile(by_key[key].image), key)
            self.assertIsNone(by_key["autopacking"].image)
            self.assertIn("autopacking", " ".join(result["warnings"]))

            wb = openpyxl.load_workbook(result["raw_file"])
            auto = wb["AUTO PDA DATA"]
            self.assertEqual(auto.max_row, 2)  # แถว 13:10 อยู่นอกรอบ
            self.assertEqual(auto["A2"].value, "=1+1")
            self.assertEqual(auto["A2"].data_type, "s")
            self.assertEqual(wb["PDA BAG DATA"].max_row, 2)
            self.assertEqual(wb["DWS PDA DATA"].max_row, 2)

    def test_abnormal_counts_distinct_billcodes_within_business_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "all error.xlsx")
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.append(["Billcode", "Pipeline", "Sort Time", "Sort Source"])
            for bill, when, source in [
                ("JT1", "2026/10/18 15:01:07", "No Plan"),
                ("JT1", "2026/10/18 15:20:00", "No Plan"),      # วนซ้ำ นับครั้งเดียว
                ("JT2", "2026/10/19 10:49:42", "Max Cycles"),   # หลังเวลาส่งรายงาน ยังอยู่ในรอบ
                ("JT3", "2026/10/18 14:59:00", "Max Cycles"),   # ก่อนต้นรอบ
                ("JT4", "2026/10/18 16:00:00", "Intercept"),
            ]:
                ws.append([bill, "58987-001", when, source])
            wb.save(path)
            window = ds.make_window(datetime(2026, 10, 18, 15), datetime(2026, 10, 19, 10))
            data = ds.read_abnormal(path, window)
            self.assertEqual(data.counts, {"No Plan": 1, "Max Cycles": 1, "Intercept": 1})
            self.assertEqual(len(data.rows), 3)  # ตัด Billcode ซ้ำเหมือนชีต DATA Abnormal
            image = ds.render_abnormal_table(data.counts, window.business_date,
                                             os.path.join(tmp, "abnormal.png"))
            self.assertTrue(os.path.isfile(image))

    def test_dws_pda_counts_only_rows_without_bag_like_power_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _scan_file(os.path.join(tmp, "DWSXAUTOPDA.xlsx"), [])
            _scan_file(os.path.join(tmp, "DWSPDA.xlsx"), [
                ("P1", "", "2026-10-18 16:10:00", "Dws_pda01"),
                ("P2", "B55", "2026-10-18 16:20:00", "Dws_pda01"),  # สแกนกระสอบ ไม่นับ
            ])
            config = configparser.RawConfigParser()
            config["PATH"] = {"output_dir": tmp, "autopacking_dir": os.path.join(tmp, "no-autopacking")}
            config["DWS_JMS"] = {"raw_path": tmp}
            ds.ensure_defaults(config)
            config[ds.SECTION]["sections"] = "dws_pda"
            with mock.patch("core.dws_mirror.plan", return_value=[]):
                settings = ds.settings_from_config(config)
            window = ds.make_window(datetime(2026, 10, 18, 15), datetime(2026, 10, 19, 10))
            # ไม่ให้เทสต์ไปเปิดไฟล์ DWS1-8 บน network share จริง
            with mock.patch.object(ds, "dws_station_paths", return_value={}):
                result = ds.build(settings, window, os.path.join(tmp, "out"), log=lambda *a, **k: None)
            wb = openpyxl.load_workbook(result["raw_file"])
            self.assertEqual(wb["DWS PDA DATA"].max_row, 2)

    def test_custom_paths_apply_only_when_enabled_and_filled(self) -> None:
        config = configparser.RawConfigParser()
        config["PATH"] = {"autopacking_dir": "//share/report"}
        config["DWS_JMS"] = {"raw_path": "C:/raw", "name_auto": "A.xlsx", "name_dwspda": "P.xlsx", "name_dws": "D.xlsx"}
        ds.ensure_defaults(config)
        config[ds.SECTION]["path_auto_pda"] = "D:/mine/auto.xlsx"
        config[ds.SECTION]["path_dws3"] = "D:/mine/dws3.xlsx"
        config[ds.SECTION]["path_autopacking_dir"] = "D:/ap"
        with mock.patch("core.dws_mirror.plan", return_value=[]):
            settings = ds.settings_from_config(config)
        # ยังไม่ติ๊กใช้ path แยก -> ใช้ค่าจากหน้า ตั้งค่า ทั้งหมด
        self.assertEqual(settings.auto_pda_file, os.path.join("C:/raw", "A.xlsx"))
        self.assertEqual(settings.autopacking_folder, "//share/report")
        settings.options["custom_paths"] = "true"
        self.assertEqual(settings.auto_pda_file, "D:/mine/auto.xlsx")
        self.assertEqual(settings.dws_pda_file, os.path.join("C:/raw", "P.xlsx"))  # ช่องว่าง = ค่าเดิม
        self.assertEqual(settings.autopacking_folder, "D:/ap")
        # เครื่องอื่นจะไปเช็ก path บน share จริง (ช้ามากตอนต่อเครือข่ายไม่ได้) จึงจำลองไว้
        with mock.patch("core.dws_mirror._source_file", return_value=None):
            self.assertEqual(ds.dws_station_paths(settings)["DWS3"], "D:/mine/dws3.xlsx")

    def test_raw_workbook_has_tables_then_every_data_sheet_and_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _scan_file(os.path.join(tmp, "DWSXAUTOPDA.xlsx"), [
                ("A1", "B1", "2026-10-18 15:10:00", "Autopacking_pda01"),
                ("A2", "B2", "2026-10-18 15:20:00", "Someone Else"),  # ไม่ใช่เครื่องในตาราง
            ])
            _scan_file(os.path.join(tmp, "DWSPDA.xlsx"), [])
            config = configparser.RawConfigParser()
            config["PATH"] = {"output_dir": tmp, "autopacking_dir": os.path.join(tmp, "no-autopacking")}
            config["DWS_JMS"] = {"raw_path": tmp}
            ds.ensure_defaults(config)
            config[ds.SECTION]["sections"] = "auto_pda,pda_bag"
            config[ds.SECTION]["send_raw_file"] = "false"
            config[ds.SECTION]["backup_dir"] = os.path.join(tmp, "backup")
            with mock.patch("core.dws_mirror.plan", return_value=[]):
                settings = ds.settings_from_config(config)
            window = ds.make_window(datetime(2026, 10, 18, 15), datetime(2026, 10, 19, 10))
            with mock.patch.object(ds, "dws_station_paths", return_value={}):
                result = ds.run(settings, window.start, window.end, log=lambda *a, **k: None,
                                send_to_feishu=False, work_dir=os.path.join(tmp, "out"))
            wb = openpyxl.load_workbook(result["raw_file"])
            self.assertEqual(wb.sheetnames, [name for name, _ in ds.TABLE_SHEETS] + ds.DATA_SHEETS)
            self.assertEqual(wb["AUTOPDA"]["A4"].value, "Autopacking_pda01")
            self.assertEqual(wb["AUTO PDA DATA"].max_row, 2)    # เก็บเฉพาะเครื่องในตาราง
            self.assertIsNone(wb["DWS PDA DATA"]["A1"].value)   # ไม่มีข้อมูล = ชีตว่าง
            self.assertIsNone(wb["DWS3"]["A1"].value)
            self.assertEqual(wb["AUTO"].sheet_properties.tabColor.rgb, "FFED7D31")  # แท็บตารางมีสี
            self.assertIsNone(wb["AUTO PDA DATA"].sheet_properties.tabColor)
            # ปุ่มสร้างตัวอย่าง (ไม่ส่ง) ไม่บันทึก
            self.assertFalse(os.path.isdir(os.path.join(tmp, "backup")))
            target = ds.save_excel_copy(result, os.path.join(tmp, "backup"), log=lambda *a, **k: None)
            # บันทึกเฉพาะไฟล์ Excel ลงโฟลเดอร์ตรง ๆ ไม่มีโฟลเดอร์ย่อยรายวัน ไม่มีรูป
            self.assertEqual(os.listdir(os.path.join(tmp, "backup")), [os.path.basename(result["raw_file"])])
            self.assertEqual(os.path.dirname(target), os.path.join(tmp, "backup"))

    def test_post_content_keeps_section_order_and_marks_missing_images(self) -> None:
        sections = [ds.Section("a", "หัวข้อ A", "a.png"), ds.Section("b", "หัวข้อ B", None, "ไม่พบรูป")]
        content = ds.post_content(sections, {"a": "img_a"})
        self.assertEqual(content[0][0]["text"], "หัวข้อ A")
        self.assertEqual(content[1][0], {"tag": "img", "image_key": "img_a"})
        self.assertEqual(content[2][0]["text"], " ")  # เว้นบรรทัดหลังรูป
        self.assertIn("ไม่พบรูป", content[4][0]["text"])

    def test_hourly_abnormal_rows_map_chinese_sort_source_and_dedupe(self) -> None:
        window = ds.make_window(datetime(2026, 10, 18, 15), datetime(2026, 10, 19, 10))
        rows = [
            ["ชั่วโมงข้อมูล", "ไฟล์", "Billcode", "Pipeline", "Sort Time", "Sort Source"],
            ["15点-16点", "16.xlsx", "JT1", "58987-001", datetime(2026, 10, 18, 15, 5), "无分拣计划"],
            ["15点-16点", "16.xlsx", "JT1", "58987-001", datetime(2026, 10, 18, 15, 9), "最大循环件"],
            ["16点-17点", "17.xlsx", "JT2", "58987-002", datetime(2026, 10, 18, 16, 1), "最大循环件"],
        ]
        data = ds.abnormal_from_rows(rows, window)
        self.assertEqual(data.counts, {"No Plan": 1, "Max Cycles": 1})
        self.assertEqual(len(data.rows), 2)

    def test_abnormal_percentages_follow_manual_workbook_formulas(self) -> None:
        values = ds.abnormal_table_values({"No Plan": 604, "Max Cycles": 2892, "Intercept": 5})
        self.assertEqual(values["total"], 3501)
        self.assertEqual(f"{values['No Plan'][1] * 100:.2f}%", "17.40%")
        self.assertEqual(f"{values['Max Cycles'][1] * 100:.2f}%", "82.75%")

    def test_blank_post_title_is_omitted(self) -> None:
        import feishu_client
        sent = {}
        with mock.patch.object(feishu_client, "_post_json",
                               side_effect=lambda url, token, payload, name, **kw: sent.update(payload)):
            feishu_client.send_post("t", "oc", "  ", [[{"tag": "text", "text": "x"}]])
        self.assertNotIn('"title"', sent["content"])
        with mock.patch.object(feishu_client, "_post_json",
                               side_effect=lambda url, token, payload, name, **kw: sent.update(payload)):
            feishu_client.send_post("t", "oc", "สรุป", [])
        self.assertIn('"title": "สรุป"', sent["content"])

    def test_oversized_raw_file_is_zipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "raw.xlsx")
            with open(path, "wb") as fh:
                fh.write(b"0" * 200_000)
            fitted = ds.fit_for_upload(path, 0.1, log=lambda *a, **k: None)
            self.assertTrue(fitted.endswith(".zip"))


if __name__ == "__main__":
    unittest.main()
