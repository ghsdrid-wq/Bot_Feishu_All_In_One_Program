"""Compare every calculated template formula with native Excel, using fixtures."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import openpyxl
import pythoncom
import win32com.client
from openpyxl.utils.datetime import to_excel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from report_workbooks import calculate
from tools.test_report_workbooks import input_fixtures


def load_table(sheet, name, headers, rows):
    sheet.ListObjects.Item(name).Unlist()
    area = sheet.Range(sheet.Cells(1, 1), sheet.Cells(len(rows) + 1, len(headers)))
    area.NumberFormat = "@"
    area.Value = tuple([tuple(headers)] + [tuple(row) for row in rows])
    table = sheet.ListObjects.Add(SourceType=1, Source=area, XlListObjectHasHeaders=1)
    table.Name = name


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    folder = ROOT / "out/workbook_migration"
    folder.mkdir(parents=True, exist_ok=True)
    fixtures = folder / "fixtures"
    fixtures.mkdir(exist_ok=True)
    paths = input_fixtures(fixtures)
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-hour", type=int, default=12)
    parser.add_argument(
        "--now", type=datetime.fromisoformat, default=datetime(2026, 10, 21, 10)
    )
    args = parser.parse_args()
    day = date(2026, 10, 20)
    now = args.now
    evidence = []
    excel = reference = None
    pythoncom.CoInitialize()
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.AskToUpdateLinks = False
        excel.EnableEvents = False
        for name in ("1AUTOPACKING_BOT.xlsx", "1DWS_&_PDA_v1.8.3 Bot.xlsx"):
            template = ROOT / "Excel" / name
            output = folder / name
            started = time.perf_counter()
            result = calculate(
                str(template),
                str(output),
                day,
                now,
                paths,
                log=lambda _: None,
                start_hour=args.start_hour,
            )
            elapsed = time.perf_counter() - started
            refpath = folder / ("reference-" + name)
            shutil.copy2(template, refpath)
            original = openpyxl.load_workbook(template)
            actual = openpyxl.load_workbook(output, data_only=True)
            reference = excel.Workbooks.Open(
                str(refpath), UpdateLinks=0, ReadOnly=False
            )
            excel.Calculation = -4135
            if "Autoformat" in original:
                reference.Worksheets.Item("Autoformat").Range("A2").Value = to_excel(
                    datetime.combine(day, datetime.min.time())
                )
                for hour in range(24):
                    sheet = reference.Worksheets.Item(f"{hour:02d}")
                    data_hour = (hour - 1) % 24
                    slot = datetime.combine(
                        day + timedelta(days=data_hour < 12), datetime.min.time()
                    ).replace(hour=data_hour)
                    active = (
                        slot
                        >= datetime.combine(day, datetime.min.time()).replace(
                            hour=args.start_hour
                        )
                        and slot + timedelta(hours=1) <= now
                    )
                    for row in range(2, 14):
                        sheet.Range(f"M{row}").Value = 201 if row == 2 and active else 0
                        sheet.Range(f"N{row}").Value = 199 if row == 2 and active else 0
                    sheet.Range("O2").Value = 2 if active else 0
                    sheet.Range("P2").Value = 0
            else:
                reference.Worksheets.Item("DWSREALTIME").Range("A2").Value = to_excel(
                    datetime.combine(day, datetime.min.time())
                )
                for i in range(1, 9):
                    sheet = reference.Worksheets.Item(f"DWS{i}")
                    sheet.Range("H2:H202").NumberFormat = "@"
                    sheet.Range("H2:H202").Value = tuple(
                        ("2026/10/20 15:05:00",) for _ in range(201)
                    )
                sheet = reference.Worksheets.Item("DWS9-11")
                sheet.Range("A2:A203").NumberFormat = "@"
                sheet.Range("A2:A203").Value = tuple(
                    ("2026-10-20 15:05:00",) for _ in range(202)
                )
                sheet.Range("E2:E203").Value = tuple(
                    ("10.30.32.220",) for _ in range(202)
                )
                rows = [
                    ["", "2026-10-20 15:05:00", "Autopacking_pda01"] for _ in range(203)
                ]
                rows += [["", "2026-10-20 15:05:00", "Dws_pda01"] for _ in range(204)]
                rows += [["B1", "2026-10-20 15:05:00", "Dws_pda01"]]
                load_table(
                    reference.Worksheets.Item("AUTO PDA DATA"),
                    "AUTO_PDA_DATA",
                    ["BAG", "SCAN_TIME", "SCANNER"],
                    rows,
                )
                load_table(
                    reference.Worksheets.Item("DWS PDA DATA"),
                    "DWS_PDA_DATA",
                    ["BAG", "SCAN_TIME", "SCANNER"],
                    rows[:-1],
                )
                load_table(
                    reference.Worksheets.Item("REALTIME DB DATA"),
                    "REALTIME_DB",
                    ["ประเภทการดำเนินงานล่าสุด", "ประเภทเกินเวลา"],
                    [
                        ["Arrive scan", "Exceed 4 hours with no track"],
                        ["Packing scan", "Exceed 12 hours with no track"],
                    ],
                )
                reference.Worksheets.Item("REALTIME DB DATA").Range("C2:C3").Value = (
                    (4,),
                    (12,),
                )
            formulas = []
            for sheet_name, control in (
                ("Autoformat", 33),
                ("DWSREALTIME", 27),
                ("AUTO PDA", 51),
            ):
                if sheet_name in original:
                    for hour in range(12, args.start_hour):
                        reference.Worksheets.Item(sheet_name).Cells(
                            control, 3 + hour - 12
                        ).ClearContents()
            for sheet in original:
                native = reference.Worksheets.Item(sheet.title)
                for row in sheet:
                    for cell in row:
                        if cell.data_type != "f":
                            continue
                        formulas.append((sheet.title, cell.coordinate))
                        if isinstance(cell.value, str):
                            formula = cell.value.replace(
                                "NOW()", str(to_excel(now))
                            ).replace(
                                "TODAY()",
                                str(
                                    to_excel(
                                        now.replace(
                                            hour=0, minute=0, second=0, microsecond=0
                                        )
                                    )
                                ),
                            )
                            try:
                                native.Range(cell.coordinate).Formula = formula
                            except Exception as error:
                                raise RuntimeError(
                                    f"Excel oracle rejected {sheet.title}!{cell.coordinate}: {formula}"
                                ) from error
            excel.CalculateFull()
            assert result["formulas_replaced"] == len(formulas)
            differences = []
            native_values = {
                s.title: reference.Worksheets.Item(s.title).UsedRange.Value
                for s in original
            }
            for sheet, address in formulas:
                cell = actual[sheet][address]
                # Templates' UsedRange starts at A1.
                expected = native_values[sheet][cell.row - 1][cell.column - 1]
                value = cell.value
                if hasattr(expected, "year"):
                    expected = to_excel(expected.replace(tzinfo=None))
                if hasattr(value, "year"):
                    value = to_excel(value.replace(tzinfo=None))
                equal = (
                    math.isclose(
                        float(expected), float(value), rel_tol=1e-10, abs_tol=1e-9
                    )
                    if isinstance(expected, (int, float))
                    and isinstance(value, (int, float))
                    else expected == value
                )
                if not equal:
                    differences.append(
                        {
                            "sheet": sheet,
                            "cell": address,
                            "excel": expected,
                            "python": value,
                        }
                    )
            check = {
                "file": name,
                "start_hour": args.start_hour,
                "now": now.isoformat(),
                "formulas_compared": len(formulas),
                "differences": differences,
                "python_seconds": round(elapsed, 3),
            }
            evidence.append(check)
            print(
                json.dumps(
                    {**check, "differences": differences[:8]},
                    ensure_ascii=False,
                    default=str,
                ),
                flush=True,
            )
            generated = excel.Workbooks.Open(str(output), UpdateLinks=0, ReadOnly=True)
            try:
                if int(generated.Connections.Count) or int(generated.Queries.Count):
                    raise AssertionError(
                        "Generated workbook retained a refresh dependency"
                    )
                for sheet in original:
                    if sheet.title not in (
                        "Autoformat",
                        "DWSREALTIME",
                        "AUTO PDA",
                        "REALTIME DB",
                    ):
                        continue
                    formula_cells = [
                        c for row in sheet for c in row if c.data_type == "f"
                    ]
                    for cell in formula_cells:
                        if str(
                            generated.Worksheets.Item(sheet.title)
                            .Range(cell.coordinate)
                            .Formula
                        ).startswith("="):
                            raise AssertionError(
                                "Generated workbook retained a formula"
                            )
            finally:
                generated.Close(False)
            print("NATIVE_VALUES_ONLY_OPEN", name, "passed", flush=True)
            reference.Close(False)
            reference = None
            original.close()
            actual.close()
        (folder / f"parity-{args.start_hour}-{now.hour}.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        if any(e["differences"] for e in evidence):
            raise AssertionError("Template formulas differ from native Excel")
    finally:
        if reference is not None:
            reference.Close(False)
        if excel is not None:
            excel.Quit()
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    main()
