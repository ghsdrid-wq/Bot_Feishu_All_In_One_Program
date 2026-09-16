from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import pythoncom
import win32com.client


XL_CALCULATION_MANUAL = -4135
XL_CELL_TYPE_FORMULAS = -4123


def normalize_com_matrix(value):
    if isinstance(value, tuple):
        for row in value:
            if isinstance(row, tuple):
                for cell in row:
                    yield cell
            else:
                yield row
    elif value is not None:
        yield value


def probe_workbook(
    path: Path,
    include_query_formulas: bool = False,
    formula_search: str | None = None,
) -> dict:
    started = time.perf_counter()
    excel = None
    workbook = None
    result = {"path": str(path), "size_bytes": path.stat().st_size}

    pythoncom.CoInitialize()
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.AskToUpdateLinks = False
        excel.EnableEvents = False
        open_started = time.perf_counter()
        workbook = excel.Workbooks.Open(
            str(path), UpdateLinks=0, ReadOnly=True, IgnoreReadOnlyRecommended=True
        )
        excel.Calculation = XL_CALCULATION_MANUAL
        result["open_seconds"] = round(time.perf_counter() - open_started, 3)
        result["connections"] = int(workbook.Connections.Count)

        queries = []
        for index in range(1, int(workbook.Queries.Count) + 1):
            query = workbook.Queries.Item(index)
            formula = str(query.Formula)
            sources = re.findall(r'File\.Contents\("([^"]+)"\)', formula)
            query_data = {"name": str(query.Name), "sources": sources}
            if include_query_formulas:
                query_data["formula"] = formula
            queries.append(query_data)
        result["queries"] = queries
        result["formula_matches"] = []

        sheets = []
        for sheet in workbook.Worksheets:
            used = sheet.UsedRange
            sheet_data = {
                "name": str(sheet.Name),
                "used_rows": int(used.Rows.Count),
                "used_columns": int(used.Columns.Count),
                "tables": [],
                "formula_count": 0,
                "whole_column_formula_count": 0,
                "formula_samples": [],
            }

            for table in sheet.ListObjects:
                body = table.DataBodyRange
                headers = [str(table.ListColumns.Item(i).Name) for i in range(1, int(table.ListColumns.Count) + 1)]
                sheet_data["tables"].append(
                    {
                        "name": str(table.Name),
                        "rows": int(body.Rows.Count) if body is not None else 0,
                        "columns": int(table.ListColumns.Count),
                        "headers": headers,
                    }
                )

            try:
                formula_cells = used.SpecialCells(XL_CELL_TYPE_FORMULAS)
            except Exception:
                formula_cells = None

            if formula_cells is not None:
                if formula_search:
                    for cell in formula_cells.Cells:
                        formula = str(cell.Formula)
                        if formula_search.lower() in formula.lower():
                            result["formula_matches"].append(
                                {"sheet": str(sheet.Name), "address": str(cell.Address), "formula": formula}
                            )
                for area in formula_cells.Areas:
                    for formula in normalize_com_matrix(area.Formula):
                        if not isinstance(formula, str) or not formula.startswith("="):
                            continue
                        sheet_data["formula_count"] += 1
                        if re.search(r"(?<![A-Z0-9_])\$?[A-Z]{1,3}:\$?[A-Z]{1,3}(?![A-Z0-9_])", formula, re.I):
                            sheet_data["whole_column_formula_count"] += 1
                        if len(sheet_data["formula_samples"]) < 4:
                            sheet_data["formula_samples"].append(formula)

            sheets.append(sheet_data)

        result["sheets"] = sheets
        result["total_seconds"] = round(time.perf_counter() - started, 3)
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
    parser.add_argument("--include-query-formulas", action="store_true")
    parser.add_argument("--formula-search")
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            [
                probe_workbook(path.resolve(), args.include_query_formulas, args.formula_search)
                for path in args.paths
            ],
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
