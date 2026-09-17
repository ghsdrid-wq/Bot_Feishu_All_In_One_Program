"""ingest_jms.py — ยุบยอดจากไฟล์ export ของ JMS J&T ลง fact_hourly

ไฟล์ดิบใหญ่มาก (3-4 แสนแถว 16 คอลัมน์) จึงอ่านด้วย pandas+calamine และเลือก
เฉพาะ 4 คอลัมน์ที่ใช้จริง — ตัดทิ้ง 75% ตั้งแต่ตอนอ่าน ทำให้เหลือ ~4 วินาที/ไฟล์
(openpyxl กับไฟล์ขนาดนี้ใช้เวลาเป็นนาที)

3 source จาก 2 ไฟล์:
  PDA_AUTO  DWSXAUTOPDA.xlsx  นับชิ้น  เฉพาะ Autopacking_pdaNN (ยอดบรรจุมือ)
  BAG       DWSXAUTOPDA.xlsx  นับกระสอบ (distinct หมายเลขกระสอบ) ต่อคนสแกน
  PDA_DWS   DWSPDA.xlsx       นับชิ้น  เฉพาะ Dws_pdaNN

หมายเหตุ: ในไฟล์ AUTO มีแถวของ 'Autopacking_KKN_WCS_01' ปนอยู่ ~214,000 แถว
ซึ่งเป็นยอดที่ "เครื่อง" บรรจุเอง ไม่ใช่ยอดบรรจุมือ จึงถูกกรองออกด้วย prefix
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd

from . import core


def _read(path: str) -> pd.DataFrame:
    cols = core.load_config()["jms"]["columns"]
    order = ["bag", "scan_time", "weight", "employee"]
    idx = [cols[name] for name in order]
    df = pd.read_excel(path, engine="calamine", usecols=idx, dtype=str)
    # usecols คืนคอลัมน์ตามลำดับในไฟล์เสมอ ไม่ใช่ลำดับที่ส่งเข้าไป
    df.columns = [name for _, name in sorted(zip(idx, order))]
    return df


def ingest_source(conn, key: str, business_date: Optional[str] = None,
                  path: Optional[str] = None) -> dict:
    cfg_all = core.load_config()
    cfg = cfg_all["jms"]
    spec = cfg["sources"][key]
    bd_cfg = cfg_all["business_day"]
    start_hour = bd_cfg["start_hour"]

    # ลำดับความสำคัญ: พาธที่ส่งเข้ามา > พาธเต็มที่ตั้งไว้ใน config > โฟลเดอร์+ชื่อไฟล์
    # ชื่อไฟล์ export เปลี่ยนได้ในโปรแกรมหลัก จึงให้ตั้งพาธเต็มรายไฟล์ได้
    path = path or (spec.get("path") or "").strip() or os.path.join(
        cfg["raw_dir"], spec["file"])
    if not os.path.isfile(path):
        return {"source": key, "rows": 0, "qty": 0,
                "warnings": [f"ไม่พบไฟล์ {path}"]}

    df = _read(path)
    total_rows = len(df)

    # กรองเฉพาะเครื่อง/คนที่เป็นของ source นี้
    df = df[df["employee"].str.startswith(spec["prefix"], na=False)].copy()
    if df.empty:
        return {"source": key, "rows": 0, "qty": 0,
                "warnings": [f"ไม่มีแถวที่ขึ้นต้นด้วย '{spec['prefix']}' ใน {total_rows:,} แถว"]}

    # PDA ลงรถ: นับเฉพาะแถวที่ไม่มีหมายเลขกระสอบ
    # พัสดุที่มีเลขกระสอบถูกนับไปแล้วตอนบรรจุกระสอบ ถ้านับซ้ำยอดจะพองขึ้น 4-5 เท่า
    # (Dws_pda03 ทั้งหมด 43,606 แถว แต่ที่ยิงด้วย PDA จริงคือ 9,100)
    excluded_bag_rows = 0
    if spec.get("exclude_rows_with_bag"):
        bag = df["bag"].astype(str).str.strip().str.lower()
        keep = bag.isin(("", "nan", "none")) | df["bag"].isna()
        excluded_bag_rows = int((~keep).sum())
        df = df[keep].copy()
        if df.empty:
            return {"source": key, "rows": 0, "qty": 0,
                    "warnings": ["ทุกแถวมีหมายเลขกระสอบ — ไม่เหลือแถวให้นับ"]}

    ts = pd.to_datetime(df["scan_time"], errors="coerce")
    df = df[ts.notna()].copy()
    ts = ts[ts.notna()]
    df["hour"] = ts.dt.hour
    # business_date: ก่อน start_hour ให้นับเป็นรอบของเมื่อวาน
    df["bd"] = (ts - pd.Timedelta(hours=start_hour)).dt.date.astype(str)
    if business_date:
        df = df[df["bd"] == business_date]
    if df.empty:
        return {"source": key, "rows": 0, "qty": 0,
                "warnings": [f"ไม่มีข้อมูลของวันรอบงาน {business_date}"]}

    measure = spec.get("measure", "count")
    if measure == "distinct_bag":
        # นับกระสอบไม่ใช่ชิ้น — และกระสอบหนึ่งใช้เวลาบรรจุข้ามชั่วโมงได้
        #
        # ถ้า distinct ต่อ (ชั่วโมง, คน) ตรงๆ กระสอบที่คาบสองชั่วโมงจะถูกนับสองรอบ
        # ทำให้ผลรวมรายชั่วโมงมากกว่ายอดจริงของวัน (ทดสอบแล้วเกินไป 40 กระสอบ)
        # distinct เป็นค่าที่บวกข้าม bucket ไม่ได้ จึงต้องผูกกระสอบกับ bucket เดียว
        #
        # กติกา: ยกกระสอบให้ "ชั่วโมงและคนที่สแกนชิ้นแรกของกระสอบนั้น" (คนเปิดกระสอบ)
        # ผลที่ได้ตรงกับยอดในรายงาน Excel เดิม และบวกรายชั่วโมงได้ลงตัวพอดี
        df = df.assign(_ts=ts)
        first = (df.sort_values("_ts")
                   .drop_duplicates(subset=["bag"], keep="first"))
        grouped = (first.groupby(["bd", "hour", "employee"])
                        .size().reset_index(name="qty"))
        grouped["weight"] = 0.0
    else:
        weight = pd.to_numeric(df["weight"], errors="coerce").fillna(0)
        df = df.assign(_w=weight)
        grouped = (df.groupby(["bd", "hour", "employee"])
                     .agg(qty=("employee", "size"), weight=("_w", "sum"))
                     .reset_index())

    rows = []
    seen_bd: set[str] = set()
    for rec in grouped.itertuples(index=False):
        shift = core.shift_of(int(rec.hour), bd_cfg["shift_a_start"], bd_cfg["shift_b_start"])
        seen_bd.add(rec.bd)
        rows.append((
            rec.bd, shift, int(rec.hour), key, key, rec.employee,
            int(rec.qty), int(rec.qty), round(float(rec.weight), 2), None, "ok",
        ))

    core.upsert_hourly(conn, rows)
    _ensure_stations(conn, key, spec, sorted(grouped["employee"].unique()))
    conn.commit()

    return {
        "source": key,
        "rows": len(rows),
        "qty": int(grouped["qty"].sum()),
        "stations": grouped["employee"].nunique(),
        "excluded_bag_rows": excluded_bag_rows,
        "dates": sorted(seen_bd),
        "warnings": [],
    }


def _ensure_stations(conn, key: str, spec: dict, found: list[str]) -> None:
    """เติม master list ของคนสแกน — ตัวที่ยังไม่เคยเจอใน raw จะถูกเพิ่มทีหลัง
    ได้เอง แต่ต้องมี prefix ครบตาม station_count เพื่อให้แถวว่างขึ้นเป็น '-'"""
    known = {f"{spec['prefix']}{i:02d}" for i in range(1, spec["station_count"] + 1)}
    known.update(found)
    rows = [
        (key, key, name, name, _sort_key(name), 1)
        for name in sorted(known, key=_sort_key)
    ]
    conn.executemany(
        """
        INSERT INTO dim_station (source, line, station, display_name, sort_order, in_service)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT (source, station) DO UPDATE SET sort_order = excluded.sort_order
        """,
        rows,
    )


def _sort_key(name: str) -> int:
    digits = "".join(ch for ch in name if ch.isdigit())
    return int(digits) if digits else 999


def ingest_all(conn, business_date: Optional[str] = None,
               raw_dir: Optional[str] = None) -> dict:
    cfg = core.load_config()["jms"]
    results, warnings = [], []
    for key, spec in cfg["sources"].items():
        # raw_dir ที่ส่งเข้ามาใช้ override ได้ (เช่นตอนทดสอบ) แต่ถ้าไม่ส่ง
        # ปล่อยให้ ingest_source เลือกจาก path เต็มใน config เอง
        path = os.path.join(raw_dir, spec["file"]) if raw_dir else None
        try:
            info = ingest_source(conn, key, business_date, path)
        except Exception as exc:
            info = {"source": key, "rows": 0, "qty": 0,
                    "warnings": [f"อ่านไม่สำเร็จ: {exc}"]}
        results.append(info)
        warnings.extend(f"{key}: {w}" for w in info.get("warnings", []))
    return {
        "source": "JMS",
        "rows": sum(r["rows"] for r in results),
        "warnings": warnings,
        "details": results,
    }
