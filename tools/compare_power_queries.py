from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pythoncom
import win32com.client


FILE_CONTENTS_PATTERN = re.compile(r'File\.Contents\("[^"]+"\)', re.IGNORECASE)


def read_queries(path: Path) -> dict[str, str]:
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
            str(path.resolve()), UpdateLinks=0, ReadOnly=True, IgnoreReadOnlyRecommended=True
        )
        return {
            str(workbook.Queries.Item(index).Name): str(workbook.Queries.Item(index).Formula)
            for index in range(1, int(workbook.Queries.Count) + 1)
        }
    finally:
        if workbook is not None:
            workbook.Close(SaveChanges=False)
        if excel is not None:
            excel.Quit()
        pythoncom.CoUninitialize()


def normalize_paths(formula: str) -> str:
    return FILE_CONTENTS_PATTERN.sub('File.Contents("<PATH>")', formula)


def compare(original_path: Path, current_path: Path) -> dict:
    original = read_queries(original_path)
    current = read_queries(current_path)
    common = sorted(set(original) & set(current))
    logic_changes = [name for name in common if normalize_paths(original[name]) != normalize_paths(current[name])]
    raw_changes = [name for name in common if original[name] != current[name]]
    return {
        "original": str(original_path.resolve()),
        "current": str(current_path.resolve()),
        "original_query_count": len(original),
        "current_query_count": len(current),
        "missing_from_current": sorted(set(original) - set(current)),
        "added_to_current": sorted(set(current) - set(original)),
        "raw_changes": raw_changes,
        "path_only_changes": [name for name in raw_changes if name not in logic_changes],
        "logic_changes": logic_changes,
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs=4, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            [compare(args.paths[0], args.paths[1]), compare(args.paths[2], args.paths[3])],
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
