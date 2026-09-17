"""ingest_autopacking.py — อ่านไฟล์รายชั่วโมง 00.xlsx..23.xlsx ของ AutoPacking
แล้วยุบเป็นยอดลง fact_hourly / fact_error

กติกาสำคัญ 2 ข้อที่พิสูจน์จากข้อมูลจริงแล้ว:
  1. ไฟล์ NN.xlsx เก็บข้อมูลของชั่วโมง NN-1 (NN คือเวลาที่ export ไม่ใช่เวลาข้อมูล)
     00.xlsx จึงเป็น 23:00 ของเมื่อวาน
  2. ชีต Throughput ไม่มีคอลัมน์เวลาเลย -> ชั่วโมงต้องมาจากชื่อไฟล์เท่านั้น
     แต่ชีต Abnormal มี Sort Time จริง -> เอามาตรวจทานกติกาข้อ 1 ทุกรอบ
     ถ้าไม่ตรงให้ขึ้น warning ไม่ใช่เงียบๆ ใส่ผิดช่อง
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional

from python_calamine import CalamineWorkbook

from . import core


def _hour_from_filename(name: str, offset: int) -> Optional[int]:
    stem = os.path.splitext(os.path.basename(name))[0]
    if not stem.isdigit():
        return None
    value = int(stem)
    if not 0 <= value <= 23:
        return None
    return (value + offset) % 24


def _read_sheet(path: str, sheet: str) -> list[list[Any]]:
    wb = CalamineWorkbook.from_path(path)
    if sheet not in wb.sheet_names:
        return []
    return wb.get_sheet_by_name(sheet).to_python(skip_empty_area=False)


def ingest_file(conn, path: str, business_date_hint: Optional[str] = None) -> dict:
    """อ่าน 1 ไฟล์รายชั่วโมง -> เขียนลง DB, คืน summary"""
    cfg = core.load_config()
    ap = cfg["autopacking"]
    bd_cfg = cfg["business_day"]
    start_hour = bd_cfg["start_hour"]

    warnings: list[str] = []
    file_hour = _hour_from_filename(path, ap["file_hour_offset"])
    if file_hour is None:
        return {"path": path, "rows": 0, "skipped": "ชื่อไฟล์ไม่ใช่ 00-23", "warnings": []}

    abnormal = _read_sheet(path, ap["sheet_abnormal"])
    throughput = _read_sheet(path, ap["sheet_throughput"])

    # โปรแกรม auto export รีเซ็ตไฟล์ทั้ง 24 ไฟล์เป็นเทมเพลตว่างหลัง 13:00
    # เพื่อรอรอบวันถัดไป — ไฟล์ว่างแปลว่า "ยังไม่มีข้อมูล" ไม่ใช่ "ยอดเป็นศูนย์"
    # ถ้าเขียนลงไปจะไปทับยอดของวันก่อนที่เก็บไว้แล้วจนกลายเป็น 0 ทั้งวัน
    has_throughput = any(r and r[0] not in (None, "") for r in throughput[1:])
    has_abnormal = any(r and r[0] not in (None, "") for r in abnormal[1:])
    if not has_throughput and not has_abnormal:
        return {"path": os.path.basename(path), "rows": 0, "errors": 0,
                "skipped": "ไฟล์ว่าง (ถูกรีเซ็ตรอรอบถัดไป)", "warnings": []}

    # ---- หาเวลาจริงจาก Abnormal เพื่อยืนยันชั่วโมง + วันรอบงาน ----
    stamps = []
    for row in abnormal[1:]:
        if len(row) > 4:
            dt = core.parse_datetime(row[4])
            if dt:
                stamps.append(dt)

    if stamps:
        data_hour = max(set(dt.hour for dt in stamps), key=[d.hour for d in stamps].count)
        anchor = max(stamps)
        if data_hour != file_hour:
            warnings.append(
                f"ชั่วโมงจากชื่อไฟล์ ({file_hour:02d}) ไม่ตรงกับข้อมูลข้างใน ({data_hour:02d}) "
                f"— ใช้ค่าจากข้อมูลจริง"
            )
            file_hour = data_hour
    else:
        # ไฟล์ว่าง (เช่น 06-14.xlsx ที่เป็น template เปล่า) — เดาวันจาก mtime
        anchor = datetime.fromtimestamp(os.path.getmtime(path))
        anchor = anchor.replace(minute=0, second=0, microsecond=0)
        # ถอยกลับไปยังชั่วโมงของข้อมูลจริง
        while anchor.hour != file_hour:
            anchor -= timedelta(hours=1)

    business_date = (
        business_date_hint
        or core.business_date_of(anchor, start_hour).isoformat()
    )
    shift = core.shift_of(file_hour, bd_cfg["shift_a_start"], bd_cfg["shift_b_start"])

    # ---- Throughput -> ยอดต่อ feeder ----
    hourly_rows: list[tuple] = []
    seen_stations: set[str] = set()

    for row in throughput[1:]:
        if not row or row[0] in (None, ""):
            continue
        pipeline = str(row[0]).strip()
        spec = ap["pipelines"].get(pipeline)
        if spec is None:
            warnings.append(f"ไม่รู้จัก Pipeline '{pipeline}' — ข้ามแถว")
            continue
        feeder = core.to_int(row[1])
        if feeder <= 0:
            continue

        line = spec["line"]
        station = f"{line}-{feeder:02d}"
        qty = core.to_int(row[2])
        valid = core.to_int(row[3], qty)
        online = core.parse_online_minutes(row[6] if len(row) > 6 else None)
        remark = str(row[7]).strip() if len(row) > 7 and row[7] else ""

        # 无关机时间 = ไม่มีเวลาหยุดเครื่อง ; อย่างอื่นถือว่ามี downtime
        has_downtime = bool(remark) and remark != "无关机时间"
        if qty > 0:
            status = "downtime" if has_downtime else "ok"
        else:
            status = "downtime" if has_downtime else "zero"

        seen_stations.add(station)
        hourly_rows.append((
            business_date, shift, file_hour, "AUTOPACK", line, station,
            qty, valid, 0.0, online, status,
        ))

    # ---- feeder ที่ไม่มีแถวใน raw = เครื่องไม่เดิน ต้องเติมเป็น 'no_data' ----
    for pipeline, spec in ap["pipelines"].items():
        line = spec["line"]
        for feeder in range(1, spec["feeder_count"] + 1):
            station = f"{line}-{feeder:02d}"
            if station in seen_stations:
                continue
            hourly_rows.append((
                business_date, shift, file_hour, "AUTOPACK", line, station,
                0, 0, 0.0, None, "no_data",
            ))

    # ---- Abnormal -> ยอด error ต่อช่อง ต่อชนิด ----
    # จัดกลุ่มตาม "สาย" ของพัสดุ ไม่ใช่เลข Sorting Port — แต่ละสายมีช่อง error
    # ของตัวเอง ส่วนเลข port ในไฟล์ใช้ปนกันระหว่างสาย จึงแยกด้วย port ไม่ได้
    error_counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for row in abnormal[1:]:
        if not row or row[0] in (None, ""):
            continue
        pipeline = str(row[1]).strip() if len(row) > 1 and row[1] else ""
        spec = ap["pipelines"].get(pipeline)
        if spec is None:
            warnings.append(f"Abnormal: ไม่รู้จัก Pipeline '{pipeline}' — ข้ามแถว")
            continue
        line = spec["line"]
        chute = spec.get("error_chute", line)
        source_type = str(row[8]).strip() if len(row) > 8 and row[8] else "unknown"
        error_counts[(line, chute, source_type)] += 1

    error_rows = [
        (business_date, shift, file_hour, "AUTOPACK", line, chute, err_type, count)
        for (line, chute, err_type), count in error_counts.items()
    ]

    core.upsert_hourly(conn, hourly_rows)
    core.upsert_errors(conn, error_rows)
    conn.commit()

    return {
        "path": os.path.basename(path),
        "business_date": business_date,
        "hour": file_hour,
        "shift": shift,
        "rows": len(hourly_rows),
        "errors": sum(error_counts.values()),
        "warnings": warnings,
    }


def ingest_folder(conn, folder: Optional[str] = None) -> dict:
    """อ่านทุกไฟล์ 00-23.xlsx ในโฟลเดอร์"""
    cfg = core.load_config()
    folder = folder or cfg["autopacking"]["raw_dir"]

    results, all_warnings = [], []
    for hour in range(24):
        path = os.path.join(folder, f"{hour:02d}.xlsx")
        if not os.path.exists(path):
            continue
        try:
            info = ingest_file(conn, path)
        except Exception as exc:                      # ไฟล์เดียวพังไม่ควรล้มทั้งรอบ
            all_warnings.append(f"{hour:02d}.xlsx อ่านไม่ได้: {exc}")
            continue
        results.append(info)
        all_warnings.extend(f"{info['path']}: {w}" for w in info["warnings"])

    return {
        "source": "AUTOPACK",
        "files": len(results),
        "rows": sum(r["rows"] for r in results),
        "errors": sum(r["errors"] for r in results),
        "warnings": all_warnings,
        "details": results,
    }
