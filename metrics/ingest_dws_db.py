"""ingest_dws_db.py — ยอดของ DWS9-11 จาก MySQL โดยตรง

ต่างจาก DWS1-8 ที่อ่านจากไฟล์ share — สามตัวนี้เขียนลง DB กลางที่ 10.30.32.10
ค่าเชื่อมต่ออ่านจาก config.ini [DWS_JMS] ของโปรแกรมเดิม จะได้ไม่ต้องตั้งซ้ำสองที่

สำคัญ: ยุบยอดด้วย GROUP BY ตั้งแต่ใน SQL ไม่ดึงรายแถวออกมา
รอบหนึ่งได้กลับมาไม่กี่ร้อยแถวแทนหลายหมื่น
"""

from __future__ import annotations

import configparser
import os
import re
from typing import Optional

import pymysql

from . import core

PROJECT_ROOT = core.PROJECT_ROOT


def db_settings() -> dict:
    """อ่านค่า DB จาก config.ini ของโปรแกรมหลัก"""
    parser = configparser.RawConfigParser()
    parser.read(os.path.join(PROJECT_ROOT, "config.ini"), encoding="utf-8")
    section = parser["DWS_JMS"] if "DWS_JMS" in parser else {}
    return {
        "host": section.get("db_host", "10.30.32.10"),
        "port": int(section.get("db_port", 3306)),
        "user": section.get("db_user", "root"),
        "password": section.get("db_password", "root"),
        "database": section.get("db_name", "dwsdb_thailand"),
    }


def ingest(conn, business_date: Optional[str] = None) -> dict:
    cfg_all = core.load_config()
    cfg = cfg_all["dws_db"]
    bd_cfg = cfg_all["business_day"]
    start_hour = bd_cfg["start_hour"]

    table = cfg["table"]
    if not re.match(r"^[A-Za-z0-9_]+$", table):       # ชื่อ table มาจาก config เท่านั้น
        return {"source": "DWS_DB", "rows": 0, "warnings": [f"ชื่อ table ไม่ถูกต้อง: {table}"]}

    settings = db_settings()
    warnings: list[str] = []
    mysql = None
    try:
        mysql = pymysql.connect(
            **settings, charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10, read_timeout=120,
        )
        # แถวขยะที่ ScanTime เป็นปี 9999 ปนอยู่ในตารางจริง — ถ้าไม่กรอง
        # business_date จะกระโดดไปปี 9999 แล้วยอดของวันนั้นหายไปทั้งก้อน
        sql = f"""
            SELECT DwsNo,
                   DATE(ScanTime - INTERVAL %s HOUR) AS bd,
                   HOUR(ScanTime)                    AS hr,
                   COUNT(*)                          AS qty,
                   SUM(IFNULL(ExceptionCode,0) IN ({{normal}})) AS valid_qty,
                   SUM(IFNULL(Weight,0))             AS weight_kg
            FROM {table}
            WHERE ScanTime >= %s AND YEAR(ScanTime) <= %s
            GROUP BY DwsNo, bd, hr
        """
        # error แยกตามรหัส — ต้องดึงด้วย ไม่งั้น % error รวมของแท็บ DWS จะต่ำกว่าจริง
        # เพราะ DWS9-11 คิดเป็นครึ่งหนึ่งของยอดแต่ถูกนับว่า error = 0
        err_sql = f"""
            SELECT DwsNo,
                   DATE(ScanTime - INTERVAL %s HOUR) AS bd,
                   HOUR(ScanTime)                    AS hr,
                   ExceptionCode                     AS code,
                   COUNT(*)                          AS qty
            FROM {table}
            WHERE ScanTime >= %s AND YEAR(ScanTime) <= %s
              AND IFNULL(ExceptionCode, 0) NOT IN ({{normal}})
            GROUP BY DwsNo, bd, hr, code
        """
        # 111 = คัดแยกสำเร็จ ไม่ใช่ error — ถ้าไม่ยกเว้น error rate จะพุ่งเป็น 63%
        normal = cfg["normal_exception_codes"]
        normal_sql = ",".join(str(int(code)) for code in normal)
        sql = sql.format(normal=normal_sql)
        err_sql = err_sql.format(normal=normal_sql)
        since = f"{business_date} 00:00:00" if business_date else "2000-01-01 00:00:00"
        with mysql.cursor() as cur:
            cur.execute(sql, (start_hour, since, cfg["max_valid_year"]))
            records = cur.fetchall()
            cur.execute(err_sql, (start_hour, since, cfg["max_valid_year"]))
            err_records = cur.fetchall()
    except pymysql.MySQLError as exc:
        return {"source": "DWS_DB", "rows": 0, "qty": 0,
                "warnings": [f"ต่อ MySQL ไม่ได้ ({settings['host']}): {exc}"]}
    finally:
        if mysql is not None:
            try:
                mysql.close()
            except Exception:
                pass

    name_map = cfg["dws_no_map"]
    labels = cfg.get("exception_labels") or {}
    rows, unknown, total = [], set(), 0
    for rec in records:
        station = name_map.get(str(rec["DwsNo"]).strip())
        if station is None:
            unknown.add(str(rec["DwsNo"]))
            continue
        bd = str(rec["bd"])
        if business_date and bd != business_date:
            continue
        hour = int(rec["hr"])
        total += int(rec["qty"])
        rows.append((
            bd, core.shift_of(hour, bd_cfg["shift_a_start"], bd_cfg["shift_b_start"]),
            hour, "DWS", "DWS", station,
            int(rec["qty"]), int(rec["valid_qty"] or 0),
            round(float(rec["weight_kg"] or 0), 2), None, "ok",
        ))

    err_rows = []
    for rec in err_records:
        station = name_map.get(str(rec["DwsNo"]).strip())
        if station is None:
            continue
        bd = str(rec["bd"])
        if business_date and bd != business_date:
            continue
        hour = int(rec["hr"])
        err_rows.append((
            bd, core.shift_of(hour, bd_cfg["shift_a_start"], bd_cfg["shift_b_start"]),
            hour, "DWS", "DWS", station,
            labels.get(str(rec["code"]), f"รหัส {rec['code']}"), int(rec["qty"]),
        ))

    if unknown:
        warnings.append("DwsNo ที่ยังไม่ได้ map: " + ", ".join(sorted(unknown)))

    # ล้าง error เดิมของเครื่องกลุ่มนี้ก่อนเขียนใหม่ — upsert อย่างเดียวไม่พอ
    # เพราะถ้าเกณฑ์ error เปลี่ยน (เช่นเลิกนับรหัส 111) แถวเก่าจะค้างอยู่ตลอด
    if business_date:
        conn.executemany(
            "DELETE FROM fact_error WHERE business_date = ? AND source = 'DWS' AND station = ?",
            [(business_date, station) for station in name_map.values()],
        )

    core.upsert_hourly(conn, rows)
    core.upsert_errors(conn, err_rows)
    conn.commit()
    return {"source": "DWS_DB", "rows": len(rows), "errors": len(err_rows),
            "qty": total, "warnings": warnings}
