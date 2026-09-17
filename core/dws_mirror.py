"""dws_mirror.py — ดึงไฟล์ raw ของเครื่อง DWS1-8 มาเก็บไว้ตามที่ตั้งไว้รายเครื่อง

**ปัญหาที่แก้**
Power Query ในไฟล์ workbook ชี้ไปที่ share ของเครื่อง DWS แต่ละตัวโดยตรง
พอเครื่องไหนดับ ยอดของชั่วโมงก่อนหน้าที่เครื่องยังเดินอยู่ก็หายไปด้วย
เพราะอ่านสดจากเครื่องที่ตอนนี้ไม่มีใครตอบ

เครื่องดับ 18:30 ไม่ควรทำให้ยอด 12:00-18:00 ที่เกิดขึ้นจริงหายไป

**วิธีแก้**
ดึงไฟล์มาเก็บไว้ทุกรอบที่ scheduler ทำงาน แล้วให้ Power Query อ่านจากสำเนานี้
เครื่องดับเมื่อไหร่ สำเนาที่ดึงได้ล่าสุดก็ยังอยู่

**กฎเหล็กข้อเดียว**
เขียนทับเฉพาะตอนดึงสำเร็จเท่านั้น ห้ามเอาความล้มเหลวไปทับของเดิม
เป็นหลักเดียวกับที่ใช้กันยอด AutoPacking หายใน metrics/core.py

ตั้งค่าอยู่ที่หน้า ตั้งค่า -> DWS PLAN Clients รายเครื่อง
(pull_folder / pull_file / pull_enabled ในหมวด [DWS1]..[DWS8] ของ config.ini)
DWS9-11 ไม่อยู่ในนี้ เพราะมันดึงจาก MySQL ด้วย run_dws อยู่แล้ว
"""

from __future__ import annotations

import configparser
import os
import shutil
import sys
import tempfile
from datetime import datetime
from typing import Callable, Optional

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metrics import core

LogFunc = Callable[..., None]

# เครื่องที่รองรับ — ต้องตรงกับ DWS_PULL_CLIENTS ใน bot_main.py
STATIONS = ("DWS1", "DWS2", "DWS3", "DWS4", "DWS5", "DWS6", "DWS7", "DWS8")

# ไฟล์ที่เล็กกว่านี้ถือว่าใช้ไม่ได้ (xlsx เปล่าๆ ยังใหญ่กว่านี้)
MIN_VALID_BYTES = 1000


def _noop(message: str, level: str = "INFO") -> None:
    print(f"[{level}] {message}")


def _ini() -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser()
    parser.read(os.path.join(core.PROJECT_ROOT, "config.ini"), encoding="utf-8")
    return parser


def _yaml_source(station: str) -> dict:
    """path ต้นทางของเครื่องนั้น มาจาก metrics_config.yaml (มีอยู่แล้ว)

    ชื่อใน yaml เป็น DWS_01..DWS_08 ส่วนใน config.ini เป็น DWS1..DWS8
    """
    key = f"DWS_{station[3:].zfill(2)}"
    machines = (core.load_config().get("dws") or {}).get("machines") or {}
    return machines.get(key) or {}


def plan() -> list:
    """อ่านค่าที่ตั้งไว้รายเครื่อง — คืนเฉพาะเครื่องที่ติ๊กไว้และกรอกครบ"""
    parser = _ini()
    jobs = []

    for station in STATIONS:
        if station not in parser:
            continue
        section = parser[station]

        enabled = str(section.get("pull_enabled", "false")).strip().lower() \
            in ("1", "true", "yes", "on")
        if not enabled:
            continue

        folder = (section.get("pull_folder") or "").strip().replace('"', "")
        filename = (section.get("pull_file") or "").strip().replace('"', "")
        spec = _yaml_source(station)

        # ไม่ได้ตั้งชื่อไฟล์ก็ใช้ชื่อเดิมของเครื่องนั้น
        if not filename:
            source_path = spec.get("path") or ""
            filename = os.path.basename(source_path) or f"{station.lower()}.xlsx"

        jobs.append({
            "station": station,
            "folder": folder,
            "file": filename,
            "spec": spec,
        })
    return jobs


def _source_file(spec: dict) -> Optional[str]:
    """เครื่องบางตัวมี path สำรอง (เช่น DWS7 ที่ย้ายโฟลเดอร์) — ลองตามลำดับ"""
    for key in ("path", "fallback_path"):
        path = spec.get(key)
        if path and os.path.isfile(path):
            return path
    return None


def _copy_atomic(source: str, target: str) -> int:
    """ก๊อปลงไฟล์ชั่วคราวก่อนแล้วสลับชื่อ

    ถ้าเขียนทับตรงๆ แล้ว Excel มาอ่านตอนก๊อปยังไม่จบ จะได้ไฟล์ครึ่งๆ
    os.replace บน Windows เป็น atomic — ผู้อ่านเห็นไฟล์เก่าเต็มๆ หรือใหม่เต็มๆ
    """
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    handle, temp = tempfile.mkstemp(suffix=".part",
                                    dir=os.path.dirname(target) or ".")
    os.close(handle)
    try:
        shutil.copyfile(source, temp)
        size = os.path.getsize(temp)
        if size < MIN_VALID_BYTES:
            raise OSError(f"ไฟล์ต้นทางเล็กผิดปกติ ({size} ไบต์)")
        os.replace(temp, target)
        return size
    finally:
        if os.path.exists(temp):
            try:
                os.remove(temp)
            except OSError:
                pass


def pull_one(job: dict, log: Optional[LogFunc] = None) -> dict:
    """ดึงไฟล์ของเครื่องเดียว — ล้มเหลวไม่แตะไฟล์เดิมที่มีอยู่"""
    write = log or _noop
    station = job["station"]
    result = {"station": station, "file": job["file"], "ok": False,
              "kept_old": False}

    if not job["folder"]:
        result["error"] = "ยังไม่ได้เลือกโฟลเดอร์"
        write(f"{station}: ยังไม่ได้เลือกโฟลเดอร์ปลายทาง", level="WARN")
        return result

    target = os.path.join(job["folder"], job["file"])
    result["target"] = target
    had_before = os.path.isfile(target)

    source = _source_file(job["spec"])
    if source is None:
        # เครื่องดับ / share เข้าไม่ถึง — ของเดิมต้องอยู่ต่อ ห้ามลบห้ามล้าง
        result["error"] = "เข้าถึงไฟล์ต้นทางไม่ได้"
        result["kept_old"] = had_before
        if had_before:
            age = (datetime.now()
                   - datetime.fromtimestamp(os.path.getmtime(target)))
            write(f"{station}: ดึงไม่ได้ — ใช้ไฟล์เดิมต่อ "
                  f"(เก่า {age.total_seconds() / 3600:,.1f} ชม.)", level="WARN")
        else:
            write(f"{station}: ดึงไม่ได้ และยังไม่เคยมีไฟล์", level="WARN")
        return result

    try:
        size = _copy_atomic(source, target)
    except Exception as exc:
        result["error"] = str(exc)
        result["kept_old"] = had_before
        write(f"{station}: ก๊อปไม่สำเร็จ ({exc})"
              + (" — ใช้ไฟล์เดิมต่อ" if had_before else ""), level="WARN")
        return result

    result["ok"] = True
    result["bytes"] = size
    write(f"{station}: {job['file']} {size / 1024 / 1024:,.1f} MB")
    return result


def pull_all(log: Optional[LogFunc] = None) -> dict:
    """ดึงทุกเครื่องที่ติ๊กไว้ — เครื่องหนึ่งล้มไม่กระทบเครื่องอื่น"""
    write = log or _noop
    jobs = plan()

    if not jobs:
        write("DWS1-8: ไม่มีเครื่องไหนติ๊กให้ดึงไฟล์", level="INFO")
        return {"results": [], "ok": 0, "kept_old": 0, "missing": 0}

    results = [pull_one(job, write) for job in jobs]
    ok = sum(1 for r in results if r["ok"])
    kept = sum(1 for r in results if r.get("kept_old"))
    missing = sum(1 for r in results
                  if not r["ok"] and not r.get("kept_old"))

    write(f"DWS1-8: สำเร็จ {ok} · ใช้ไฟล์เดิม {kept} · ไม่มีข้อมูล {missing}",
          level="SUCCESS" if not missing else "WARN")
    return {"results": results, "ok": ok, "kept_old": kept, "missing": missing}


def main() -> int:
    core.use_utf8_console()
    summary = pull_all()
    print()
    for r in summary["results"]:
        if r["ok"]:
            mark = f"{r['bytes'] / 1024 / 1024:,.1f} MB"
        elif r.get("kept_old"):
            mark = "ใช้ไฟล์เดิม"
        else:
            mark = r.get("error", "ไม่สำเร็จ")
        print(f"  {r['station']:<7} {r['file']:<14} {mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
