"""aggregate.py — ดึงยอดที่ยุบแล้วออกมาเป็นโครงที่ dashboard ใช้ต่อ + CLI

รันได้ตรงๆ:
    python -m metrics.aggregate ingest --autopack-dir "C:/0DWS/AutoPacking"
    python -m metrics.aggregate report --date 2026-09-16
    python -m metrics.aggregate health
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Optional

if __package__ in (None, ""):                    # ให้รันเป็นไฟล์เดี่ยวได้ด้วย
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from metrics import (core, ingest_autopacking, ingest_dws, ingest_dws_db,
                         ingest_jms)
else:
    from . import core, ingest_autopacking, ingest_dws, ingest_dws_db, ingest_jms

# แหล่งข้อมูลพื้นฐาน — ใช้เป็นการ์ดบนหน้าภาพรวม (ห้ามมีแท็บรวมปนในนี้
# ไม่งั้นการ์ดจะนับยอดซ้ำ)
SOURCE_TABS = [
    ("AUTOPACK", "AutoPacking",   "ชิ้น"),
    ("DWS",      "DWS 1–11",      "ชิ้น"),
    ("PDA_DWS",  "PDA ลงรถ",      "ชิ้น"),
    ("PDA_AUTO", "PDA บรรจุมือ",  "ชิ้น"),
    ("BAG",      "กระสอบที่ใช้",  "กระสอบ"),
]

# แท็บที่แสดงจริง — บางแท็บรวมหลายแหล่งเข้าด้วยกัน
#
# DWS_ALL: พัสดุที่ผ่านเครื่อง DWS ปกติเครื่องจะรับเอง แต่บางครั้งปิดโปรแกรมสแกน
# แล้วใช้ PDA ยิงแทน ยอดจึงไปตกอยู่คนละแหล่ง — ต้องดูรวมกันถึงจะเห็นยอดจริง
TABS = [
    {"key": "AUTOPACK", "title": "AutoPacking",     "unit": "ชิ้น",    "sources": ["AUTOPACK"]},
    {"key": "DWS",      "title": "DWS 1–11",        "unit": "ชิ้น",    "sources": ["DWS"]},
    {"key": "PDA_DWS",  "title": "PDA ลงรถ",        "unit": "ชิ้น",    "sources": ["PDA_DWS"]},
    {"key": "DWS_ALL",  "title": "DWS + PDA ลงรถ",  "unit": "ชิ้น",    "sources": ["DWS", "PDA_DWS"]},
    {"key": "PDA_AUTO", "title": "PDA บรรจุมือ",    "unit": "ชิ้น",    "sources": ["PDA_AUTO"]},
    {"key": "BAG",      "title": "กระสอบที่ใช้",    "unit": "กระสอบ",  "sources": ["BAG"]},
]


# =====================================================================
# สร้างโครงรายงาน
# =====================================================================

def build_report(conn, business_date: str, source: str | list[str] = "AUTOPACK") -> dict:
    """คืน dict ที่ template ใช้ render ได้ตรงๆ — ตารางยอด + KPI + error

    source รับได้ทั้งชื่อเดียวและหลายชื่อ (แท็บรวม เช่น DWS + PDA ลงรถ)
    เวลารวมหลายแหล่ง จะเรียงตามลำดับที่ส่งมา แล้วค่อยเรียงตาม sort_order ในแหล่งนั้น
    """
    cfg = core.load_config()
    bd_cfg = cfg["business_day"]
    hours = core.hour_sequence(bd_cfg["start_hour"])

    sources = [source] if isinstance(source, str) else list(source)
    min_qty = min_qty_for(cfg, sources[0])
    marks = ",".join("?" for _ in sources)
    rank = {name: idx for idx, name in enumerate(sources)}

    stations = conn.execute(
        f"SELECT source, station, line, display_name, in_service, sort_order "
        f"FROM dim_station WHERE source IN ({marks}) AND active = 1",
        sources,
    ).fetchall()
    stations = sorted(stations, key=lambda r: (rank[r["source"]], r["sort_order"]))

    facts = conn.execute(
        f"SELECT source, station, hour_start, shift, qty, status, online_minutes "
        f"FROM fact_hourly WHERE business_date = ? AND source IN ({marks})",
        [business_date, *sources],
    ).fetchall()

    by_station: dict[str, dict[int, Any]] = {}
    for row in facts:
        key = f"{row['source']}|{row['station']}"
        by_station.setdefault(key, {})[row["hour_start"]] = row

    rows = []
    for st in stations:
        cells, total, effective, peak = [], 0, 0, 0
        shift_totals = {"A": 0, "B": 0}
        # ชั่วโมงทำงานจริงต้องแยกกะด้วย — รายงานเดิมมี Avg/Hr ของกะ A กับกะ B
        # คนละช่อง ซึ่งหารด้วยชั่วโมงของกะนั้นๆ ไม่ใช่ชั่วโมงรวมทั้งวัน
        shift_effective = {"A": 0, "B": 0}
        shift_peak = {"A": 0, "B": 0}
        downtime_hours = 0

        for hour in hours:
            fact = by_station.get(f"{st['source']}|{st['station']}", {}).get(hour)
            if fact is None:
                cells.append({"hour": hour, "value": None, "status": "no_data"})
                continue

            qty = fact["qty"]
            status = fact["status"]
            cells.append({
                "hour": hour,
                "value": qty if status != "no_data" else None,
                "status": status,
            })
            if status == "no_data":
                continue

            shift = fact["shift"]
            total += qty
            peak = max(peak, qty)
            shift_totals[shift] += qty
            shift_peak[shift] = max(shift_peak[shift], qty)
            if status == "downtime":
                downtime_hours += 1
            # threshold: ชั่วโมงที่ยอดจิ๋ว (เครื่องเปิด/ปิดกลางชั่วโมง) ไม่นับเป็น
            # ชั่วโมงทำงาน ไม่งั้น Avg จะถูกหารด้วยชั่วโมงที่ไม่ได้ทำงานจริง
            if qty >= min_qty:
                effective += 1
                shift_effective[shift] += 1

        rows.append({
            "station": st["station"],
            "source": st["source"],
            "line": st["line"],
            "display_name": st["display_name"],
            # เครื่องที่ยังไม่ได้ใช้งาน: แสดงในตารางเป็น '-' แต่ไม่นับเป็นของเสีย
            "in_service": bool(st["in_service"]),
            "cells": cells,
            "total": total,
            "effective_hours": effective,
            "avg_per_hour": round(total / effective, 1) if effective else 0,
            "peak": peak,
            "shift_a": shift_totals["A"],
            "shift_b": shift_totals["B"],
            "eff_a": shift_effective["A"],
            "eff_b": shift_effective["B"],
            "avg_a": round(shift_totals["A"] / shift_effective["A"], 1) if shift_effective["A"] else 0,
            "avg_b": round(shift_totals["B"] / shift_effective["B"], 1) if shift_effective["B"] else 0,
            "peak_a": shift_peak["A"],
            "peak_b": shift_peak["B"],
            "downtime_hours": downtime_hours,
        })

    errors = conn.execute(
        f"SELECT station, error_type, hour_start, SUM(qty) AS qty "
        f"FROM fact_error WHERE business_date = ? AND source IN ({marks}) "
        f"GROUP BY station, error_type, hour_start",
        [business_date, *sources],
    ).fetchall()

    error_by_port: dict[str, dict] = {}
    for row in errors:
        entry = error_by_port.setdefault(
            row["station"], {"station": row["station"], "total": 0, "by_hour": {}, "by_type": {}}
        )
        entry["total"] += row["qty"]
        entry["by_hour"][row["hour_start"]] = entry["by_hour"].get(row["hour_start"], 0) + row["qty"]
        entry["by_type"][row["error_type"]] = entry["by_type"].get(row["error_type"], 0) + row["qty"]

    # ---- แถวสรุปรายสาย (AP1 / AP2) — รายงานเดิมมีบรรทัด 合计 ของแต่ละสาย ----
    line_totals = []
    for line in sorted({r["line"] for r in rows}):
        group = [r for r in rows if r["line"] == line]
        gt = sum(r["total"] for r in group)
        ge = sum(r["effective_hours"] for r in group)
        line_totals.append({
            "line": line,
            "total": gt,
            "shift_a": sum(r["shift_a"] for r in group),
            "shift_b": sum(r["shift_b"] for r in group),
            "effective_hours": ge,
            "avg_per_hour": round(gt / ge, 1) if ge else 0,
            "peak": max((r["peak"] for r in group), default=0),
            "stations": len(group),
            "active": sum(1 for r in group if r["total"] > 0),
        })

    grand_total = sum(r["total"] for r in rows)
    error_total = sum(e["total"] for e in error_by_port.values())
    active_rows = [r for r in rows if r["total"] > 0]
    # ตัวหารของ "จุดที่เดิน" ต้องไม่รวมเครื่องที่ยังไม่ได้ใช้งาน
    # ไม่งั้น DWS ที่ยังไม่เปิดใช้ 4 เครื่องจะทำให้ตัวเลขดูเหมือนมีปัญหาตลอด
    expected_rows = [r for r in rows if r["in_service"]]
    idle_rows = [r["display_name"] for r in expected_rows if r["total"] == 0]

    return {
        "business_date": business_date,
        "source": sources[0] if len(sources) == 1 else "+".join(sources),
        "sources": sources,
        "hours": hours,
        "rows": rows,
        "kpi": {
            "total_qty": grand_total,
            "shift_a": sum(r["shift_a"] for r in rows),
            "shift_b": sum(r["shift_b"] for r in rows),
            "error_qty": error_total,
            "error_rate_pct": round(error_total / grand_total * 100, 2) if grand_total else 0,
            "active_stations": len(active_rows),
            "expected_stations": len(expected_rows),
            "total_stations": len(rows),
            "not_in_service": len(rows) - len(expected_rows),
            "idle_stations": idle_rows,
            "avg_per_station": round(grand_total / len(active_rows)) if active_rows else 0,
            "best_station": max(active_rows, key=lambda r: r["total"])["display_name"] if active_rows else "-",
            "best_qty": max((r["total"] for r in active_rows), default=0),
            "downtime_hours": sum(r["downtime_hours"] for r in rows),
            # สรุปรายกะ — "ชั่วโมงรวม" คือผลรวมชั่วโมงทำงานจริงของทุกจุดในกะนั้น
            # ส่วน "เฉลี่ย" คือยอดของกะหารด้วยชั่วโมงรวม = ยอดต่อเครื่องต่อชั่วโมง
            "shift_a_hours": sum(r["eff_a"] for r in rows),
            "shift_b_hours": sum(r["eff_b"] for r in rows),
            "shift_a_avg": _safe_div(sum(r["shift_a"] for r in rows),
                                     sum(r["eff_a"] for r in rows)),
            "shift_b_avg": _safe_div(sum(r["shift_b"] for r in rows),
                                     sum(r["eff_b"] for r in rows)),
            "best_a": _best(rows, "shift_a", "avg_a"),
            "best_b": _best(rows, "shift_b", "avg_b"),
        },
        "line_totals": line_totals,
        "errors": sorted(error_by_port.values(), key=lambda e: -e["total"]),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def min_qty_for(cfg: dict, key: str) -> int:
    """เกณฑ์ "ชั่วโมงทำงานจริง" ของแหล่งนั้น — หน่วยต่างกันจึงใช้ค่าเดียวกันไม่ได้"""
    target = (cfg.get("targets") or {}).get(key) or {}
    return int(target.get("min_qty", cfg["effective_hour"]["min_qty"]))


def _safe_div(total: int, hours: int) -> float:
    return round(total / hours, 1) if hours else 0


def _best(rows: list[dict], qty_key: str, avg_key: str) -> dict:
    """จุดที่ทำได้มากสุดของกะนั้น — รายงานเดิมโชว์แยกกะ A กับ B"""
    candidates = [r for r in rows if r[qty_key] > 0]
    if not candidates:
        return {"station": "-", "qty": 0, "avg": 0}
    best = max(candidates, key=lambda r: r[qty_key])
    return {"station": best["display_name"], "qty": best[qty_key], "avg": best[avg_key]}


def build_overview(conn, business_date: str) -> dict:
    """หน้าหลัก — ยอดของทุกแหล่งในหน้าเดียว พร้อมรายชั่วโมงไว้วาด sparkline

    แต่ละแหล่งนับคนละหน่วย (ชิ้น / กระสอบ) และคนละงาน จึงห้ามเอามาบวกรวมกัน
    เป็น 'ยอดรวมทั้งโรงงาน' — จะเป็นตัวเลขที่ไม่มีความหมาย
    """
    cfg = core.load_config()
    hours = core.hour_sequence(cfg["business_day"]["start_hour"])
    sources = []

    for key, title, unit in SOURCE_TABS:
        report = build_report(conn, business_date, key)
        kpi = report["kpi"]
        per_hour = []
        for idx in range(len(hours)):
            per_hour.append(sum((r["cells"][idx]["value"] or 0) for r in report["rows"]))
        top = sorted((r for r in report["rows"] if r["total"] > 0),
                     key=lambda r: -r["total"])[:5]
        sources.append({
            "key": key, "title": title, "unit": unit,
            "total": kpi["total_qty"],
            "shift_a": kpi["shift_a"], "shift_b": kpi["shift_b"],
            "error_qty": kpi["error_qty"], "error_rate_pct": kpi["error_rate_pct"],
            "active": kpi["active_stations"], "expected": kpi["expected_stations"],
            "worked_hours": sum(r["effective_hours"] for r in report["rows"]),
            "not_in_service": kpi["not_in_service"],
            "best_station": kpi["best_station"], "best_qty": kpi["best_qty"],
            "per_hour": per_hour,
            "top": [{"name": r["display_name"], "source": r["source"],
                     "total": r["total"]} for r in top],
            "has_data": kpi["total_qty"] > 0,
        })

    return {
        "business_date": business_date,
        "hours": hours,
        "sources": sources,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def machine_health(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM machine_health ORDER BY station"
    ).fetchall()
    return [dict(r) for r in rows]


# =====================================================================
# CLI
# =====================================================================

def cmd_ingest(args) -> int:
    conn = core.connect(args.db)
    summary: dict[str, Any] = {}

    if not args.skip_autopack:
        summary["autopack"] = ingest_autopacking.ingest_folder(conn, args.autopack_dir)
    if not args.skip_dws:
        summary["dws"] = ingest_dws.ingest_all(conn, args.date)
        summary["dws_db"] = ingest_dws_db.ingest(conn, args.date)
    if not args.skip_jms:
        summary["jms"] = ingest_jms.ingest_all(conn, args.date, args.jms_dir)

    for name, result in summary.items():
        print(f"[{name}] rows={result['rows']}")
        for warn in result.get("warnings", []):
            print(f"   ! {warn}")
        if name == "jms":
            for detail in result["details"]:
                print(f"   {detail['source']:9} qty={detail.get('qty', 0):>9,}  "
                      f"จุด={detail.get('stations', 0)}")
        if name == "dws":
            mark = {"online": "OK   ", "idle": "IDLE ", "stale": "STALE",
                    "offline": "DOWN ", "disabled": "-    "}
            for detail in result["details"]:
                print(f"   {mark.get(detail['health'], '?')} {detail['station']:8} "
                      f"qty={detail.get('qty', 0):>7,} {detail['detail']}")
            if result["problems"]:
                print(f"   ! เครื่องที่ควรเดินแต่มีปัญหา: {', '.join(result['problems'])}")
    conn.close()
    return 0


def cmd_report(args) -> int:
    conn = core.connect(args.db)
    report = build_report(conn, args.date, args.source)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        kpi = report["kpi"]
        print(f"=== {report['source']} {report['business_date']} ===")
        print(f"  ยอดรวม        {kpi['total_qty']:>10,}")
        print(f"  กะ A / กะ B   {kpi['shift_a']:>10,} / {kpi['shift_b']:,}")
        print(f"  Error         {kpi['error_qty']:>10,}  ({kpi['error_rate_pct']}%)")
        print(f"  จุดที่เดิน     {kpi['active_stations']}/{kpi['expected_stations']}"
              + (f"   (ยังไม่ได้ใช้งาน {kpi['not_in_service']} จุด)"
                 if kpi["not_in_service"] else ""))
        print(f"  เฉลี่ย/จุด     {kpi['avg_per_station']:>10,}")
        print(f"  สูงสุด        {kpi['best_station']} = {kpi['best_qty']:,}")
        print()
        for row in report["rows"]:
            if row["total"] == 0:
                continue
            print(f"  {row['display_name']:>4}  total={row['total']:>8,}  "
                  f"eff={row['effective_hours']:>2}h  avg={row['avg_per_hour']:>8,.1f}  "
                  f"peak={row['peak']:>6,}")
    conn.close()
    return 0


def cmd_overview(args) -> int:
    conn = core.connect(args.db)
    data = build_overview(conn, args.date)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"=== สรุปทุกแหล่ง {data['business_date']} ===")
        print(f"  {'แหล่ง':<14}{'ยอดรวม':>12}{'กะ A':>11}{'กะ B':>11}{'จุด':>9}")
        for src in data["sources"]:
            print(f"  {src['title']:<14}{src['total']:>12,}{src['shift_a']:>11,}"
                  f"{src['shift_b']:>11,}{src['active']:>6}/{src['expected']:<3}"
                  f"  {src['unit']}")
    conn.close()
    return 0


def cmd_health(args) -> int:
    conn = core.connect(args.db)
    for row in machine_health(conn):
        print(f"{row['health']:>8}  {row['station']:8}  "
              f"mtime={row['file_mtime'] or '-':19}  "
              f"last_data={row['last_data_time'] or '-':19}  {row['detail']}")
    conn.close()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="metrics store สำหรับ DWS/AutoPacking dashboard")
    parser.add_argument("--db", default=core.DEFAULT_DB_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    p_ing = sub.add_parser("ingest", help="ดึง raw เข้า store")
    p_ing.add_argument("--autopack-dir")
    p_ing.add_argument("--date", help="จำกัดเฉพาะวันรอบงานนี้ (YYYY-MM-DD)")
    p_ing.add_argument("--jms-dir", help="โฟลเดอร์ที่มี DWSXAUTOPDA.xlsx / DWSPDA.xlsx")
    p_ing.add_argument("--skip-autopack", action="store_true")
    p_ing.add_argument("--skip-dws", action="store_true")
    p_ing.add_argument("--skip-jms", action="store_true")
    p_ing.set_defaults(func=cmd_ingest)

    p_ove = sub.add_parser("overview", help="สรุปทุกแหล่งของวัน")
    p_ove.add_argument("--date", required=True)
    p_ove.add_argument("--json", action="store_true")
    p_ove.set_defaults(func=cmd_overview)

    p_rep = sub.add_parser("report", help="สรุปยอดของวัน")
    p_rep.add_argument("--date", required=True)
    p_rep.add_argument("--source", default="AUTOPACK")
    p_rep.add_argument("--json", action="store_true")
    p_rep.set_defaults(func=cmd_report)

    p_hea = sub.add_parser("health", help="สถานะเครื่อง DWS")
    p_hea.set_defaults(func=cmd_health)

    core.use_utf8_console()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
