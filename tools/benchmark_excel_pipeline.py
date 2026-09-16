from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import pythoncom
import win32com.client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from Createphoto import refresh_workbook_queries, wait_excel


XL_CALCULATION_MANUAL = -4135
REPORT_RANGES = {
    "Autoformat": "A1:AK39",
    "DWSREALTIME": "A1:AE16",
    "AUTO PDA": "A1:AC30",
    "DWS PDA": "A1:AC15",
    "REALTIME DB": "A1:I10",
}


def log(message: str) -> None:
    print(message, flush=True)


def flatten(value):
    if isinstance(value, tuple):
        for item in value:
            yield from flatten(item)
    else:
        yield value


def report_snapshot(workbook) -> dict:
    sheet_names = {str(sheet.Name): sheet for sheet in workbook.Worksheets}
    reports = {}
    for sheet_name, address in REPORT_RANGES.items():
        sheet = sheet_names.get(sheet_name)
        if sheet is None:
            continue
        values = sheet.Range(address).Value2
        normalized = json.dumps(values, ensure_ascii=False, default=str, separators=(",", ":"))
        errors = sorted(
            {
                value
                for value in flatten(values)
                if isinstance(value, str) and value.startswith("#")
            }
        )
        numeric_sum = sum(
            float(value)
            for value in flatten(values)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        )
        reports[sheet_name] = {
            "range": address,
            "sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            "numeric_sum": round(numeric_sum, 6),
            "errors": errors,
        }
    return reports


def table_rows(workbook) -> dict:
    rows = {}
    for sheet in workbook.Worksheets:
        for table in sheet.ListObjects:
            body = table.DataBodyRange
            rows[f"{sheet.Name}/{table.Name}"] = int(body.Rows.Count) if body is not None else 0
    return rows


def benchmark(path: Path, save_refreshed: bool, result_path: Path | None, batch_size: int | None = None) -> dict:
    excel = None
    workbook = None
    pythoncom.CoInitialize()
    started = time.perf_counter()
    result = {"path": str(path.resolve())}
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.AskToUpdateLinks = False
        excel.EnableEvents = False

        phase = time.perf_counter()
        workbook = excel.Workbooks.Open(
            str(path.resolve()), UpdateLinks=0, ReadOnly=False, IgnoreReadOnlyRecommended=True
        )
        result["open_seconds"] = round(time.perf_counter() - phase, 3)
        excel.Calculation = XL_CALCULATION_MANUAL
        excel.ScreenUpdating = False
        log(f"OPEN {path.name} {result['open_seconds']}s")

        phase = time.perf_counter()
        refresh_kwargs = {"batch_size": batch_size} if batch_size is not None else {}
        refresh_workbook_queries(workbook, excel, lambda: True, log, **refresh_kwargs)
        result["refresh_seconds"] = round(time.perf_counter() - phase, 3)
        log(f"REFRESH_DONE {result['refresh_seconds']}s")

        phase = time.perf_counter()
        log("CALCULATE_START")
        excel.CalculateFull()
        wait_excel(excel, lambda: True, log, timeout=20 * 60)
        result["calculate_seconds"] = round(time.perf_counter() - phase, 3)
        log(f"CALCULATE_DONE {result['calculate_seconds']}s")

        result["table_rows"] = table_rows(workbook)
        result["reports"] = report_snapshot(workbook)

        if save_refreshed:
            phase = time.perf_counter()
            workbook.Save()
            result["save_seconds"] = round(time.perf_counter() - phase, 3)
            log(f"SAVE_DONE {result['save_seconds']}s")

        result["total_seconds"] = round(time.perf_counter() - started, 3)
        if result_path is not None:
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("RESULT " + json.dumps(result, ensure_ascii=False), flush=True)
        return result
    finally:
        if workbook is not None:
            workbook.Close(SaveChanges=False)
        if excel is not None:
            excel.Quit()
        pythoncom.CoUninitialize()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--save-refreshed", action="store_true")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--batch-size", type=int)
    args = parser.parse_args()
    benchmark(args.workbook, args.save_refreshed, args.result, args.batch_size)


if __name__ == "__main__":
    main()
