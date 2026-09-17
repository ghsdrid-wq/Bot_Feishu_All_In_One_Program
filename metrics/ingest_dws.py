"""ingest_dws.py — อ่าน raw ของเครื่อง DWS1-8 จาก network share ของแต่ละเครื่อง

สถานะเครื่องใช้ 2 สัญญาณ ไม่ใช่แค่ mtime เพราะเจอเคสจริงว่า
dws2.xlsx ถูกเขียนใหม่ตอน 05:00 ของวันนี้ (mtime สด) แต่ข้อมูลข้างในหยุดที่
21:29 ของเมื่อวาน — คือตัว exporter ยังเดิน แต่ตัวเครื่องไม่ได้ยิงพัสดุ

  health = 'offline'  เปิดไฟล์ไม่ได้ / เครื่องไม่อยู่ในเน็ตเวิร์ก  -> ตารางโชว์ '-'
  health = 'stale'    เปิดได้ แต่ exporter ไม่เขียนมานาน           -> ตารางโชว์ '-'
  health = 'idle'     exporter เขียนอยู่ แต่ไม่มีพัสดุใหม่          -> ตารางโชว์ 0
  health = 'online'   ปกติ
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Any, Optional

import requests
from python_calamine import CalamineWorkbook

from . import core

# ตำแหน่งคอลัมน์ในไฟล์ราคา raw ของเครื่อง DWS (12 คอลัมน์)
# 单号 taskNo 重量 长度 宽度 高度 体积 出秤时间 分拣口 分拣状态 上传状态 异常详情
COL_WEIGHT = 2
COL_SCAN_TIME = 7
COL_CHUTE = 8
COL_SORT_STATE = 9


def _resolve_path(spec: dict) -> Optional[str]:
    """เครื่องบางตัวมี path สำรอง (เช่น DWS_07 ที่ย้ายโฟลเดอร์) — ลองตามลำดับ"""
    for key in ("path", "fallback_path"):
        path = spec.get(key)
        if path and os.path.isfile(path):
            return path
    return None


def probe_machine(station: str, spec: dict) -> dict:
    """เช็กสถานะเครื่อง 1 ตัว โดยยังไม่อ่านข้อมูลทั้งไฟล์"""
    cfg = core.load_config()["dws"]
    now = datetime.now()

    # เครื่องที่ยังไม่ได้ใช้งาน — ข้ามการแตะ share ไปเลย
    # DWS_01 ที่ host ไม่ตอบ ping ใช้เวลา timeout หลายวินาทีทุกรอบโดยเปล่าประโยชน์
    if not spec.get("in_service", True):
        return {
            "station": station, "path": spec.get("path"), "reachable": 0,
            "file_mtime": None, "last_data_time": None, "row_count": None,
            "health": "disabled", "detail": "ยังไม่ได้ใช้งาน",
            "checked_at": now.isoformat(timespec="seconds"),
        }

    path = _resolve_path(spec)

    if path is None:
        return {
            "station": station, "path": spec.get("path"), "reachable": 0,
            "file_mtime": None, "last_data_time": None, "row_count": None,
            "health": "offline", "detail": "เปิด share ไม่ได้ หรือไฟล์ไม่มีอยู่",
            "checked_at": now.isoformat(timespec="seconds"),
        }

    mtime = datetime.fromtimestamp(os.path.getmtime(path))
    file_age_min = (now - mtime).total_seconds() / 60
    if file_age_min > cfg["stale_file_minutes"]:
        return {
            "station": station, "path": path, "reachable": 1,
            "file_mtime": mtime.isoformat(timespec="seconds"),
            "last_data_time": None, "row_count": None,
            "health": "stale",
            "detail": f"ไฟล์ไม่ถูกเขียนใหม่มา {file_age_min / 60:.1f} ชั่วโมง",
            "checked_at": now.isoformat(timespec="seconds"),
        }

    return {
        "station": station, "path": path, "reachable": 1,
        "file_mtime": mtime.isoformat(timespec="seconds"),
        "last_data_time": None, "row_count": None,
        "health": "online", "detail": "",
        "checked_at": now.isoformat(timespec="seconds"),
    }


def _read_rows(path: str) -> list[list[Any]]:
    """ก๊อปมาไว้เครื่องก่อนแล้วค่อยอ่าน — อ่านตรงจาก share ระหว่างที่เครื่องปลายทาง
    กำลังเขียนไฟล์ทับ มีโอกาสได้ไฟล์ครึ่งๆ กลางๆ"""
    sheet = core.load_config()["dws"]["sheet"]
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        local = tmp.name
    try:
        shutil.copy2(path, local)
        wb = CalamineWorkbook.from_path(local)
        name = sheet if sheet in wb.sheet_names else wb.sheet_names[0]
        return wb.get_sheet_by_name(name).to_python(skip_empty_area=False)
    finally:
        try:
            os.remove(local)
        except OSError:
            pass


def _ingest_via_api(conn, station: str, spec: dict, cfg_all: dict,
                    business_date: Optional[str]) -> dict:
    """อ่านยอดจาก agent แทนไฟล์ share

    ได้ยอดที่ยุบแล้วมาเลย ไม่ต้องก๊อปไฟล์ข้ามเน็ตเวิร์กและไม่ต้องรอรอบ export
    ช่วงเวลาเป็นแบบปลายไม่รวม (ฝั่ง agent ใช้ >= start AND < end)
    """
    cfg = cfg_all["dws"]
    bd_cfg = cfg_all["business_day"]
    start_hour = bd_cfg["start_hour"]
    now = datetime.now()

    day = (date.fromisoformat(business_date) if business_date
           else core.business_date_of(now, start_hour))
    begin = datetime.combine(day, dtime(hour=start_hour))
    finish = begin + timedelta(days=1)

    base = (spec.get("api_url") or "").rstrip("/")
    if not base:
        return {"station": station, "health": "offline", "rows": 0,
                "detail": "ตั้ง source เป็น api แต่ไม่ได้ใส่ api_url"}

    try:
        response = requests.get(
            f"{base}/hourly",
            params={"start": begin.strftime("%Y-%m-%d %H:%M:%S"),
                    "end": finish.strftime("%Y-%m-%d %H:%M:%S")},
            timeout=cfg.get("api_timeout", 20))
        payload = response.json()
    except Exception as exc:
        health = {"station": station, "path": base, "reachable": 0,
                  "file_mtime": None, "last_data_time": None, "row_count": None,
                  "health": "offline", "detail": f"เรียก API ไม่สำเร็จ: {exc}",
                  "checked_at": now.isoformat(timespec="seconds")}
        _save_health(conn, health)
        return {"station": station, "health": "offline", "rows": 0,
                "detail": health["detail"]}

    if not payload.get("success"):
        detail = f"agent ตอบว่าไม่สำเร็จ: {payload.get('message') or payload.get('detail')}"
        _save_health(conn, {"station": station, "path": base, "reachable": 1,
                            "file_mtime": None, "last_data_time": None, "row_count": None,
                            "health": "offline", "detail": detail,
                            "checked_at": now.isoformat(timespec="seconds")})
        return {"station": station, "health": "offline", "rows": 0, "detail": detail}

    api_rows = payload.get("rows") or []
    hourly_rows, error_rows = [], []
    last_dt = None
    for item in api_rows:
        bd = core.business_date_of(
            datetime.strptime(f"{item['date']} {item['hour']:02d}:00:00",
                              "%Y-%m-%d %H:%M:%S"), start_hour).isoformat()
        if business_date and bd != business_date:
            continue
        hour = int(item["hour"])
        shift = core.shift_of(hour, bd_cfg["shift_a_start"], bd_cfg["shift_b_start"])
        hourly_rows.append((bd, shift, hour, "DWS", "DWS", station,
                            int(item["qty"]), int(item["ok_qty"]),
                            round(float(item["weight_kg"]), 2), None,
                            "ok" if item["qty"] > 0 else "zero"))
        # ชื่อชนิด error ใช้ชุดเดียวกับฝั่งไฟล์ ยอดจึงรวมกันได้ไม่แตก
        for qty, label in ((item.get("reflow_qty", 0), "回流"),
                           (item.get("unknown_qty", 0), "未知")):
            if qty:
                error_rows.append((bd, shift, hour, "DWS", "DWS", station, label, int(qty)))
        if item.get("last_at"):
            stamp = datetime.strptime(item["last_at"], "%Y-%m-%d %H:%M:%S")
            last_dt = stamp if last_dt is None or stamp > last_dt else last_dt

    health = {"station": station, "path": base, "reachable": 1,
              "file_mtime": None,
              "last_data_time": last_dt.isoformat(timespec="seconds") if last_dt else None,
              "row_count": sum(r[6] for r in hourly_rows),
              "health": "online", "detail": "อ่านผ่าน API",
              "checked_at": now.isoformat(timespec="seconds")}
    if last_dt is not None:
        idle_min = (now - last_dt).total_seconds() / 60
        if idle_min > cfg["idle_data_minutes"]:
            health["health"] = "idle"
            health["detail"] = f"ไม่มีพัสดุใหม่มา {idle_min / 60:.1f} ชั่วโมง (API ปกติ)"
    elif not hourly_rows:
        health["health"] = "idle"
        health["detail"] = "API ปกติ แต่ยังไม่มีพัสดุในรอบนี้"
    _save_health(conn, health)

    core.upsert_hourly(conn, hourly_rows)
    core.upsert_errors(conn, error_rows)
    conn.commit()
    return {"station": station, "health": health["health"], "rows": len(hourly_rows),
            "qty": sum(r[6] for r in hourly_rows), "detail": health["detail"]}


def ingest_machine(conn, station: str, spec: dict,
                   business_date: Optional[str] = None) -> dict:
    cfg_all = core.load_config()
    cfg = cfg_all["dws"]
    bd_cfg = cfg_all["business_day"]
    start_hour = bd_cfg["start_hour"]
    now = datetime.now()

    if not spec.get("in_service", True):
        health = probe_machine(station, spec)
        _save_health(conn, health)
        return {"station": station, "health": "disabled", "rows": 0,
                "detail": health["detail"]}

    if (spec.get("source") or "file").strip().lower() == "api":
        return _ingest_via_api(conn, station, spec, cfg_all, business_date)

    health = probe_machine(station, spec)

    if health["health"] in ("disabled", "offline", "stale"):
        _save_health(conn, health)
        # ไม่เขียน fact เลย -> query จะ LEFT JOIN ไม่เจอ แล้วแสดงเป็น '-'
        return {"station": station, "health": health["health"],
                "rows": 0, "detail": health["detail"]}

    rows = _read_rows(health["path"])

    # (business_date, hour) -> [qty, valid, weight]
    buckets: dict[tuple[str, int], list] = defaultdict(lambda: [0, 0, 0.0])
    errors: dict[tuple[str, int, str], int] = defaultdict(int)
    last_dt: Optional[datetime] = None
    unparsed = 0

    for row in rows[1:]:
        # เดิมกรองด้วย "ช่องแรก (หมายเลขพัสดุ) ว่าง = ข้าม" ซึ่งผิด
        # พัสดุที่อ่านบาร์โค้ดไม่ออกจะมีหมายเลขว่างแต่เป็นของจริงที่ชั่งแล้ว
        # และตกช่อง -1 แบบไหลกลับ — เป็น error ที่ต้องเห็นที่สุด ไม่ใช่ทิ้ง
        # (เทียบกับ API ของ agent แล้วต่างกัน 1 แถวเพราะเรื่องนี้)
        # เกณฑ์ใหม่: ต้องมีเวลาสแกนเท่านั้น ถึงจะนับเป็นแถวข้อมูล
        if not row:
            continue
        dt = core.parse_datetime(row[COL_SCAN_TIME] if len(row) > COL_SCAN_TIME else None)
        if dt is None:
            unparsed += 1
            continue
        last_dt = dt if last_dt is None or dt > last_dt else last_dt

        bd = core.business_date_of(dt, start_hour).isoformat()
        if business_date and bd != business_date:
            continue

        key = (bd, dt.hour)
        state = str(row[COL_SORT_STATE]).strip() if len(row) > COL_SORT_STATE and row[COL_SORT_STATE] else ""

        buckets[key][0] += 1
        if state == cfg["success_state"]:
            buckets[key][1] += 1
        buckets[key][2] += core.to_float(row[COL_WEIGHT] if len(row) > COL_WEIGHT else 0)

        if state in cfg["error_states"]:
            errors[(bd, dt.hour, state)] += 1

    # ---- exporter ยังเขียนอยู่ แต่เครื่องไม่ได้ยิงพัสดุ = idle ----
    if last_dt is not None:
        health["last_data_time"] = last_dt.isoformat(timespec="seconds")
        idle_min = (now - last_dt).total_seconds() / 60
        if idle_min > cfg["idle_data_minutes"]:
            health["health"] = "idle"
            health["detail"] = f"ไม่มีพัสดุใหม่มา {idle_min / 60:.1f} ชั่วโมง (ไฟล์ยังอัปเดตปกติ)"
    else:
        health["health"] = "idle"
        health["detail"] = "ไฟล์ว่าง ไม่มีข้อมูลพัสดุ"
    health["row_count"] = max(len(rows) - 1, 0)
    if unparsed:
        health["detail"] = (health["detail"] + f" | อ่านเวลาไม่ออก {unparsed} แถว").strip(" |")
    _save_health(conn, health)

    hourly_rows = [
        (bd, core.shift_of(hour, bd_cfg["shift_a_start"], bd_cfg["shift_b_start"]),
         hour, "DWS", "DWS", station,
         qty, valid, round(weight, 2), None, "ok" if qty > 0 else "zero")
        for (bd, hour), (qty, valid, weight) in buckets.items()
    ]
    error_rows = [
        (bd, core.shift_of(hour, bd_cfg["shift_a_start"], bd_cfg["shift_b_start"]),
         hour, "DWS", "DWS", station, state, count)
        for (bd, hour, state), count in errors.items()
    ]

    core.upsert_hourly(conn, hourly_rows)
    core.upsert_errors(conn, error_rows)
    conn.commit()

    return {
        "station": station,
        "health": health["health"],
        "rows": len(hourly_rows),
        "qty": sum(b[0] for b in buckets.values()),
        "detail": health["detail"],
    }


def _save_health(conn, health: dict) -> None:
    conn.execute(
        """
        INSERT INTO machine_health (station, path, reachable, file_mtime,
                                    last_data_time, row_count, health, detail, checked_at)
        VALUES (:station,:path,:reachable,:file_mtime,:last_data_time,
                :row_count,:health,:detail,:checked_at)
        ON CONFLICT (station) DO UPDATE SET
            path = excluded.path, reachable = excluded.reachable,
            file_mtime = excluded.file_mtime, last_data_time = excluded.last_data_time,
            row_count = excluded.row_count, health = excluded.health,
            detail = excluded.detail, checked_at = excluded.checked_at
        """,
        health,
    )
    conn.commit()


def ingest_all(conn, business_date: Optional[str] = None) -> dict:
    cfg = core.load_config()["dws"]
    results = []
    for station, spec in cfg["machines"].items():
        try:
            results.append(ingest_machine(conn, station, spec, business_date))
        except Exception as exc:                  # เครื่องเดียวพังไม่ควรล้มทั้งรอบ
            results.append({"station": station, "health": "offline",
                            "rows": 0, "detail": f"อ่านไม่สำเร็จ: {exc}"})
    # นับเฉพาะเครื่องที่ควรเดินจริง — เครื่องที่ยังไม่ได้ใช้งานไม่ใช่ของเสีย
    in_service = [r for r in results if r["health"] != "disabled"]
    return {
        "source": "DWS",
        "machines": len(results),
        "in_service": len(in_service),
        "online": sum(1 for r in in_service if r["health"] == "online"),
        "problems": [r["station"] for r in in_service
                     if r["health"] in ("offline", "stale")],
        "rows": sum(r["rows"] for r in results),
        "details": results,
    }
