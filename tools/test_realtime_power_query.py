"""Exercise the bilingual M query in native Excel on disposable local files."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pythoncom
import win32com.client

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    output = ROOT / "out/realtime_language_probe"
    output.mkdir(parents=True, exist_ok=True)
    formula = (ROOT / "Project-Template/modules/realtime_db_bilingual.pq").read_text(encoding="utf-8")
    excel = None
    opened = []
    pythoncom.CoInitialize()
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.EnableEvents = False
        for case, chinese, summary in [("CN_raw", True, False), ("TH_raw", False, False), ("CN_report", True, True), ("TH_report", False, True)]:
            source = excel.Workbooks.Add()
            opened.append(source)
            sheet = source.Worksheets.Item(1)
            sheet.Name = "DATA"
            # Reversed columns prove selection is by name, not position.
            headers = ("超时类型", "unused", "最新操作类型") if chinese else ("ประเภทเกินเวลา", "unused", "ประเภทการดำเนินงานล่าสุด")
            sheet.Range("A1:C3").Value = (headers, ("Exceed 4 hours with no track", "x", "Arrive scan"), ("Exceed 12 hours with no track", "y", "Packing scan"))
            if summary:
                table = source.Worksheets.Add(Before=sheet)
                table.Name = "TABLE"
                table.Range("A1").Value = "Ignore this summary"
            path = output / (case + ".xlsx")
            source.SaveAs(str(path), FileFormat=51)
            source.Close(False)
            opened.pop()
            target = excel.Workbooks.Add()
            opened.append(target)
            m = formula.replace("C:\\0DWS\\RealtimeDB.xlsx", str(path))
            target.Queries.Add("RealtimeTest", m)
            connection = 'OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;Location=RealtimeTest;Extended Properties=""'
            query = target.Worksheets.Item(1).QueryTables.Add(connection, target.Worksheets.Item(1).Range("A1"))
            query.CommandType = 2
            query.CommandText = "SELECT * FROM [RealtimeTest]"
            query.Refresh(False)
            values = target.Worksheets.Item(1).Range("A1:B3").Value
            expected = (("ประเภทการดำเนินงานล่าสุด", "ประเภทเกินเวลา"), ("Arrive scan", "Exceed 4 hours with no track"), ("Packing scan", "Exceed 12 hours with no track"))
            if values != expected:
                raise AssertionError(f"Unexpected Power Query result in {case}: {values!r}")
            target.SaveAs(str(output / (case + "_query.xlsx")), FileFormat=51)
            target.Close(False)
            opened.pop()
            print(json.dumps({"case": case, "rows": 2, "native_refresh": "passed"}), flush=True)
    finally:
        for workbook in reversed(opened):
            workbook.Close(False)
        if excel is not None:
            excel.Quit()
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    main()
