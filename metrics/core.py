"""core.py — ของใช้ร่วมของชั้น metrics: config, business date, การเปิด DB

แยกออกมาเพราะทั้ง ingest / aggregate / dashboard ต้องใช้กติกา business_date
กับ shift ชุดเดียวกัน ถ้าปล่อยให้แต่ละไฟล์คิดเอง เดี๋ยวยอดไม่ตรงกัน
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
CONFIG_PATH = os.path.join(PROJECT_ROOT, "metrics_config.yaml")
SCHEMA_PATH = os.path.join(HERE, "schema.sql")
DEFAULT_DB_PATH = os.path.join(HERE, "store.db")

_config_cache: Optional[Dict[str, Any]] = None


def load_config(path: str = CONFIG_PATH) -> Dict[str, Any]:
    global _config_cache
    if _config_cache is None:
        with open(path, "r", encoding="utf-8") as fh:
            _config_cache = yaml.safe_load(fh)
        _merge_program_config(_config_cache)
    return _config_cache


def _merge_program_config(cfg: Dict[str, Any]) -> None:
    """เอาค่าจาก config.ini ของโปรแกรมหลักมาทับ metrics_config.yaml

    ค่าพวกนี้โปรแกรมหลักตั้งไว้อยู่แล้วและแก้ได้จากหน้าตั้งค่าของมัน
    ถ้าปล่อยให้ yaml เก็บค่าเดียวกันอีกชุด วันหนึ่งสองที่จะไม่ตรงกัน
    แล้วไม่มีใครรู้ว่าอันไหนของจริง — yaml จึงเหลือแค่ "นิยาม metric"
    (เป้า ชื่อสถานี เกณฑ์) ส่วน "ที่อยู่ไฟล์ / เวลา" มาจาก config.ini ที่เดียว
    """
    import configparser

    parser = configparser.RawConfigParser()
    parser.read(os.path.join(PROJECT_ROOT, "config.ini"), encoding="utf-8")

    def value(section: str, key: str) -> str:
        if section not in parser:
            return ""
        return (parser[section].get(key) or "").strip().replace('"', "")

    # รอบงานเริ่มกี่โมง <- [TIME] start_hour
    start_hour = value("TIME", "start_hour")
    if start_hour.isdigit():
        cfg.setdefault("business_day", {})
        cfg["business_day"]["start_hour"] = int(start_hour)
        cfg["business_day"]["shift_a_start"] = int(start_hour)

    # โฟลเดอร์ + ชื่อไฟล์ดิบ JMS <- [DWS_JMS] raw_path / name_dwspda / name_auto
    raw_path = value("DWS_JMS", "raw_path")
    if raw_path:
        cfg.setdefault("jms", {})["raw_dir"] = raw_path
        names = {
            "PDA_DWS": value("DWS_JMS", "name_dwspda") or "DWSPDA.xlsx",
            "PDA_AUTO": value("DWS_JMS", "name_auto") or "DWSXAUTOPDA.xlsx",
            "BAG": value("DWS_JMS", "name_auto") or "DWSXAUTOPDA.xlsx",
        }
        sources = (cfg.get("jms") or {}).get("sources") or {}
        for key, filename in names.items():
            if key in sources:
                sources[key]["file"] = filename
                sources[key]["path"] = os.path.join(
                    raw_path, filename).replace("\\", "/")


def use_utf8_console() -> None:
    """กัน console ของ Windows ตายเวลาพิมพ์ภาษาไทย

    cmd.exe ดีฟอลต์เป็น cp1252/cp874 ซึ่ง encode ตัวอักษรไทยไม่ได้ -> UnicodeEncodeError
    แล้วโปรแกรมตายทั้งตัว (เจอจริงตอน server ตายเพราะบรรทัด print บรรทัดเดียว)
    errors="replace" ทำให้อย่างมากที่สุดคือตัวอักษรเพี้ยน ไม่ใช่โปรแกรมล้ม
    ต้องเรียกที่ entry point ของทุก CLI"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


# =====================================================================
# Business date / shift
# =====================================================================

def business_date_of(dt: datetime, start_hour: int) -> date:
    """วันรอบงานของเวลา dt — รอบเริ่ม start_hour (12:00) ดังนั้น 02:00 ของวันที่ 17
    ยังนับเป็นรอบของวันที่ 16"""
    return dt.date() if dt.hour >= start_hour else dt.date() - timedelta(days=1)


def shift_of(hour: int, shift_a_start: int, shift_b_start: int) -> str:
    """กะ A = 12:00-01:00 (ชั่วโมง 12..23 และ 0) / กะ B = 01:00-12:00 (ชั่วโมง 1..11)"""
    if shift_a_start <= hour <= 23 or hour < shift_b_start:
        return "A"
    return "B"


def hour_sequence(start_hour: int) -> list[int]:
    """ลำดับชั่วโมงของรอบงาน 24 ช่อง เริ่มที่ start_hour — ใช้เรียงคอลัมน์บน dashboard"""
    return [(start_hour + i) % 24 for i in range(24)]


# =====================================================================
# การแปลงค่าจาก raw ที่รูปแบบไม่นิ่ง
# =====================================================================

_DT_PATTERNS = (
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d %H:%M",
)


def parse_datetime(value: Any) -> Optional[datetime]:
    """raw ของ DWS แต่ละเครื่องส่งเวลามาคนละรูปแบบ — dws2 ใช้ '2026/09/16 16:59:47'
    ส่วน dws3 ใช้ '2026/9/16 10:09:25' (ไม่เติมศูนย์) เลยต้องรับหลายแบบ"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for pattern in _DT_PATTERNS:
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    # เผื่อรูปแบบที่ไม่คาดคิด — ดึงตัวเลขออกมาประกอบเอง
    nums = re.findall(r"\d+", text)
    if len(nums) >= 5:
        try:
            y, mo, d, h, mi = (int(n) for n in nums[:5])
            s = int(nums[5]) if len(nums) > 5 else 0
            return datetime(y, mo, d, h, mi, min(s, 59))
        except ValueError:
            return None
    return None


def parse_online_minutes(value: Any) -> Optional[int]:
    """'59分钟' -> 59 ; '1小时30分钟' -> 90 ; ว่าง -> None"""
    if value is None:
        return None
    text = str(value)
    hours = re.search(r"(\d+)\s*小时", text)
    minutes = re.search(r"(\d+)\s*分钟", text)
    if not hours and not minutes:
        digits = re.findall(r"\d+", text)
        return int(digits[0]) if digits else None
    total = 0
    if hours:
        total += int(hours.group(1)) * 60
    if minutes:
        total += int(minutes.group(1))
    return total


def to_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def to_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# =====================================================================
# SQLite
# =====================================================================

def connect(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """เปิด DB พร้อมสร้าง schema ถ้ายังไม่มี — เรียกซ้ำได้ (schema เป็น IF NOT EXISTS)"""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        conn.executescript(fh.read())
    _migrate(conn)
    seed_stations(conn)          # sync ทุกครั้ง — in_service ใน config เปลี่ยนได้
    conn.commit()
    return conn


def read_connect(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """เปิดแบบอ่านอย่างเดียว ข้ามการสร้าง schema/seed

    connect() รัน schema.sql + migrate + seed ทุกครั้ง ซึ่งคุ้มตอน ingest
    แต่ถ้าเว็บเรียกทุก request จะเสียเวลาไปกับงานที่ไม่ได้เปลี่ยนอะไรเลย"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """เติมคอลัมน์ที่เพิ่มทีหลังให้ DB เก่า — CREATE TABLE IF NOT EXISTS ไม่ทำให้"""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(dim_station)")}
    if "in_service" not in existing:
        conn.execute("ALTER TABLE dim_station ADD COLUMN in_service INTEGER NOT NULL DEFAULT 1")

    # fact_error รุ่นแรกไม่มี line ใน PK ทำให้ AP1/AP2 ทับกัน — สร้างตารางใหม่แล้วย้ายข้อมูล
    # (ข้อมูลที่ทับกันไปแล้วกู้ไม่ได้ ต้อง ingest ใหม่ ซึ่งทำได้เพราะยุบมาจาก raw ทั้งหมด)
    pk_cols = [row["name"] for row in conn.execute("PRAGMA table_info(fact_error)") if row["pk"]]
    if pk_cols and "line" not in pk_cols:
        conn.executescript("""
            ALTER TABLE fact_error RENAME TO fact_error_old;
            CREATE TABLE fact_error (
                business_date TEXT NOT NULL, shift TEXT NOT NULL, hour_start INTEGER NOT NULL,
                source TEXT NOT NULL, line TEXT NOT NULL, station TEXT NOT NULL,
                error_type TEXT NOT NULL, qty INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
                PRIMARY KEY (business_date, source, line, station, error_type, hour_start)
            );
            INSERT OR IGNORE INTO fact_error SELECT * FROM fact_error_old;
            DROP TABLE fact_error_old;
            CREATE INDEX IF NOT EXISTS ix_fact_error_date ON fact_error (business_date, source);
        """)


def seed_stations(conn: sqlite3.Connection) -> None:
    """เติม/อัปเดต master list ของจุดปล่อยพัสดุ — ต้องมีครบก่อน ไม่งั้นแถวที่เครื่อง
    ไม่เดินจะหายไปจากรายงานแทนที่จะขึ้นเป็น '-'"""
    cfg = load_config()
    rows = []

    for pipeline, spec in cfg["autopacking"]["pipelines"].items():
        line = spec["line"]
        offset = spec["display_offset"]
        for feeder in range(1, spec["feeder_count"] + 1):
            rows.append((
                "AUTOPACK", line, f"{line}-{feeder:02d}",
                str(offset + feeder), offset + feeder, 1,
            ))

    for idx, (station, spec) in enumerate(cfg["dws"]["machines"].items(), start=1):
        rows.append(("DWS", "DWS", station, station, idx,
                     1 if spec.get("in_service", True) else 0))
    for idx, (station, spec) in enumerate(cfg["dws_db"]["stations"].items(), start=9):
        spec = spec or {}
        rows.append(("DWS", "DWS", station, station, idx,
                     1 if spec.get("in_service", True) else 0))

    conn.executemany(
        """
        INSERT INTO dim_station
            (source, line, station, display_name, sort_order, in_service)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT (source, station) DO UPDATE SET
            display_name = excluded.display_name,
            sort_order   = excluded.sort_order,
            in_service   = excluded.in_service
        """,
        rows,
    )


def upsert_hourly(conn: sqlite3.Connection, rows: list[tuple]) -> int:
    """rows: (business_date, shift, hour_start, source, line, station,
              qty, valid_qty, weight_kg, online_minutes, status)"""
    now = datetime.now().isoformat(timespec="seconds")
    conn.executemany(
        """
        INSERT INTO fact_hourly (business_date, shift, hour_start, source, line,
                                 station, qty, valid_qty, weight_kg,
                                 online_minutes, status, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (business_date, source, station, hour_start) DO UPDATE SET
            qty = excluded.qty,
            valid_qty = excluded.valid_qty,
            weight_kg = excluded.weight_kg,
            online_minutes = excluded.online_minutes,
            status = excluded.status,
            updated_at = excluded.updated_at
        -- กันไม่ให้ "ไม่มีข้อมูล" ไปทับ "มีข้อมูล" ที่เก็บไว้แล้ว
        -- ต้นทางบางตัวรีเซ็ตไฟล์ตัวเองเป็นว่างเมื่อขึ้นรอบใหม่ (AutoPacking ทำตอน 13:00)
        -- ถ้าปล่อยให้ทับ ยอดทั้งวันที่เก็บมาแล้วจะหายเป็น 0 ซึ่งกู้ไม่ได้
        -- status 'zero' ยังทับได้ เพราะแปลว่า "รู้ว่าไม่มีของ" ซึ่งเป็นข้อมูลจริง
        WHERE NOT (excluded.status = 'no_data' AND fact_hourly.qty > 0)
        """,
        [row + (now,) for row in rows],
    )
    return len(rows)


def prune_old_data(conn: sqlite3.Connection,
                   keep_days: Optional[int] = None) -> dict:
    """ลบข้อมูลที่เก่ากว่าจำนวนวันที่ตั้งไว้

    ระบบสะสมข้อมูลไปข้างหน้า ไม่ได้ดึงย้อนหลัง (DWSPDA/DWSXAUTOPDA ดึงย้อนหลัง
    จากเว็บ JMS ไม่ได้) จึงต้องมีเพดานไม่ให้ไฟล์โตไปเรื่อยๆ

    ไม่แตะวันรอบงานปัจจุบันเด็ดขาด แม้ตั้ง keep_days เป็น 0 ก็ตาม
    """
    cfg = load_config()
    retention = cfg.get("retention") or {}
    if keep_days is None:
        keep_days = int(retention.get("days", 7))
    keep_days = max(1, keep_days)

    start_hour = cfg["business_day"]["start_hour"]
    today = business_date_of(datetime.now(), start_hour)
    cutoff = (today - timedelta(days=keep_days - 1)).isoformat()

    removed = {}
    for table in ("fact_hourly", "fact_error"):
        cur = conn.execute(
            f"DELETE FROM {table} WHERE business_date < ?", (cutoff,))
        removed[table] = cur.rowcount
    conn.commit()
    return {"cutoff": cutoff, "keep_days": keep_days, "removed": removed}


def available_dates(conn: sqlite3.Connection) -> dict:
    """ช่วงวันที่ที่มีข้อมูลจริง — ให้ปุ่มเปลี่ยนวันบนเว็บรู้ขอบเขต
    ไม่งั้นกดย้อนไปเรื่อยๆ ได้ไม่รู้จบแล้วเจอแต่หน้าว่าง"""
    row = conn.execute(
        "SELECT MIN(business_date) lo, MAX(business_date) hi FROM fact_hourly"
    ).fetchone()
    return {"first": row["lo"], "last": row["hi"]}


def upsert_errors(conn: sqlite3.Connection, rows: list[tuple]) -> int:
    """rows: (business_date, shift, hour_start, source, line, station, error_type, qty)"""
    now = datetime.now().isoformat(timespec="seconds")
    conn.executemany(
        """
        INSERT INTO fact_error (business_date, shift, hour_start, source, line,
                                station, error_type, qty, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT (business_date, source, line, station, error_type, hour_start)
        DO UPDATE SET qty = excluded.qty, updated_at = excluded.updated_at
        """,
        [row + (now,) for row in rows],
    )
    return len(rows)
