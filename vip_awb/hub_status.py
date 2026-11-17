"""สถานะพัสดุที่คลัง (สแกนออกแล้ว / ค้างคลัง / หลุดสแกนส่ง)

ย้ายกติกามาจาก Worker Server: portal/tools/missing_status/logic.py (classify) ให้ผลตรงกับ
หน้าเว็บ "หาเลขไม่มีสถานะ" — ถ้าแก้กติกาที่นั่น ต้องแก้ที่นี่ด้วย
ต่างกันแค่ไม่ใช้ pandas (เครื่องรันไม่จำเป็นต้องมี)

ข้อมูลจาก JMS หน้า "ติดตามพัสดุ": POST operatingplatform/podTracking/inner/query/keywordList
{"keywordList": [...สูงสุด 100 เลข], "trackingTypeEnum": "WAYBILL", "countryId": "1"}
"""
from __future__ import annotations

import re
from datetime import datetime

GATEWAY_WORD = "เกตเวย์"
ARRIVAL_TYPES = ("พัสดุเข้าจากการขนลงรถ", "พัสดุถึงศูนย์กระจายสินค้า")
BAG_RE = re.compile(r"หมายเลขกระสอบ\s*[【\[]\s*([^】\]]*?)\s*[】\]]")
TIME_KEYS = ("scanTime", "scanDate", "operateTime", "scanTimeStr", "time")
TYPE_KEYS = ("scanTypeName", "scanTypeDesc", "typeName", "scanType")
DESC_KEYS = ("waybillTrackingContent", "packTrackingContent", "content", "desc", "description", "trackingDesc",
             "scanDesc", "remark")
BAG_KEYS = ("packageNumber", "packageCode", "bagNumber", "bagCode", "bagNo")
AWB_KEYS = ("billCode", "waybillNo", "waybillCode", "keyword", "billNo")
SITE_KEYS = ("scanNetworkName", "networkName", "siteName")
SENT_SCAN_TYPES = ("สแกนบรรจุกระสอบ", "สแกนส่งพัสดุขึ้นรถ", "สแกนสร้างถุง", "ขึ้นรถส่งออก",
                   "สแกนส่งออกพัสดุ", "สแกนนำส่งพัสดุ")
OPEN_CONTAINER = ("สแกนเปิดตู้", "สแกนรถมาถึงและขนถ่าย")
CLOSE_CONTAINER = ("สแกนปิดตู้", "สแกนปิดรถก่อนออก")
ADMIN_SCAN_WORDS = ("ลงทะเบียน", "ยืนยัน", "พิมพ์", "ปิดงาน", "พัสดุมีปัญหา", "เช็คสต็อค", "ตรวจนับ",
                    "ส่งมอบเข้าคลัง")

# สถานะ -> กลุ่มที่แสดงในรายงาน
SENT = "สแกนออกแล้ว"
STUCK = "ค้างคลัง"
MISSED = "หลุดสแกนส่ง"
OTHER = "ไม่พบข้อมูล"
GROUPS = (SENT, STUCK, MISSED, OTHER)
GROUP = {"สแกนแล้ว": SENT, "รอสแกนส่ง": STUCK, "ไม่มีสถานะ": STUCK, "หลุดสแกนส่ง": MISSED,
         "ไม่ผ่านคลัง": OTHER, "ไม่พบข้อมูล": OTHER}


def _pick(d: dict, keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def find_scan_lists(obj, awb=None, out=None) -> dict[str, list[dict]]:
    """เดินหา list ของบรรทัดสแกน (dict ที่มีเวลาสแกน) แล้วจัดกลุ่มตามเลขพัสดุ"""
    if out is None:
        out = {}
    if isinstance(obj, dict):
        awb = _pick(obj, AWB_KEYS) or awb
        for v in obj.values():
            find_scan_lists(v, awb, out)
    elif isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj) and any(_pick(x, TIME_KEYS) for x in obj):
            for x in obj:
                out.setdefault(str(awb or _pick(x, AWB_KEYS) or ""), []).append(x)
        else:
            for v in obj:
                find_scan_lists(v, awb, out)
    return out


def _time(v) -> datetime | None:
    try:
        return datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def classify(records: list[dict], hub: str = "KKN_GW") -> dict:
    """ดูช่วงที่พัสดุอยู่ที่คลังครั้งล่าสุด (กติกาเดียวกับ Worker Server)
    - มีสแกนบรรจุกระสอบ / ส่งพัสดุขึ้นรถ / ปิดตู้ขาออก ที่คลัง      -> สแกนแล้ว
    - ไม่มีสแกนส่ง แต่ไปโผล่ที่สาขาอื่นแล้ว                        -> หลุดสแกนส่ง
    - ยังอยู่ที่คลัง เครื่องปล่อยพัสดุสแกนถึงเกตเวย์ (กระสอบ -) แล้วเงียบ -> ไม่มีสถานะ
    - ยังอยู่ที่คลัง ยังไม่มีสแกนส่ง                                -> รอสแกนส่ง
    - ไม่เคยถูกสแกนที่คลัง                                        -> ไม่ผ่านคลัง"""
    rows = []
    for r in records:
        desc = str(_pick(r, DESC_KEYS) or "")
        bag = _pick(r, BAG_KEYS)
        if bag is None:
            m = BAG_RE.search(desc)
            bag = m.group(1) if m else None
        t = _time(_pick(r, TIME_KEYS))
        if t:
            rows.append({"time": t, "type": str(_pick(r, TYPE_KEYS) or ""),
                         "bag": None if bag is None else str(bag).strip(),
                         "site": str(_pick(r, SITE_KEYS) or "").strip()})
    rows.sort(key=lambda x: x["time"])
    res = {"status": "ไม่พบข้อมูล", "out_time": None, "last_type": "", "last_time": None, "last_site": ""}
    if not rows:
        return res
    last = rows[-1]
    res.update(last_type=last["type"], last_time=last["time"], last_site=last["site"])

    phys = [x for x in rows if not any(w in x["type"] for w in ADMIN_SCAN_WORDS)]
    at_hub = [i for i, x in enumerate(phys) if x["site"] == hub]
    if not at_hub:
        res["status"] = "ไม่ผ่านคลัง"
        return res
    end = start = at_hub[-1]
    while start > 0 and phys[start - 1]["site"] == hub:
        start -= 1
    visit, after = phys[start:end + 1], phys[end + 1:]
    machine = [x for x in visit if (GATEWAY_WORD in x["type"] or x["type"] in ARRIVAL_TYPES)
               and x["bag"] in ("", "-", None)]
    inbound = {x["bag"] for x in visit if x["type"] in OPEN_CONTAINER}
    sent = [x for x in visit if x["type"] in SENT_SCAN_TYPES
            or (x["type"] in CLOSE_CONTAINER and x["bag"] and x["bag"] not in inbound)]
    if sent:
        res.update(status="สแกนแล้ว", out_time=sent[0]["time"])
    elif after:
        res["status"] = "หลุดสแกนส่ง"
    elif machine:
        res["status"] = "ไม่มีสถานะ"
    else:
        res["status"] = "รอสแกนส่ง"
    return res
