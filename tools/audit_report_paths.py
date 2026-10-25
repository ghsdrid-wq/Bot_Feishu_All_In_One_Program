"""Check the exact source paths stored in both operator workbooks, bounded."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.report_sources import template_paths


def probe(path):
    code = "import os,json,sys; sys.stdout.reconfigure(encoding='utf-8');\ntry:\n s=os.stat(sys.argv[1]); print(json.dumps({'readable_path':True,'bytes':s.st_size}))\nexcept OSError as e:\n print(json.dumps({'readable_path':False,'error':str(e)},ensure_ascii=False))"
    try:
        result = subprocess.run(
            [sys.executable, "-c", code, path],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=8,
        )
        return {"path": path, **json.loads(result.stdout)}
    except subprocess.TimeoutExpired:
        return {
            "path": path,
            "readable_path": False,
            "error": "Source access timed out after 8 seconds",
        }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    paths = {}
    for name in ("1AUTOPACKING_BOT.xlsx", "1DWS_&_PDA_v1.8.3 Bot.xlsx"):
        for key, path in template_paths(str(ROOT / "Excel" / name)).items():
            if key.startswith("A") and key[1:].isdigit():
                continue
            paths.setdefault(path, []).append(name + ":" + key)
    # Probe each share folder once; local source files individually.
    probes = {os.path.dirname(p) if p.startswith("\\\\") else p for p in paths}
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(probe, sorted(probes)))
    output = {"workbook_paths": paths, "access_checks": results}
    target = ROOT / "out/workbook_migration/path-access.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for result in results:
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
