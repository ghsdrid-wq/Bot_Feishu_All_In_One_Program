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
BROKEN_FORMULA_TOKENS = ("#REF!", "#VALUE!", "#NAME?")
FILE_CONTENTS_PATTERN = re.compile(r'File\.Contents\("([^"]+)"\)', re.IGNORECASE)


def iter_values(value):
    if isinstance(value, tuple):
        for item in value:
            yield from iter_values(item)
    else:
        yield value


def validate(path: Path) -> dict:
    excel = None
    workbook = None
    pythoncom.CoInitialize()
    started = time.perf_counter()
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.AskToUpdateLinks = False
        excel.EnableEvents = False
        workbook = excel.Workbooks.Open(
            str(path.resolve()), UpdateLinks=0, ReadOnly=True, IgnoreReadOnlyRecommended=True
        )
        excel.Calculation = XL_CALCULATION_MANUAL

        query_sources = []
        missing_sources = []
        for index in range(1, int(workbook.Queries.Count) + 1):
            query = workbook.Queries.Item(index)
            for source in FILE_CONTENTS_PATTERN.findall(str(query.Formula)):
                query_sources.append(source)
                if not Path(source).is_file():
                    missing_sources.append(source)

        formula_count = 0
        broken_formulas = []
        table_count = 0
        sheet_names = []
        for sheet in workbook.Worksheets:
            sheet_names.append(str(sheet.Name))
            table_count += int(sheet.ListObjects.Count)
            try:
                formula_cells = sheet.UsedRange.SpecialCells(XL_CELL_TYPE_FORMULAS)
            except Exception:
                continue
            for area in formula_cells.Areas:
                for formula in iter_values(area.Formula):
                    if not isinstance(formula, str) or not formula.startswith("="):
                        continue
                    formula_count += 1
                    if any(token in formula.upper() for token in BROKEN_FORMULA_TOKENS):
                        broken_formulas.append({"sheet": str(sheet.Name), "formula": formula})

        return {
            "path": str(path.resolve()),
            "open_seconds": round(time.perf_counter() - started, 3),
            "connections": int(workbook.Connections.Count),
            "queries": int(workbook.Queries.Count),
            "tables": table_count,
            "sheets": sheet_names,
            "formula_count": formula_count,
            "query_sources": query_sources,
            "missing_sources": sorted(set(missing_sources)),
            "broken_formulas": broken_formulas,
        }
    finally:
        if workbook is not None:
            workbook.Close(SaveChanges=False)
        if excel is not None:
            excel.Quit()
        pythoncom.CoUninitialize()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps([validate(path) for path in args.paths], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
