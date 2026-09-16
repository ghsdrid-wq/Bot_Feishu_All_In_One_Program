from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pythoncom
import win32com.client


def normalize(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook", type=Path)
    args = parser.parse_args()
    target = normalize(str(args.workbook.resolve()))

    pythoncom.CoInitialize()
    try:
        excel = win32com.client.GetActiveObject("Excel.Application")
        closed = False
        for index in range(int(excel.Workbooks.Count), 0, -1):
            workbook = excel.Workbooks.Item(index)
            if normalize(str(workbook.FullName)) == target:
                workbook.Close(SaveChanges=False)
                closed = True
                print(f"CLOSED {target}")
        if closed and int(excel.Workbooks.Count) == 0:
            excel.Quit()
            print("QUIT EMPTY EXCEL INSTANCE")
        elif not closed:
            print("TARGET WORKBOOK NOT FOUND")
    finally:
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    main()
