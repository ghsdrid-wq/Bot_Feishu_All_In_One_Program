"""pipeline.py — งานหนึ่งรอบของ dashboard: ดึง raw -> ยุบยอด -> แคป PNG

แยกออกมาเป็นโมดูลเดียวเพื่อให้ bot_main.py เรียกได้บรรทัดเดียว
ไม่ต้องไปรู้เรื่อง metrics/dashboard ข้างใน

หลักการสำคัญ: **รอบนี้ต้องไม่ทำให้ pipeline เดิมล้ม**
ทุกขั้นตอนจับ exception ไว้เอง แล้วคืน summary ให้ผู้เรียกไปเขียน log
เพราะยอดที่ส่งเข้า Feishu ทุกวันนี้สำคัญกว่า dashboard ที่เพิ่งเพิ่มเข้ามา
"""

from __future__ import annotations

import configparser
import os
import sys
from datetime import datetime
from typing import Callable, Optional

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metrics import (core, ingest_autopacking, ingest_dws, ingest_dws_db,
                     ingest_jms)

LogFunc = Callable[..., None]


def _noop(message: str, level: str = "INFO") -> None:
    print(f"[{level}] {message}")


def _settings() -> dict:
    parser = configparser.RawConfigParser()
    parser.read(os.path.join(core.PROJECT_ROOT, "config.ini"), encoding="utf-8")
    section = parser["DASHBOARD"] if "DASHBOARD" in parser else {}

    def flag(key: str, default: bool) -> bool:
        return str(section.get(key, str(default))).strip().lower() in ("1", "true", "yes", "on")

    return {
        "enabled": flag("enabled", True),
        "render_png": flag("render_png", True),
        # รูปที่ส่งเข้าแชทโชว์ทุกจุด รวมจุดที่ไม่มียอดด้วย
        # จุดที่ไม่มียอดคือข้อมูลเหมือนกัน — บอกว่าเครื่องนั้นไม่ได้เดินทั้งรอบ
        # ซ่อนไปแล้วคนดูรูปจะไม่รู้ว่ามีจุดนั้นอยู่
        "hide_empty_png": flag("hide_empty_png", False),
        # รูปที่ส่งเข้าแชทเอาเฉพาะตาราง — ยอดรวมกับกราฟอยู่บนตัวการ์ด Feishu
        # อยู่แล้ว แนบทั้งหน้ามาอีกทำให้แชทยาวจนเลื่อนหาอย่างอื่นไม่เจอ
        "tables_only_png": flag("tables_only_png", True),
        "png_dir": section.get("png_dir", "").strip()
                   or os.path.join(core.PROJECT_ROOT, "out"),
        # โฟลเดอร์ raw ของ JMS ใช้ตัวเดียวกับ [DWS_JMS] raw_path ของโปรแกรมหลัก
        "jms_dir": (parser["DWS_JMS"].get("raw_path", "C:/0DWS").strip()
                    if "DWS_JMS" in parser else "C:/0DWS"),
    }


def current_business_date() -> str:
    start_hour = core.load_config()["business_day"]["start_hour"]
    return core.business_date_of(datetime.now(), start_hour).isoformat()


def run_cycle(log: Optional[LogFunc] = None,
              business_date: Optional[str] = None,
              db_path: str = core.DEFAULT_DB_PATH,
              should_stop: Optional[Callable[[], bool]] = None) -> dict:
    """หนึ่งรอบเต็ม — เรียกได้จาก scheduler ของ bot_main"""
    write = log or _noop
    cfg = _settings()
    if not cfg["enabled"]:
        return {"skipped": "ปิดไว้ใน config.ini [DASHBOARD] enabled"}

    business_date = business_date or current_business_date()
    started = datetime.now()
    summary: dict = {"business_date": business_date, "warnings": [], "rows": 0}

    conn = core.connect(db_path)
    try:
        ap_dir = core.load_config()["autopacking"]["raw_dir"]
        steps = [
            ("AutoPacking", lambda: ingest_autopacking.ingest_folder(conn, ap_dir)),
            ("DWS1-8", lambda: ingest_dws.ingest_all(conn, business_date)),
            ("DWS9-11", lambda: ingest_dws_db.ingest(conn, business_date)),
            # ไม่ส่ง raw_dir แล้ว — แต่ละไฟล์มีพาธเต็มของตัวเองใน metrics_config.yaml
            ("JMS", lambda: ingest_jms.ingest_all(conn, business_date)),
        ]
        for name, func in steps:
            try:
                result = func()
            except Exception as exc:          # แหล่งเดียวพังไม่ควรล้มทั้งรอบ
                summary["warnings"].append(f"{name}: {exc}")
                write(f"Dashboard ingest {name} failed — {exc}", level="WARN")
                continue
            summary["rows"] += result.get("rows", 0)
            summary["warnings"].extend(result.get("warnings", []))
            write(f"Dashboard ingest {name}: {result.get('rows', 0)} rows")

        # ลบข้อมูลเก่าเกินเพดาน — ทำหลัง ingest เพื่อให้วันปัจจุบันถูกเขียนก่อนเสมอ
        try:
            retention = core.load_config().get("retention") or {}
            if str(retention.get("prune_enabled", True)).lower() not in ("false", "0", "no"):
                pruned = core.prune_old_data(conn)
                total = sum(pruned["removed"].values())
                if total:
                    write(f"ลบข้อมูลก่อน {pruned['cutoff']} ออก {total:,} แถว "
                          f"(เก็บย้อนหลัง {pruned['keep_days']} วัน)")
        except Exception as exc:
            summary["warnings"].append(f"ลบข้อมูลเก่าไม่สำเร็จ: {exc}")

        if not os.path.isdir(ap_dir):
            summary["warnings"].append(
                f"ไม่พบโฟลเดอร์ raw ของ AutoPacking: {ap_dir} "
                f"(แก้ที่ metrics_config.yaml -> autopacking.raw_dir)")
    finally:
        conn.close()

    # แคปหน้าเว็บเป็น PNG
    # ไม่ต้องตัดสินใจเรื่อง "ทำโหมดไหนบ้าง" ตรงนี้ — bot_main.run_process เป็นคนคุม
    # ลำดับ Excel -> Dashboard -> Feishu อยู่แล้ว ที่นี่ทำหน้าที่เดียวคือของโหมดนี้
    if cfg["render_png"]:
        try:
            from dashboard import render
            paths = render.render_all_tabs(
                business_date, cfg["png_dir"], db_path,
                hide_empty=cfg["hide_empty_png"],
                tables_only=cfg["tables_only_png"])
            summary["png"] = paths
            write(f"Dashboard PNG: {len(paths)} ใบ -> {cfg['png_dir']}")
        except Exception as exc:
            summary["warnings"].append(f"สร้างรูปไม่สำเร็จ: {exc}")
            write(f"Dashboard render failed — {exc}", level="WARN")

    summary["seconds"] = round((datetime.now() - started).total_seconds(), 1)
    for warn in summary["warnings"]:
        write(f"Dashboard: {warn}", level="WARN")
    write(f"Dashboard cycle done — {summary['rows']} rows, {summary['seconds']}s",
          level="SUCCESS")
    return summary


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="รัน dashboard หนึ่งรอบ")
    parser.add_argument("--date", help="วันรอบงาน (ปล่อยว่าง = รอบปัจจุบัน)")
    parser.add_argument("--db", default=core.DEFAULT_DB_PATH)
    core.use_utf8_console()
    args = parser.parse_args()
    run_cycle(business_date=args.date, db_path=args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
