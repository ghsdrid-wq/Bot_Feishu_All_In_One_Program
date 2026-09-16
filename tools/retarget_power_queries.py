from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pythoncom
import win32com.client


FILE_CONTENTS_PATTERN = re.compile(r'File\.Contents\("([^"]+)"\)', re.IGNORECASE)


def find_source(source_root: Path, old_path: str) -> Path:
    filename = Path(old_path).name
    matches = [path for path in source_root.rglob(filename) if path.is_file()]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one local source for {filename}, found {len(matches)}")
    return matches[0].resolve()


def retarget(workbook_path: Path, source_root: Path) -> None:
    excel = None
    workbook = None
    pythoncom.CoInitialize()
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.AskToUpdateLinks = False
        excel.EnableEvents = False
        workbook = excel.Workbooks.Open(
            str(workbook_path.resolve()),
            UpdateLinks=0,
            ReadOnly=False,
            IgnoreReadOnlyRecommended=True,
        )

        changed = 0
        for index in range(1, int(workbook.Queries.Count) + 1):
            query = workbook.Queries.Item(index)
            formula = str(query.Formula)

            def replace(match: re.Match[str]) -> str:
                local_path = find_source(source_root, match.group(1))
                return f'File.Contents("{local_path}")'

            updated = FILE_CONTENTS_PATTERN.sub(replace, formula)
            if updated != formula:
                query.Formula = updated
                changed += 1
                print(f"RETARGET {query.Name}")

        workbook.Save()
        print(f"SAVED {workbook_path} | {changed} query formula(s) changed")
    finally:
        if workbook is not None:
            workbook.Close(SaveChanges=False)
        if excel is not None:
            excel.Quit()
        pythoncom.CoUninitialize()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("workbooks", nargs="+", type=Path)
    args = parser.parse_args()
    for workbook_path in args.workbooks:
        retarget(workbook_path, args.source_root.resolve())


if __name__ == "__main__":
    main()
