"""Generate synthetic extended reports, compare baseline cells and optionally open in Excel."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import openpyxl

from core.report_window import ReportWindow
from report_workbooks import calculate
from tools.test_report_workbooks import input_fixtures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-excel", action="store_true")
    args = parser.parse_args()
    folder = ROOT / "out" / "extended-report"
    folder.mkdir(parents=True, exist_ok=True)
    paths = input_fixtures(folder)
    evidence = {"synthetic": True, "baseline_numeric_cells": 0, "reports": []}
    files = ["1AUTOPACKING_BOT.xlsx", "1DWS_&_PDA_v1.8.3 Bot.xlsx"]
    for filename in files:
        end = datetime(2026, 10, 21, 12)
        window = ReportWindow(datetime(2026, 10, 20, 15), end, end)
        template = str(ROOT / "Excel" / filename)
        calculate(
            template,
            str(folder / ("legacy-" + filename)),
            window.start.date(),
            end,
            paths,
            start_hour=15,
            log=lambda _: None,
        )
        result = calculate(
            template,
            str(folder / filename),
            window.start.date(),
            end,
            paths,
            start_hour=15,
            window=window,
            log=lambda _: None,
        )
        old = openpyxl.load_workbook(folder / ("legacy-" + filename), data_only=True)
        new = openpyxl.load_workbook(folder / filename, data_only=True)
        for name, layout in result["layouts"].items():
            for row in old[name].iter_rows(max_row=layout["end_row"]):
                for cell in row:
                    if cell.column in (3, 4, 5) or not isinstance(
                        cell.value, (float, int)
                    ):
                        continue
                    other = (
                        new[name]
                        .cell(
                            cell.row, layout["column_map"].get(cell.column, cell.column)
                        )
                        .value
                    )
                    assert (
                        isinstance(other, (float, int))
                        and abs(other - cell.value) < 1e-6
                    ), (name, cell.coordinate, cell.value, other)
                    evidence["baseline_numeric_cells"] += 1
        old.close()
        new.close()
        end = datetime(2026, 10, 21, 14)
        window = ReportWindow(window.start, end, end)
        result = calculate(
            template,
            str(folder / filename),
            window.start.date(),
            end,
            paths,
            start_hour=15,
            window=window,
            log=lambda _: None,
        )
        evidence["reports"].append(result)
    if args.native_excel:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.DisplayAlerts = False
        excel.AskToUpdateLinks = False
        excel.EnableEvents = False
        try:
            for filename in files:
                book = excel.Workbooks.Open(
                    str(folder / filename), UpdateLinks=0, ReadOnly=True
                )
                try:
                    assert book.Queries.Count == 0 and book.Connections.Count == 0
                finally:
                    book.Close(False)
            evidence["native_excel_open"] = True
        finally:
            excel.Quit()
            pythoncom.CoUninitialize()
    (folder / "verification.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "baseline_numeric_cells": evidence["baseline_numeric_cells"],
                "native_excel_open": evidence.get("native_excel_open", False),
            }
        )
    )


if __name__ == "__main__":
    main()
