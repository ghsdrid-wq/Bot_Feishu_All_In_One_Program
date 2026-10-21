"""Reconcile a live Chinese export through Python and native Power Query."""
from __future__ import annotations

import json
import shutil
import sys
from collections import Counter
from pathlib import Path

import pythoncom
import win32com.client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import realtime_report


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    folder = ROOT / "out/realtime_language_probe"
    raw = folder / "RealtimeDB_CN_raw.xlsx"
    report = folder / "RealtimeDB_CN_report.xlsx"
    header, rows = realtime_report.read_raw(str(raw))
    matrix = realtime_report.build_matrix(header, rows)
    assert sum(sum(v.values()) for v in matrix.values()) == len(rows)
    op, ot = header.index("最新操作类型"), header.index("超时类型")
    expected = Counter((str(row[op]), str(row[ot])) for row in rows)
    shutil.copy2(raw, report)
    realtime_report.build_report(str(report))
    actual_header, actual_rows = realtime_report.read_raw(str(report))
    assert actual_header == header
    normalize = lambda records: [tuple(None if value == "" else value for value in row) for row in records]
    assert normalize(actual_rows) == normalize(rows)
    assert realtime_report.build_matrix(actual_header, actual_rows) == matrix
    formula = (ROOT / "Project-Template/modules/realtime_db_bilingual.pq").read_text(encoding="utf-8")
    excel = target = None
    pythoncom.CoInitialize()
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.EnableEvents = False
        for path in (raw, report):
            target = excel.Workbooks.Add()
            target.Queries.Add("RealtimeTest", formula.replace("C:\\0DWS\\RealtimeDB.xlsx", str(path)))
            sheet = target.Worksheets.Item(1)
            query = sheet.QueryTables.Add('OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;Location=RealtimeTest;Extended Properties=""', sheet.Range("A1"))
            query.CommandType = 2
            query.CommandText = "SELECT * FROM [RealtimeTest]"
            query.Refresh(False)
            result = query.ResultRange.Value
            assert result[0] == (realtime_report.COL_OPERATE, realtime_report.COL_OVERTIME)
            assert Counter(tuple(row) for row in result[1:]) == expected
            print(json.dumps({"source": path.name, "rows": len(result)-1, "python_power_query_parity": True}), flush=True)
            target.Close(False)
            target = None
    finally:
        if target is not None:
            target.Close(False)
        if excel is not None:
            excel.Quit()
        pythoncom.CoUninitialize()
    evidence = {"rows": len(rows), "matrix_total": sum(sum(v.values()) for v in matrix.values()),
                "headers": ["最新操作类型", "超时类型"], "native_raw_and_report_refresh": "passed",
                "category_counts": dict(Counter(str(row[op]) for row in rows))}
    (folder / "verification.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == "__main__":
    main()
