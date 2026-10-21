"""Verify a Chinese JMS Realtime export without sending any Feishu message."""
from __future__ import annotations

import ast
import configparser
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import openpyxl
import requests

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    config = configparser.ConfigParser(interpolation=None)
    config.read(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "config.ini", encoding="utf-8-sig")
    token = next((config.get(section, key, fallback="").strip()
                  for section, key in [("FEISHU", "JMS_TOKEN"), ("FEISHU", "AUTH_TOKEN"),
                                       ("DWS_JMS", "jms_token")]
                  if config.get(section, key, fallback="").strip()), "")
    if not token:
        raise RuntimeError("No configured JMS token")
    method = next(n for n in ast.walk(ast.parse((ROOT / "bot_main.py").read_text(encoding="utf-8")))
                  if isinstance(n, ast.FunctionDef) and n.name == "_run_realtime_db")
    payload = next(ast.literal_eval(n.value) for n in ast.walk(method)
                   if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "payload" for t in n.targets))
    headers = {"authtoken": token, "lang": "CN", "langtype": "CN",
               "routename": "TrackRealTimeMonitoringDB", "timezone": "GMT+0700",
               "origin": "https://jms.jtexpress.co.th", "referer": "https://jms.jtexpress.co.th/"}
    base = "https://jmsgw.jtexpress.co.th/businessindicator/bigdataReport/"
    started = datetime.now() - timedelta(seconds=5)
    with requests.Session() as session:
        response = session.post(base + "pageExcelByTask/trail_monitor_detail_doris", headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        print(json.dumps({k: result.get(k) for k in ("code", "succ", "msg")}, ensure_ascii=False), flush=True)
        if not result.get("succ"):
            return
        for _ in range(60):
            time.sleep(3)
            response = session.post(base + "report/file/list", headers=headers, json={"current": 1, "size": 50}, timeout=60)
            response.raise_for_status()
            for row in response.json().get("data", {}).get("list", []):
                if row.get("business") != "trail_monitor_detail_doris" or str(row.get("status")) != "2":
                    continue
                if str(row.get("createTime", "")) < started.strftime("%Y-%m-%d %H:%M:%S"):
                    continue
                url = row.get("downUrl")
                if not url:
                    download = row.get("downloadResult") or {}
                    if isinstance(download, str):
                        download = json.loads(download)
                    url = download.get("data")
                if not url:
                    continue
                data = session.get(url, timeout=120)
                data.raise_for_status()
                path = ROOT / "out" / "realtime_language_probe" / "RealtimeDB_CN_raw.xlsx"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data.content)
                book = openpyxl.load_workbook(path, read_only=True, data_only=True)
                sheet = book.worksheets[0]
                sheet.reset_dimensions()
                rows = sheet.iter_rows(values_only=True)
                header = next(rows)
                print("HEADERS", json.dumps(header, ensure_ascii=False), flush=True)
                # Category values only: no parcel IDs, employee names, or tokens.
                values = {i: set() for i, value in enumerate(header) if value in (
                    "最新操作类型", "超时类型", "ประเภทการดำเนินงานล่าสุด", "ประเภทเกินเวลา")}
                for row in rows:
                    for i in values:
                        if i < len(row):
                            values[i].add(str(row[i]))
                print("CATEGORIES", json.dumps({header[i]: sorted(v) for i, v in values.items()}, ensure_ascii=False))
                book.close()
                print("SAVED", path)
                return
        raise TimeoutError("Chinese Realtime export was not ready within 180 seconds")


if __name__ == "__main__":
    main()
