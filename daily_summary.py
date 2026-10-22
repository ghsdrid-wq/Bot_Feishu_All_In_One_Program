# -*- coding: utf-8 -*-
"""daily_summary.py — สรุปยอดประจำวันส่งเข้า Feishu แบบเดียวกับรายงาน manual

ข้อความเดียว (Feishu post) เรียงหัวข้อ + รูปตารางทีละหัวข้อ:
  1. อัตราการปล่อยพัสดุ AutoPacking      <- คำนวณเองจากไฟล์ 00-23.xlsx บน share (เหมือน Autoformat!A2:AK32)
  2. อัตราการยิงบรรจุ PDA 人工建包        <- คำนวณเองจาก DWSXAUTOPDA.xlsx
  3. จำนวนการใช้กระสอบซิปแดง             <- คำนวณเองจาก DWSXAUTOPDA.xlsx
  4. ยอดแสกนพัสดุลงรถผ่านเครื่อง PDA      <- คำนวณเองจาก DWSPDA.xlsx
  5. อัตราการปล่อยพัสดุ DWS              <- คำนวณเองจาก DWS1-8 realtime + DWS9-11 + DWSPDA (เหมือน DWSREALTIME)
  6. รายการความผิดปกติ Autopacking        <- รวมชีต Abnormal จากไฟล์ AutoPacking รายชั่วโมง
แล้วตามด้วยไฟล์ข้อมูลดิบของทุกตารางรวมไฟล์เดียว แยก sheet

กติกาการนับยึดตามสูตรในไฟล์ manual ("2_DWS & PDA (Network Sharing)"):
  - ตารางรายชั่วโมงนับ "จำนวนแถว" ของเครื่องนั้นในชั่วโมงนั้น (COUNTIFS เดิม)
  - กระสอบซิปแดง = ตัดเลขกระสอบซ้ำทั้งไฟล์ (เก็บแถวแรก) แล้วนับเลขที่ขึ้นต้นด้วย B ต่อเครื่อง
    (ตรวจกับ Excel 2026-09-11 แล้ว: ตรงทุกเครื่อง รวม 2,356)
  - สีเป็น color scale 3 สี แดง(ต่ำสุด) -> เหลือง(มัธยฐาน) -> เขียว(สูงสุด)
  - ตาราง Error นับ Billcode ไม่ซ้ำต่อชนิด (ตรวจกับยอด manual 2026-10-18 แล้ว: 604 / 2,892 / รวม 3,501)

โมดูลนี้ไม่ยุ่งกับ UI — bot_main เรียก run() จาก worker thread
และรันเดี่ยวเพื่อทดสอบได้:  python daily_summary.py --start "2026-10-18 15:00" --end "2026-10-19 10:00" --no-send
"""

from __future__ import annotations

import argparse
import configparser
import os
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

LogFunc = Callable[..., None]
RunFunc = Callable[[], bool]

SECTION = "DAILY_SUMMARY"

DEFAULTS: Dict[str, str] = {
    # ลำดับหัวข้อในข้อความ — ลบ/สลับได้ (autopacking, auto_pda, pda_bag, dws_pda, dws)
    "sections": "autopacking,auto_pda,pda_bag,dws_pda,dws,abnormal",
    # รูปที่มาจากขั้นตอน Excel Image (ชื่อไฟล์ในโฟลเดอร์ output)
    # รูปของแต่ละหัวข้อ: ว่าง = บอทวาดเองจากไฟล์ดิบ (ตัวเลขตรงกับสูตรใน Excel)
    # ใส่ชื่อไฟล์ = ใช้รูปที่ขั้นตอน Excel Image แคปไว้แทน — ช่วงรูปที่ต้องตั้งในหน้า Workbooks:
    #   Autoformat = A2:AK32, DWSREALTIME = A1:AF26, AUTO PDA = A1:AC50, DWS PDA = A1:AC15, PDA BAG = A1:B47
    "autopacking_image": "",
    "dws_image": "",
    "auto_pda_image": "",
    "pda_bag_image": "",
    "dws_pda_image": "",
    # รูปจาก Excel ที่เก่ากว่านี้ (นาที) ถือว่าเป็นรูปรอบก่อน จะไม่ส่ง
    "image_max_age_minutes": "120",
    # เครื่อง PDA ที่แสดงในตาราง
    "auto_pda_prefix": "Autopacking_pda",
    "auto_pda_count": "45",
    "dws_pda_prefix": "Dws_pda",
    "dws_pda_count": "10",
    # รอบงานจบกี่โมง (ตารางรายชั่วโมงแสดงถึงก่อนชั่วโมงนี้) / กะ B เริ่มกี่โมง
    "day_end_hour": "12",
    "shift_b_start": "1",
    # เวลาสิ้นสุดในหัวข้อ: ชั่วโมงท้าย ๆ ที่ยอด AutoPacking รวมกันไม่เกินนี้ถือเป็นยอดเกินมา
    # ตัดไปจบที่ชั่วโมงก่อนหน้า (0 = ไม่ตัด ใช้แค่ชั่วโมงที่มียอดตั้งแต่ 100)
    "title_end_trim_total": "1000",
    # หัวข้อ — ใช้ {date} {start} {end} ได้
    "title_post": "สรุปยอดประจำวันที่ {date} เวลา {start}-{end}",
    "title_autopacking": "อัตราการปล่อยพัสดุ AutoPacking KKN 供包台 日期 ประจำวันที่ {date} เวลา {start}-{end}",
    "title_auto_pda": "อัตราการยิงบรรจุ PDA 人工建包 日期 ประจำวันที่ {date} เวลา {start}-{end}",
    "title_pda_bag": "จำนวนการใช้กระสอบซิปแดง 用于包装的红色拉链袋数量 ประจำวันที่ {date} เวลา {start}-{end}",
    "title_dws_pda": "ยอดแสกนพัสดุลงรถผ่านเครื่อง PDA ประจำวันที่ {date} เวลา {start}-{end}",
    "title_dws": "อัตราการปล่อยพัสดุ DWS KKN 供包台 日期 ประจำวันที่ {date} เวลา {start}-{end}",
    # | = ขึ้นบรรทัดใหม่ในข้อความ
    "title_abnormal": "รายการความผิดปกติ Autopacking KKN ประจำวันที่ {date}|"
                      "พัสดุ Autopacking ที่วิ่งเกินจำนวนรอบทำให้ตก Error และพัสดุที่ตกช่อง Error",
    # ตาราง Error: ว่าง = รวมชีต Abnormal จากไฟล์ AutoPacking รายชั่วโมง (00-23.xlsx)
    # ใส่ไฟล์หรือโฟลเดอร์ของ Abnormal Detail ที่ export แยก (เช่น \\10.30.32.44\...\report\Auto)
    # = ใช้ไฟล์นั้นแทน (มีพัสดุ Intercept ด้วย ยอดจึงตรงกับไฟล์ manual เป๊ะ)
    "abnormal_file": "",
    # ว่าง = ใช้ CHAT_ID ใน [FEISHU]
    "chat_id": "",
    # ส่งอัตโนมัติวันละครั้งตอนเปิด Auto — ตั้งนาทีให้หลังรอบรายชั่วโมงดึงไฟล์ดิบเสร็จ
    # (ปุ่ม "ส่งสรุปตอนนี้" ไม่สนเวลานี้)
    "auto_send": "true",
    "send_time": "12:00",
    # ส่งย้อนหลังเมื่อพลาดเวลาส่ง (เช่น เปิด Auto ช้า): true = ส่งตามได้ภายใน send_late_minutes นาที
    # false = ส่งเฉพาะตรงเวลา (เผื่อ ON_TIME_GRACE_MINUTES) — เกินนั้นต้องกด "ส่งสรุปตอนนี้" เอง
    "send_catch_up": "true",
    "send_late_minutes": "30",
    # วันที่รอบงาน (YYYY-MM-DD) ที่ส่งสำเร็จล่าสุด — บอทเขียนเอง กันส่งซ้ำ
    "last_sent_date": "",
    "send_raw_file": "true",
    # Feishu รับไฟล์แนบไม่เกิน 30MB — เกินจะบีบ zip, zip ยังเกินจะไม่ส่งไฟล์
    "raw_file_max_mb": "30",
    "raw_file_name": "ข้อมูลดิบ สรุปยอด {date}.xlsx",
    # โฟลเดอร์บันทึกไฟล์ Excel: ว่าง = ไม่บันทึก / ใส่โฟลเดอร์ = ทุกครั้งที่ส่ง จะบันทึกไฟล์ Excel
    # ที่ใช้ส่งเข้ากลุ่มไว้ในโฟลเดอร์นี้ตรง ๆ (ชื่อไฟล์มี {date} จึงแยกรายวันอยู่แล้ว)
    "backup_dir": "",
    # true = ใช้ path ไฟล์ดิบแยกจากหน้า ตั้งค่า (ช่องที่ว่างยังใช้ค่าจากหน้า ตั้งค่า)
    "custom_paths": "false",
    "path_autopacking_dir": "",
    "path_auto_pda": "",
    "path_dws_pda": "",
    "path_dws911": "",
    **{f"path_dws{i}": "" for i in range(1, 9)},
}

# ชื่อหัวคอลัมน์ในไฟล์ JMS — ไฟล์ export บางรอบใช้คำต่างกันเล็กน้อย
# JMS header reference and runtime fallback: Chinese first, then Thai aliases.
# 运单号 -> หมายเลข AWB / เลขพัสดุ
# 所属包号 -> หมายเลขกระสอบ
# 扫描时间 -> เวลาที่สแกน / เวลาที่ทำการสแกน / เวลาสแกน
# 扫描员 -> พนักงานสแกน / ผู้สแกน
# 上传时间 -> เวลาอัปโหลด
# Preserve RAW_SCAN_HEADER separately: it controls the exported report labels.
COL_AWB = ("运单号", "หมายเลข AWB", "เลขพัสดุ")
COL_BAG = ("所属包号", "หมายเลขกระสอบ")
COL_TIME = ("扫描时间", "เวลาที่สแกน", "เวลาที่ทำการสแกน", "เวลาสแกน")
COL_SCANNER = ("扫描员", "พนักงานสแกน", "ผู้สแกน")
COL_UPLOAD = ("上传时间", "เวลาอัปโหลด")
RAW_SCAN_HEADER = ["หมายเลข AWB", "หมายเลขกระสอบ", "เวลาที่สแกน", "พนักงานสแกน", "เวลาอัปโหลด"]

# สีเดียวกับ conditional formatting / หัวตารางในไฟล์ manual
SCALE_LOW = (0xED, 0x7B, 0x77)
SCALE_MID = (0xFA, 0xEA, 0x61)
SCALE_HIGH = (0x6C, 0xBF, 0x63)
FILL_NAME_HEADER = (0xA6, 0xA6, 0xA6)
FILL_SHIFT_A = (0xFF, 0xE6, 0x99)
FILL_SHIFT_B = (0xB4, 0xC6, 0xE7)
FILL_HOUR = (0xD9, 0xD9, 0xD9)
FILL_SUBTOTAL = (0xFF, 0xC0, 0x00)
FILL_TOTAL_ROW = (0xD9, 0xD9, 0xD9)
FILL_YELLOW = (0xFF, 0xFF, 0x00)
FILL_RED_HEADER = (0xC0, 0x00, 0x00)
TEXT_RED = (0xFF, 0x00, 0x00)
WHITE = (0xFF, 0xFF, 0xFF)
BLACK = (0x00, 0x00, 0x00)
GRID = (0x1F, 0x23, 0x29)


def _noop(message: str, level: str = "INFO") -> None:
    print(f"[{level}] {message}")


def _always() -> bool:
    return True


def _say(log: LogFunc, message: str, level: str = "INFO") -> None:
    try:
        log(message, level=level)
    except TypeError:
        log(message)


# =====================================================================
# ช่วงเวลา
# =====================================================================

@dataclass
class Window:
    start: datetime
    end: datetime
    hours: List[int]
    shift_b_start: int

    @property
    def business_date(self) -> date:
        return self.start.date()

    def labels(self) -> Dict[str, str]:
        return {
            "date": self.business_date.strftime("%Y/%m/%d"),
            "start": f"{self.start.hour}.00",
            "end": f"{self.end.hour}.00",
        }

    def labels_for_hourly_totals(self, totals: Dict[int, int], minimum: int = 100,
                                 trailing_total: int = 0) -> Dict[str, str]:
        """Return title labels trimmed to complete, populated hourly columns.

        The first and last partial hours of an AutoPacking report frequently
        contain only a small amount of data.  They remain visible in the table,
        but must not make the Feishu title imply a full reporting hour.  A
        total of ``minimum`` or more is considered a populated hour.

        ``trailing_total``: ชั่วโมงท้ายรอบที่ยอดรวมกันไม่เกินค่านี้ = ยอดเกินมาหลังหน้างานจบ
        ตัดออกจากหัวข้อ ให้เวลาสิ้นสุดเป็นชั่วโมงก่อนหน้า (0 = ไม่ใช้กฎนี้)
        """
        populated = [hour for hour in self.hours if totals.get(hour, 0) >= minimum]
        if not populated:
            return self.labels()
        if trailing_total > 0:
            kept = populated[:]
            leftover = sum(totals.get(h, 0) for h in self.hours[self.hours.index(kept[-1]) + 1:])
            while len(kept) > 1 and leftover + totals.get(kept[-1], 0) <= trailing_total:
                leftover += totals.get(kept[-1], 0)
                kept.pop()
            populated = kept
        start = populated[0]
        # The label at the right edge is the beginning of the hour after the
        # final populated column; modulo keeps a 23:00-00:00 boundary correct.
        end = (populated[-1] + 1) % 24
        return {
            "date": self.business_date.strftime("%Y/%m/%d"),
            "start": f"{start}.00",
            "end": f"{end}.00",
        }

    def shift_of(self, hour: int) -> str:
        """กะ A = ตั้งแต่ต้นรอบถึงก่อน shift_b_start, กะ B = shift_b_start ถึงจบรอบ"""
        position = self.hours.index(hour)
        b_position = self.hours.index(self.shift_b_start) if self.shift_b_start in self.hours else len(self.hours)
        return "A" if position < b_position else "B"

    def slot_of(self, moment: datetime) -> Optional[int]:
        """ชั่วโมงที่ moment ตกอยู่ ถ้าอยู่นอกรอบคืน None"""
        if moment < self.start or moment >= self.end:
            return None
        return moment.hour if moment.hour in self.hours else None

    def slot_started(self, hour: int) -> bool:
        """ชั่วโมงนี้เริ่มแล้วหรือยัง ณ เวลาสิ้นสุดของรอบ (ชั่วโมงในอนาคตยังไม่มีข้อมูล)"""
        return self.start + timedelta(hours=self.hours.index(hour)) < self.end


def make_window(start: datetime, end: datetime, day_end_hour: int = 12,
                shift_b_start: int = 1) -> Window:
    start = start.replace(minute=0, second=0, microsecond=0)
    if end <= start:
        raise ValueError(f"ช่วงเวลาไม่ถูกต้อง: {start} -> {end}")
    span = (day_end_hour - start.hour) % 24 or 24
    hours = [(start.hour + i) % 24 for i in range(span)]
    return Window(start=start, end=end, hours=hours, shift_b_start=shift_b_start)


def hour_label(hour: int) -> str:
    return f"{hour}点-{hour + 1}点"


def parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if value in (None, ""):
        return None
    text = str(value).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


# =====================================================================
# อ่านไฟล์ดิบ
# =====================================================================

def read_sheet(path: str, sheet: Optional[str] = None) -> List[list]:
    """อ่านทั้งชีตด้วย calamine (เร็วกว่า openpyxl หลายเท่ากับไฟล์หลายแสนแถว)"""
    from python_calamine import CalamineWorkbook

    workbook = CalamineWorkbook.from_path(path)
    name = sheet if sheet in workbook.sheet_names else workbook.sheet_names[0]
    return workbook.get_sheet_by_name(name).to_python(skip_empty_area=False)


def _find_column(header: Sequence[Any], names: Sequence[str], path: str) -> int:
    cleaned = [str(h or "").strip() for h in header]
    for name in names:
        if name in cleaned:
            return cleaned.index(name)
    # JMS เปลี่ยนชื่อหัวคอลัมน์เป็นระยะ — ลองเทียบแบบไม่สนช่องว่าง/คำว่า "ที่" ก่อนยอมแพ้
    def loose(text: str) -> str:
        return text.replace(" ", "").replace("ที่ทำการ", "").replace("ที่", "")
    loose_cols = [loose(h) for h in cleaned]
    for name in names:
        if loose(name) in loose_cols:
            return loose_cols.index(loose(name))
    found = ", ".join(h for h in cleaned if h)
    raise ValueError(f"{os.path.basename(path)}: ไม่พบคอลัมน์ {' / '.join(names)} "
                     f"(หัวคอลัมน์ในไฟล์: {found})")


@dataclass
class ScanRow:
    awb: Any
    bag: str
    scanned: datetime
    scanner: str
    uploaded: Any

    def as_list(self) -> list:
        return [self.awb, self.bag, self.scanned.strftime("%Y-%m-%d %H:%M:%S"),
                self.scanner, self.uploaded]


def read_scan_file(path: str, window: Window) -> List[ScanRow]:
    """อ่านไฟล์ JMS (DWSXAUTOPDA / DWSPDA) เก็บเฉพาะแถวที่อยู่ในรอบ"""
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"ไม่พบไฟล์ {path}")
    rows = read_sheet(path)
    if not rows:
        return []
    header = rows[0]
    i_awb = _find_column(header, COL_AWB, path)
    i_bag = _find_column(header, COL_BAG, path)
    i_time = _find_column(header, COL_TIME, path)
    i_scanner = _find_column(header, COL_SCANNER, path)
    try:
        i_upload: Optional[int] = _find_column(header, COL_UPLOAD, path)
    except ValueError:
        i_upload = None

    result: List[ScanRow] = []
    for row in rows[1:]:
        if len(row) <= max(i_awb, i_bag, i_time, i_scanner):
            continue
        scanned = parse_dt(row[i_time])
        if scanned is None or window.slot_of(scanned) is None:
            continue
        uploaded = row[i_upload] if i_upload is not None and i_upload < len(row) else ""
        if isinstance(uploaded, datetime):
            uploaded = uploaded.strftime("%Y-%m-%d %H:%M:%S")
        result.append(ScanRow(
            awb=row[i_awb],
            bag=str(row[i_bag] or "").strip(),
            scanned=scanned,
            scanner=str(row[i_scanner] or "").strip(),
            uploaded=uploaded,
        ))
    return result


def machine_names(prefix: str, count: int) -> List[str]:
    return [f"{prefix}{i:02d}" for i in range(1, count + 1)]


def hourly_counts(rows: Iterable[ScanRow], names: Sequence[str],
                  window: Window) -> Dict[str, Dict[int, int]]:
    """นับจำนวนแถวต่อเครื่องต่อชั่วโมง (ตรงกับ COUNTIFS ในไฟล์ manual)"""
    lookup = {name.lower(): name for name in names}
    counts: Dict[str, Dict[int, int]] = {name: {h: 0 for h in window.hours} for name in names}
    for row in rows:
        name = lookup.get(row.scanner.lower())
        if name is None:
            continue
        slot = window.slot_of(row.scanned)
        if slot is not None:
            counts[name][slot] += 1
    return counts


def unique_bag_rows(rows: Iterable[ScanRow]) -> List[ScanRow]:
    """เก็บแถวแรกของแต่ละเลขกระสอบทั้งไฟล์ — เหมือน Table.Distinct ใน query PDA BAG DATA

    กระสอบที่หลายเครื่องยิงจึงนับให้เครื่องแรกที่เจอในไฟล์เครื่องเดียว
    (เทียบเลขกระสอบตรงตัวเหมือน Power Query)
    """
    seen: set = set()
    result: List[ScanRow] = []
    for row in rows:
        bag = row.bag
        if bag in seen:
            continue
        seen.add(bag)
        result.append(row)
    return result


def bag_counts(rows: Iterable[ScanRow], names: Sequence[str]) -> Dict[str, int]:
    """นับกระสอบที่ขึ้นต้นด้วย B ต่อเครื่อง หลังตัดเลขกระสอบซ้ำทั้งไฟล์
    (ตรงกับ COUNTIFS บน PDA BAG DATA ในไฟล์ manual)"""
    lookup = {name.lower(): name for name in names}
    counts: Dict[str, int] = {name: 0 for name in names}
    for row in unique_bag_rows(rows):
        name = lookup.get(row.scanner.lower())
        if name is not None and row.bag.upper().startswith("B"):
            counts[name] += 1
    return counts


# =====================================================================
# วาดตาราง
# =====================================================================

class Fonts:
    """ฟอนต์ไทย + จีน ในรูปเดียวกัน — ไม่มีฟอนต์ตัวเดียวบน Windows ที่มีครบทั้งสองภาษา
    จึงแยกข้อความเป็นช่วงตามภาษาแล้ววาดทีละช่วง"""

    # ฟอนต์ตามไฟล์ Excel ต้นฉบับ: ตาราง PDA ใช้ Microsoft JhengHei, ตารางอื่นใช้ Microsoft YaHei
    # ตัวอักษรไทย Excel แสดงด้วย Tahoma
    THAI = ("tahoma.ttf", "LeelawUI.ttf", "leelawad.ttf")
    THAI_BOLD = ("tahomabd.ttf", "LeelaUIb.ttf", "leelawdb.ttf")
    FAMILIES = {
        "yahei": (("msyh.ttc", "simhei.ttf"), ("msyhbd.ttc", "msyh.ttc")),
        "jhenghei": (("msjh.ttc", "msyh.ttc"), ("msjhbd.ttc", "msyhbd.ttc")),
        "tahoma": (("tahoma.ttf", "msyh.ttc"), ("tahomabd.ttf", "msyhbd.ttc")),
    }

    def __init__(self) -> None:
        self._cache: Dict[Tuple[str, int, bool, str], Any] = {}
        self.font_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

    def _load(self, candidates: Sequence[str], size: int):
        for name in candidates:
            path = os.path.join(self.font_dir, name)
            if os.path.isfile(path):
                return ImageFont.truetype(path, size)
        return ImageFont.load_default()

    def get(self, script: str, size: int, bold: bool, family: str = "yahei"):
        key = (script, size, bold, family)
        if key not in self._cache:
            if script == "thai":
                names = self.THAI_BOLD if bold else self.THAI
            elif script == "han" and family == "tahoma":  # Tahoma ไม่มีตัวจีน
                regular, heavy = self.FAMILIES["yahei"]
                names = heavy if bold else regular
            else:
                regular, heavy = self.FAMILIES.get(family, self.FAMILIES["yahei"])
                names = heavy if bold else regular
            self._cache[key] = self._load(names, size)
        return self._cache[key]

    @staticmethod
    def runs(text: str) -> List[Tuple[str, str]]:
        result: List[Tuple[str, str]] = []
        for ch in text:
            if "\u0e00" <= ch <= "\u0e7f":
                script = "thai"
            elif "\u2e80" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef":
                script = "han"
            else:
                script = "cjk"
            if result and result[-1][0] == script:
                result[-1] = (script, result[-1][1] + ch)
            else:
                result.append((script, ch))
        return result

    def width(self, text: str, size: int, bold: bool = False, family: str = "yahei") -> float:
        return sum(self.get(s, size, bold, family).getlength(part) for s, part in self.runs(text))

    def draw(self, draw: ImageDraw.ImageDraw, x: float, y_center: float, text: str,
             size: int, bold: bool = False, fill=BLACK, family: str = "yahei") -> None:
        for script, part in self.runs(text):
            font = self.get(script, size, bold, family)
            draw.text((x, y_center), part, font=font, fill=fill, anchor="lm")
            x += font.getlength(part)


_FONTS: Optional[Fonts] = None


def fonts() -> Fonts:
    global _FONTS
    if _FONTS is None:
        _FONTS = Fonts()
    return _FONTS


def _percentile_inc(values: Sequence[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    pos = (len(ordered) - 1) * pct
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def color_scale(values: Sequence[float]) -> Callable[[float], Tuple[int, int, int]]:
    """color scale 3 สีแบบ Excel: min=แดง, percentile 50=เหลือง, max=เขียว"""
    if not values:
        return lambda v: SCALE_HIGH
    lo, hi = min(values), max(values)
    mid = _percentile_inc(values, 0.5)

    def lerp(a, b, t):
        t = max(0.0, min(1.0, t))
        return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))

    def pick(v: float):
        if hi == lo:
            return SCALE_HIGH
        if v <= mid:
            return SCALE_LOW if mid == lo else lerp(SCALE_LOW, SCALE_MID, (v - lo) / (mid - lo))
        return SCALE_HIGH if hi == mid else lerp(SCALE_MID, SCALE_HIGH, (v - mid) / (hi - mid))

    return pick


@dataclass
class Cell:
    text: str = ""
    fill: Tuple[int, int, int] = WHITE
    bold: bool = False
    align: str = "center"
    color: Tuple[int, int, int] = BLACK
    size: Optional[int] = None  # ขนาดฟอนต์เฉพาะเซลล์ (ก่อนคูณ scale)
    family: Optional[str] = None  # ฟอนต์เฉพาะเซลล์ (ว่าง = ตามตาราง)


@dataclass
class Grid:
    """ตารางแบบง่าย: col_widths + แถว (ความสูง, เซลล์) + เซลล์ผสาน"""
    col_widths: List[int]
    rows: List[Tuple[int, List[Optional[Cell]]]] = field(default_factory=list)
    merges: List[Tuple[int, int, int, int]] = field(default_factory=list)  # r1, c1, r2, c2
    family: str = "yahei"

    def add_row(self, height: int, cells: List[Optional[Cell]]) -> int:
        self.rows.append((height, cells))
        return len(self.rows) - 1


def render_grid(grid: Grid, out_path: str, font_size: int = 14, scale: int = 2,
                max_tall_ratio: Optional[float] = None, margin: int = 6,
                capture: Optional[list] = None) -> str:
    """max_tall_ratio: ถ้ารูปสูงเกิน (สูง/กว้าง) เท่านี้ จะเติมพื้นขาวด้านขวาให้กว้างขึ้น
    เพราะ Feishu ตัดรูปที่สูงยาวทิ้งตอนแสดงใน post — ตารางยังอยู่ชิดซ้ายเหมือนเดิม

    เส้นตารางเป็นเส้นเดียวสีเข้มหนา 1 พิกเซลแบบเส้น thin ของ Excel — วาดสีพื้นทุกช่องก่อน
    แล้วค่อยวาดเส้น จะได้ไม่มีเส้นของสองช่องซ้อนกันจนหนา และเว้นขอบขาวรอบรูป
    """
    if capture is not None:
        capture.append(grid)
    f = fonts()
    size = font_size * scale
    edge = margin * scale
    xs = [edge]
    for w in grid.col_widths:
        xs.append(xs[-1] + w * scale)
    ys = [edge]
    for height, _ in grid.rows:
        ys.append(ys[-1] + height * scale)
    width = xs[-1] + edge
    if max_tall_ratio:
        width = max(width, int((ys[-1] + edge) / max_tall_ratio))
    image = Image.new("RGB", (width, ys[-1] + edge), WHITE)
    draw = ImageDraw.Draw(image)

    covered: Dict[Tuple[int, int], Tuple[int, int, int, int]] = {}
    for r1, c1, r2, c2 in grid.merges:
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                covered[(r, c)] = (r1, c1, r2, c2)

    placed: List[Tuple[Tuple[int, int, int, int], Cell]] = []
    for r, (_, cells) in enumerate(grid.rows):
        for c in range(len(grid.col_widths)):
            area = covered.get((r, c))
            if area and (area[0], area[1]) != (r, c):
                continue
            r2, c2 = (area[2], area[3]) if area else (r, c)
            cell = cells[c] if c < len(cells) else None
            placed.append(((xs[c], ys[r], xs[c2 + 1], ys[r2 + 1]), cell or Cell()))
    for box, cell in placed:
        draw.rectangle(box, fill=cell.fill)
    for box, _ in placed:
        draw.rectangle(box, outline=GRID, width=1)

    pad = 6 * scale
    for box, cell in placed:
        if not cell.text:
            continue
        family = cell.family or grid.family
        cell_size = cell.size * scale if cell.size else size
        lines = cell.text.split("\n")
        line_height = cell_size * 1.35
        top = (box[1] + box[3]) / 2 - line_height * (len(lines) - 1) / 2
        for i, text_line in enumerate(lines):
            text_width = f.width(text_line, cell_size, cell.bold, family)
            if cell.align == "left":
                x = box[0] + pad
            elif cell.align == "right":
                x = box[2] - pad - text_width
            else:
                x = (box[0] + box[2] - text_width) / 2
            f.draw(draw, x, top + i * line_height, text_line, cell_size, cell.bold, cell.color, family)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    image.save(out_path, "PNG", optimize=True)
    return out_path


def _num(value: int, dash_zero: bool = False) -> str:
    if dash_zero and not value:
        return "-"
    return f"{value:,}"


def render_hourly_table(counts: Dict[str, Dict[int, int]], window: Window,
                        out_path: str, name_header: str = "พนักงานสแกน",
                        bold_values: bool = False, capture: Optional[list] = None) -> str:
    """ตารางรายชั่วโมงแบบหน้า AUTO PDA / DWS PDA ในไฟล์ manual

    ฟอนต์ Microsoft JhengHei เหมือนชีตต้นฉบับ (AUTO PDA ตัวเลขตัวหนา, DWS PDA ตัวปกติ)
    """
    names = list(counts)
    hours_a = [h for h in window.hours if window.shift_of(h) == "A"]
    hours_b = [h for h in window.hours if window.shift_of(h) == "B"]

    # คอลัมน์: ชื่อ | ชม.กะ A... | 合计 | ชม.กะ B... | 合计 | Total
    columns: List[Tuple[str, Any]] = [("name", None)]
    columns += [("hour", h) for h in hours_a] + [("sum", "A")]
    if hours_b:
        columns += [("hour", h) for h in hours_b] + [("sum", "B")]
    columns.append(("total", None))
    widths = [180 if kind == "name" else (80 if kind == "hour" else 70) for kind, _ in columns]
    grid = Grid(widths, family="jhenghei")
    last = len(columns) - 1

    grid.add_row(23, [Cell(window.business_date.isoformat(), WHITE, True)] + [None] * last)
    grid.merges.append((0, 0, 0, last))

    shift_row: List[Optional[Cell]] = [Cell(name_header, FILL_NAME_HEADER, True)]
    hour_row: List[Optional[Cell]] = [None]
    for kind, key in columns[1:]:
        if kind == "hour":
            fill = FILL_SHIFT_A if window.shift_of(key) == "A" else FILL_SHIFT_B
            shift_row.append(Cell("", fill))
            hour_row.append(Cell(hour_label(key), FILL_HOUR, True, size=12))
        elif kind == "sum":
            shift_row.append(Cell("", FILL_SHIFT_A if key == "A" else FILL_SHIFT_B))
            hour_row.append(Cell("合计", FILL_SUBTOTAL, True))
        else:
            shift_row.append(Cell("Total", FILL_NAME_HEADER, True))
            hour_row.append(None)
    grid.add_row(20, shift_row)
    grid.add_row(20, hour_row)
    a_first, a_last = 1, len(hours_a) + 1
    grid.merges += [(1, 0, 2, 0), (1, last, 2, last), (1, a_first, 1, a_last)]
    shift_row[a_first] = Cell("Shift A", FILL_SHIFT_A, True)
    if hours_b:
        b_first, b_last = a_last + 1, last - 1
        grid.merges.append((1, b_first, 1, b_last))
        shift_row[b_first] = Cell("Shift B", FILL_SHIFT_B, True)

    scale = color_scale([counts[n][h] for n in names for h in window.hours])
    column_totals = [0] * len(columns)
    for name in names:
        row: List[Optional[Cell]] = [Cell(name, WHITE)]
        sums = {"A": 0, "B": 0}
        for idx, (kind, key) in enumerate(columns[1:], start=1):
            if kind == "hour":
                value = counts[name][key]
                sums[window.shift_of(key)] += value
                row.append(Cell(_num(value), scale(value), bold_values))
            elif kind == "sum":
                value = sums[key]
                row.append(Cell(_num(value, True), WHITE))
            else:
                value = sums["A"] + sums["B"]
                row.append(Cell(_num(value, True), WHITE))
            column_totals[idx] += value
        grid.add_row(19, row)

    total_row: List[Optional[Cell]] = [Cell("Total", FILL_TOTAL_ROW, True)]
    for idx in range(1, len(columns)):
        total_row.append(Cell(_num(column_totals[idx], True), FILL_TOTAL_ROW, True))
    grid.add_row(20, total_row)
    return render_grid(grid, out_path, font_size=14, capture=capture)


def render_bag_table(counts: Dict[str, int], out_path: str, capture: Optional[list] = None) -> str:
    """ตารางกระสอบซิปแดงแบบหน้า PDA BAG ในไฟล์ manual (แถวยาวเรียงลงมา หัว/ผลรวมสีเหลือง)

    ขนาดเท่ารูปต้นฉบับ (~530 x 1,410 px, สูง:กว้าง ~2.7) — Feishu ตัดท้ายรูปใน post
    ที่สูงเกินราว 3 เท่าของความกว้าง (รูป 744 x 2,378 ถูกตัด แต่ต้นฉบับแสดงเต็ม)
    """
    grid = Grid([240, 290])
    grid.add_row(30, [Cell("เครื่องแสกน", FILL_YELLOW, True), Cell("จำนวนกระสอบ", FILL_YELLOW, True)])
    for name, value in counts.items():
        grid.add_row(30, [Cell(name, WHITE, False, "left"), Cell(str(value), WHITE, False, "right")])
    grid.add_row(30, [Cell("ผลรวมทั้งหมด", FILL_YELLOW, False, "left"),
                      Cell(str(sum(counts.values())), FILL_YELLOW, False, "right")])
    return render_grid(grid, out_path, font_size=20, scale=1, margin=2, capture=capture)


# =====================================================================
# ตาราง DWS (ชีต DWSREALTIME ในไฟล์ "2_DWS & PDA (Network Sharing)")
# =====================================================================

DWS_SORT_IPS = {"DWS_09": "10.30.32.220", "DWS_10": "10.30.32.221", "DWS_11": "10.30.32.222"}
FILL_DWS_LABEL = (0xF7, 0x96, 0x46)
FILL_PDA_LABEL = (0xC9, 0xC9, 0xC9)
FILL_DARK_HEADER = (0x80, 0x80, 0x80)
FILL_GOLD = (0xFF, 0xC0, 0x00)
FILL_GOLD_LIGHT = (0xFF, 0xE6, 0x99)
FILL_BLUE_LIGHT = (0xBD, 0xD7, 0xEE)
THRESHOLD = 200  # ชั่วโมงที่ยอดเกินนี้ถึงนับเป็นชั่วโมงทำงาน (สูตรใน Excel ใช้ ">200")


def count_by_hour(times: Iterable[datetime], window: Window) -> Dict[int, int]:
    counts = {h: 0 for h in window.hours}
    for moment in times:
        slot = window.slot_of(moment)
        if slot is not None:
            counts[slot] += 1
    return counts


def read_time_column(path: str, column: str, filter_column: Optional[str] = None,
                     filter_value: Optional[str] = None) -> List[datetime]:
    """อ่านคอลัมน์เวลาทั้งไฟล์ (กรองตามอีกคอลัมน์ได้) — ไฟล์ DWS realtime / DWS9-11"""
    rows = read_sheet(path)
    if not rows:
        return []
    i_time = _find_column(rows[0], (column,), path)
    i_filter = _find_column(rows[0], (filter_column,), path) if filter_column else None
    result: List[datetime] = []
    for row in rows[1:]:
        if len(row) <= i_time:
            continue
        if i_filter is not None and str(row[i_filter] if i_filter < len(row) else "").strip() != filter_value:
            continue
        moment = parse_dt(row[i_time])
        if moment is not None:
            result.append(moment)
    return result


@dataclass
class DwsTable:
    window: Window
    rows: List[Tuple[str, str, Dict[int, int]]]  # (ชื่อ, กลุ่ม "dws"/"pda", ยอดรายชั่วโมง)


def _shift_hours(window: Window, shift: str) -> List[int]:
    return [h for h in window.hours if window.shift_of(h) == shift]


def dws_row_stats(values: Dict[int, int], window: Window) -> Dict[str, float]:
    """คอลัมน์ทางขวาของแต่ละแถว ตามสูตรในชีต DWSREALTIME"""
    a = sum(values[h] for h in _shift_hours(window, "A") if values[h] > THRESHOLD)
    b = sum(values[h] for h in _shift_hours(window, "B") if values[h] > THRESHOLD)
    effective = sum(1 for h in window.hours if values[h] > THRESHOLD)
    peak = max(values.values()) if values else 0
    total = a + b
    avg = total / effective if effective else 0
    return {"A": a, "B": b, "total": total, "effective": effective, "avg": avg,
            "peak": peak if peak > THRESHOLD else 0, "pcs": avg}


def dws_summary_rows(table: DwsTable) -> Dict[str, Dict[str, Any]]:
    """แถว 合计 และ PCS/Hr ตามสูตรในชีต DWSREALTIME"""
    window = table.window
    sums = {h: sum(values[h] for _, _, values in table.rows) for h in window.hours}
    a = sum(sums[h] for h in _shift_hours(window, "A"))
    b = sum(sums[h] for h in _shift_hours(window, "B"))
    effective = sum(1 for h in window.hours if sums[h] > THRESHOLD)
    peak = max(sums.values()) if sums else 0
    total_row = {"hours": sums, "A": a, "B": b, "total": a + b, "effective": effective,
                 "avg": (a + b) / effective if effective else 0,
                 "peak": peak if peak > THRESHOLD else 0}
    total_row["pcs"] = total_row["avg"]

    pcs: Dict[int, float] = {}
    for h in window.hours:
        active = sum(1 for _, _, values in table.rows if values[h] > 3)
        pcs[h] = sums[h] / active if sums[h] > 3 and active else 0
    a_nonzero = [pcs[h] for h in _shift_hours(window, "A") if pcs[h]]
    b_cells = [values[h] for _, _, values in table.rows for h in _shift_hours(window, "B")
               if values[h] > THRESHOLD]
    all_nonzero = [v for v in pcs.values() if v]
    pcs_row = {"hours": pcs,
               "A": int(sum(a_nonzero) / len(a_nonzero)) if a_nonzero else 0,
               "B": int(sum(b_cells) / len(b_cells)) if b_cells else 0,
               "total": int(sum(all_nonzero) / len(all_nonzero)) if all_nonzero else 0}
    return {"total": total_row, "pcs": pcs_row}


def render_dws_table(table: DwsTable, out_path: str, capture: Optional[list] = None) -> str:
    window = table.window
    hours_a, hours_b = _shift_hours(window, "A"), _shift_hours(window, "B")
    columns: List[Tuple[str, Any]] = [("name", None)] + [("hour", h) for h in hours_a] + [("A", None)]
    columns += [("hour", h) for h in hours_b] + [("B", None)]
    columns += [("total", None), ("effective", None), ("avg", None), ("peak", None), ("pcs", None)]
    widths = [110 if kind == "name" else (76 if kind == "hour" else 72) for kind, _ in columns]
    grid = Grid(widths, family="yahei")
    last = len(columns) - 1
    grid.add_row(23, [Cell(window.business_date.isoformat(), WHITE, True, family="jhenghei")] + [None] * last)
    grid.merges.append((0, 0, 0, last))

    titles = {"name": "供件台", "A": "A", "B": "B", "total": "合计", "effective": "Effective\nTime",
              "avg": "Avg.\nEfficiency", "peak": "Peak\nEfficiency", "pcs": "PCS/Hr"}
    header: List[Optional[Cell]] = []
    for kind, key in columns:
        if kind == "hour":
            header.append(Cell(hour_label(key), FILL_DARK_HEADER, True, size=11, family="jhenghei"))
        elif kind in ("A", "B"):
            header.append(Cell(titles[kind], FILL_GOLD, True, family="jhenghei"))
        else:
            header.append(Cell(titles[kind], FILL_DARK_HEADER, True,
                               size=9 if "\n" in titles[kind] else None, family="jhenghei"))
    grid.add_row(30, header)

    scales = {}
    for group in ("dws", "pda"):
        values = [v[h] for _, g, v in table.rows if g == group for h in window.hours]
        scales[group] = color_scale(values)
    for name, group, values in table.rows:
        stats = dws_row_stats(values, window)
        row: List[Optional[Cell]] = [Cell(name, FILL_DWS_LABEL if group == "dws" else FILL_PDA_LABEL,
                                          family="jhenghei")]
        for kind, key in columns[1:]:
            if kind == "hour":
                row.append(Cell(_num(values[key]), scales[group](values[key]), group == "dws"))
            else:
                row.append(Cell(_num(round(stats[kind])), WHITE))
        grid.add_row(19, row)

    summary = dws_summary_rows(table)
    total, pcs = summary["total"], summary["pcs"]
    row = [Cell("合计", WHITE, True, family="jhenghei")]
    for kind, key in columns[1:]:
        if kind == "hour":
            row.append(Cell(_num(total["hours"][key], True), WHITE, True))
        else:
            fill = FILL_GOLD_LIGHT if kind == "A" else (FILL_BLUE_LIGHT if kind == "B" else WHITE)
            row.append(Cell(_num(round(total[kind])), fill))
    grid.add_row(20, row)
    row = [Cell("PCS/Hr", WHITE, family="jhenghei")]
    for kind, key in columns[1:]:
        if kind == "hour":
            row.append(Cell(f"{pcs['hours'][key]:.0f}", WHITE))
        elif kind in ("A", "B", "total"):
            fill = FILL_GOLD_LIGHT if kind == "A" else (FILL_BLUE_LIGHT if kind == "B" else WHITE)
            row.append(Cell(_num(pcs[kind]), fill))
        else:
            row.append(Cell("", WHITE))
    grid.add_row(20, row)
    return render_grid(grid, out_path, font_size=13, capture=capture)


def dws_station_paths(settings: "Settings") -> Dict[str, str]:
    """ไฟล์ realtime ของ DWS1-8: ไฟล์ที่บอท mirror มาไว้ก่อน ถ้าไม่มีใช้ path บน share ของเครื่อง"""
    mirrored = {station: path for station, path in settings.dws_files}
    result: Dict[str, str] = {}
    for i in range(1, 9):
        station = f"DWS{i}"
        custom = settings.custom_path(f"dws{i}")
        if custom:
            result[station] = custom
            continue
        path = mirrored.get(station, "")
        if not path or not os.path.isfile(path):
            try:
                from core import dws_mirror
                path = dws_mirror._source_file(dws_mirror._yaml_source(station)) or path
            except Exception:
                pass
        result[station] = path
    return result


def build_dws_table(settings: "Settings", window: Window, dws_pda_rows: List[ScanRow],
                    log: LogFunc = _noop) -> Tuple[DwsTable, List[str]]:
    warnings: List[str] = []
    rows: List[Tuple[str, str, Dict[int, int]]] = []
    for station, path in dws_station_paths(settings).items():
        name = f"DWS_{station[3:].zfill(2)}"
        times: List[datetime] = []
        if path and os.path.isfile(path):
            try:
                times = read_time_column(path, "出秤时间")
            except Exception as exc:
                warnings.append(f"{name}: อ่านไฟล์ไม่ได้ ({exc})")
        else:
            warnings.append(f"{name}: ไม่พบไฟล์ {path or '(ไม่ได้ตั้งค่า)'}")
        rows.append((name, "dws", count_by_hour(times, window)))
    dws911 = settings.dws911_file
    for name, ip in DWS_SORT_IPS.items():
        times = []
        try:
            times = read_time_column(dws911, "扫描时间", "dws序号", ip)
        except Exception as exc:
            warnings.append(f"{name}: อ่าน {settings.name_dws} ไม่ได้ ({exc})")
        rows.append((name, "dws", count_by_hour(times, window)))
    pda_names = machine_names(settings.opt("dws_pda_prefix"), settings.opt_int("dws_pda_count"))
    pda_counts = hourly_counts(dws_pda_rows, pda_names, window)
    for name in pda_names:
        rows.append((name.replace("Dws_pda", "DWS_PDA"), "pda", pda_counts[name]))
    for message in warnings:
        _say(log, f"Daily summary: {message}", "WARN")
    return DwsTable(window, rows), warnings


# =====================================================================
# ตาราง AutoPacking (ชีต Autoformat ในไฟล์ 1_AUTOPACKING_BOT.xlsx) เฉพาะ A2:AK32
# =====================================================================

# (ชื่อกลุ่ม, Pipeline, จำนวน feeder, เลขที่แสดงของ feeder แรก)
AUTOPACK_LINES = [("AP 1", "58987-001", 12, 1), ("AP 2", "58987-002", 11, 13)]
FILL_AP_LABEL = (0xD9, 0xE1, 0xF2)
FILL_FEEDER = (0xF7, 0x96, 0x46)
FILL_AP_HEADER = (0xDE, 0xE0, 0xE3)
FILL_AP_SUM = (0xFB, 0xBF, 0xBC)
FILL_AP_TOTAL_COL = (0xFF, 0xF2, 0xE8)
FILL_AP_YELLOW = (0xFF, 0xF2, 0x58)
FILL_AP_A = (0xFA, 0xF1, 0xD1)
FILL_AP_B = (0xFE, 0xD4, 0xA4)
FILL_AP_B_HEADER = (0xFF, 0x88, 0x00)
TEXT_AP_RED = (0xF5, 0x4A, 0x45)


@dataclass
class AutopackTable:
    window: Window
    # กลุ่ม -> [(เลข feeder ที่แสดง, ยอดรายชั่วโมง)]
    lines: List[Tuple[str, List[Tuple[int, Dict[int, int]]]]]


def autopack_feeder_counts(throughput_by_hour: Dict[int, List[list]], window: Window) -> AutopackTable:
    """throughput_by_hour: ชั่วโมงข้อมูล -> แถวชีต Throughput (Pipeline, Feeder No, Loading Num, ...)

    ใช้ค่าแถวแรกที่เจอของ Pipeline+Feeder (เหมือน INDEX(FILTER(...),1) ในชีตรายชั่วโมง)
    """
    lines = []
    for label, pipeline, feeders, first_no in AUTOPACK_LINES:
        rows = []
        for feeder in range(1, feeders + 1):
            values = {h: 0 for h in window.hours}
            for hour, data in throughput_by_hour.items():
                if hour not in values:
                    continue
                for record in data:
                    if len(record) < 3 or str(record[0]).strip() != pipeline:
                        continue
                    try:
                        if int(float(record[1])) != feeder:
                            continue
                        values[hour] = int(float(record[2] or 0))
                    except (TypeError, ValueError):
                        continue
                    break
            rows.append((first_no + feeder - 1, values))
        lines.append((label, rows))
    return AutopackTable(window, lines)


def read_autopack_throughput(raw_dir: str, window: Window, file_hour_offset: int = -1) -> Dict[int, List[list]]:
    """ไฟล์ NN.xlsx เก็บยอดของชั่วโมง NN-1 — อ่านเฉพาะชั่วโมงที่เริ่มแล้ว"""
    from python_calamine import CalamineWorkbook

    folder = autopacking_folder(raw_dir, window.business_date) if raw_dir else None
    result: Dict[int, List[list]] = {}
    if folder is None:
        return result
    for hour in window.hours:
        if not window.slot_started(hour):
            continue
        path = os.path.join(folder, f"{(hour - file_hour_offset) % 24:02d}.xlsx")
        if not os.path.isfile(path):
            continue
        workbook = CalamineWorkbook.from_path(path)
        if "Throughput" not in workbook.sheet_names:
            continue
        data = workbook.get_sheet_by_name("Throughput").to_python(skip_empty_area=False)
        result[hour] = [list(r) for r in data[1:]]
    return result


def _ap_feeder_stats(values: Dict[int, int], window: Window) -> Dict[str, float]:
    hours_a, hours_b = _shift_hours(window, "A"), _shift_hours(window, "B")
    total = sum(values.values())
    effective = sum(1 for v in values.values() if v > THRESHOLD)
    a = sum(values[h] for h in hours_a)
    b = sum(values[h] for h in hours_b)
    a_eff = sum(1 for h in hours_a if values[h] > THRESHOLD)
    b_eff = sum(1 for h in hours_b if values[h] > THRESHOLD)
    return {"total": total, "effective": effective, "avg": total / effective if effective else 0,
            "peak": max(values.values()) if values else 0,
            "A": a, "A_avg": a / a_eff if a_eff else 0, "B": b, "B_avg": b / b_eff if b_eff else 0}


def autopack_summary(table: AutopackTable) -> Dict[str, Any]:
    """ค่าทุกแถวสรุปของตาราง (Avg/Hr ต่อสาย, 合计 ต่อสาย, Avg., ผลรวม, เครื่องที่เยอะสุด)"""
    window = table.window
    hours_a, hours_b = _shift_hours(window, "A"), _shift_hours(window, "B")
    out: Dict[str, Any] = {"lines": []}
    all_feeders = []
    for label, rows in table.lines:
        stats = [(no, values, _ap_feeder_stats(values, window)) for no, values in rows]
        all_feeders += stats
        sums = {h: sum(v[h] for _, v, _ in stats) for h in window.hours}
        avg_hr = {h: sums[h] / sum(1 for _, v, _ in stats if v[h] > THRESHOLD)
                  if any(v[h] > THRESHOLD for _, v, _ in stats) else 0 for h in window.hours}
        a_cells = [v[h] for _, v, _ in stats for h in hours_a if v[h] > THRESHOLD]
        b_cells = [v[h] for _, v, _ in stats for h in hours_b if v[h] > THRESHOLD]
        a_sum = sum(s["A"] for _, _, s in stats)
        b_sum = sum(s["B"] for _, _, s in stats)
        total = sum(s["total"] for _, _, s in stats)
        effective = sum(s["effective"] for _, _, s in stats)
        out["lines"].append({
            "label": label, "feeders": stats, "sums": sums, "avg_hr": avg_hr,
            "max": {k: max((s[k] for _, _, s in stats), default=0) for k in ("total", "effective", "avg", "peak")},
            "A": a_sum, "A_avg": a_sum / len(a_cells) if a_cells else 0,
            "B": b_sum, "B_avg": b_sum / len(b_cells) if b_cells else 0,
            "sum_total": total, "sum_effective": effective,
            "sum_avg": total / effective if effective else 0,
            "sum_peak": max(sums.values()) if sums else 0,
        })
    grand = {h: sum(line["sums"][h] for line in out["lines"]) for h in window.hours}
    active = {h: sum(1 for _, v, _ in all_feeders if v[h] > THRESHOLD) for h in window.hours}
    out["avg"] = {h: grand[h] / active[h] if active[h] else 0 for h in window.hours}
    out["avg_max"] = {k: max(line["max"][k] for line in out["lines"]) for k in ("total", "effective", "avg", "peak")}
    total = sum(line["sum_total"] for line in out["lines"])
    effective = sum(line["sum_effective"] for line in out["lines"])
    out["grand"] = {"hours": grand, "total": total, "effective": effective,
                    "avg": total / effective if effective else 0,
                    "peak": max(grand.values()) if grand else 0}
    best_a = max(all_feeders, key=lambda f: f[2]["A"], default=None)
    best_b = max(all_feeders, key=lambda f: f[2]["B"], default=None)
    out["best"] = {
        "A": (best_a[2]["A"], best_a[0], best_a[2]["A_avg"]) if best_a else (0, "", 0),
        "B": (best_b[2]["B"], best_b[0], best_b[2]["B_avg"]) if best_b else (0, "", 0),
    }
    return out


def render_autopack_table(table: AutopackTable, out_path: str, capture: Optional[list] = None) -> str:
    window = table.window
    summary = autopack_summary(table)
    hours = window.hours
    n_hours = len(hours)
    # คอลัมน์: 分区 | 供件台 | ชั่วโมง... | (ช่องว่าง) | 合计 | Effective | Avg | Peak | A | เครื่อง | Avg/Hr | B | เครื่อง | Avg/Hr
    widths = [104, 58] + [66] * n_hours + [10, 76, 76, 70, 70, 76, 54, 68, 76, 54, 68]
    c_gap = 2 + n_hours
    c_total, c_eff, c_avg, c_peak = c_gap + 1, c_gap + 2, c_gap + 3, c_gap + 4
    c_a, c_a_no, c_a_avg, c_b, c_b_no, c_b_avg = range(c_gap + 5, c_gap + 11)
    ncols = len(widths)
    grid = Grid(widths)

    def blank(fill=WHITE) -> List[Optional[Cell]]:
        return [Cell("", fill) for _ in range(ncols)]

    def red_if(flag: bool, cell: Cell) -> Cell:
        if flag:
            cell.color = TEXT_AP_RED
        return cell

    row = blank()
    row[0] = Cell(window.business_date.isoformat(), WHITE, False, size=16)
    row[c_a] = Cell("ยอดปล่อยพัสดุกะ A และ B", WHITE, True, size=15)
    r = grid.add_row(32, row)
    grid.merges += [(r, 0, r, c_peak), (r, c_a, r, c_b_avg)]

    row = blank(FILL_AP_HEADER)
    row[0] = Cell("分区", WHITE, True)
    row[1] = Cell("供件台", FILL_AP_HEADER, True)
    for i, h in enumerate(hours):
        row[2 + i] = Cell(hour_label(h), FILL_AP_HEADER, True, size=10)
    row[c_gap] = Cell("", WHITE)
    for col, text in ((c_total, "合计"), (c_eff, "有效操作时长\nEffective Time"),
                      (c_avg, "平均效率\nAvg."), (c_peak, "峰值效率\nPeak")):
        row[col] = Cell(text, FILL_AP_HEADER, True, size=9 if "\n" in text else None)
    for col, text, fill in ((c_a, "A", FILL_AP_YELLOW), (c_a_no, "เครื่อง", FILL_AP_YELLOW),
                            (c_a_avg, "Avg/Hr", FILL_AP_YELLOW), (c_b, "B", FILL_AP_B_HEADER),
                            (c_b_no, "เครื่อง", FILL_AP_B_HEADER), (c_b_avg, "Avg/Hr", FILL_AP_B_HEADER)):
        row[col] = Cell(text, fill, True)
    grid.add_row(29, row)

    best_a_total = max((s["A"] for line in summary["lines"] for _, _, s in line["feeders"]), default=0)
    best_b_total = max((s["B"] for line in summary["lines"] for _, _, s in line["feeders"]), default=0)
    for index, line in enumerate(summary["lines"]):
        values_all = [v[h] for _, v, _ in line["feeders"] for h in hours]
        scale = color_scale(values_all)
        first = len(grid.rows)
        for no, values, stats in line["feeders"]:
            row = blank()
            row[0] = Cell(line["label"], FILL_AP_LABEL)
            row[1] = Cell(str(no), FILL_FEEDER)
            for i, h in enumerate(hours):
                row[2 + i] = Cell(_num(values[h]), scale(values[h]), align="right")
            m = line["max"]
            row[c_total] = red_if(stats["total"] == m["total"] and m["total"] > 0,
                                  Cell(_num(stats["total"]), FILL_AP_TOTAL_COL, align="right"))
            row[c_eff] = red_if(stats["effective"] == m["effective"] and m["effective"] > 0,
                                Cell(_num(stats["effective"]), FILL_AP_YELLOW))
            row[c_avg] = red_if(stats["avg"] == m["avg"] and m["avg"] > 0,
                                Cell(_num(round(stats["avg"])), FILL_AP_YELLOW, align="right"))
            row[c_peak] = red_if(stats["peak"] == m["peak"] and m["peak"] > 0,
                                 Cell(_num(stats["peak"]), FILL_AP_YELLOW, align="right"))
            row[c_a] = red_if(stats["A"] == best_a_total and best_a_total > 0,
                              Cell(_num(stats["A"]), FILL_AP_A, align="right"))
            row[c_a_no] = Cell(str(no), FILL_AP_A)
            row[c_a_avg] = Cell(_num(round(stats["A_avg"])), FILL_AP_A, align="right")
            row[c_b] = red_if(stats["B"] == best_b_total and best_b_total > 0,
                              Cell(_num(stats["B"]), FILL_AP_B, align="right"))
            row[c_b_no] = Cell(str(no), FILL_AP_B)
            row[c_b_avg] = Cell(_num(round(stats["B_avg"])), FILL_AP_B, align="right")
            grid.add_row(20, row)
        grid.merges.append((first, 0, len(grid.rows) - 1, 0))

        row = blank()
        row[0] = Cell(f"Avg/Hr{line['label']}", WHITE, True, size=11)
        for i, h in enumerate(hours):
            row[2 + i] = Cell(_num(round(line["avg_hr"][h])), WHITE, align="right")
        for col, key in ((c_total, "total"), (c_eff, "effective"), (c_avg, "avg"), (c_peak, "peak")):
            row[col] = Cell(_num(round(line["max"][key])), (0xF8, 0xF9, 0xFA), color=TEXT_AP_RED, align="right")
        row[c_a] = Cell(_num(line["A"]), FILL_AP_HEADER, align="right")
        row[c_a_avg] = Cell(_num(round(line["A_avg"])), FILL_AP_HEADER, align="right")
        row[c_b] = Cell(_num(line["B"]), FILL_AP_HEADER, align="right")
        row[c_b_avg] = Cell(_num(round(line["B_avg"])), FILL_AP_HEADER, align="right")
        r = grid.add_row(22, row)
        grid.merges.append((r, 0, r, 1))

        row = blank(FILL_AP_SUM)
        row[0] = Cell("合计", FILL_AP_SUM, index == len(summary["lines"]) - 1)
        for i, h in enumerate(hours):
            row[2 + i] = Cell(_num(line["sums"][h]), FILL_AP_SUM, align="right")
        row[c_gap] = Cell("", WHITE)
        row[c_total] = Cell(_num(line["sum_total"]), FILL_AP_SUM, align="right")
        row[c_eff] = Cell(_num(line["sum_effective"]), FILL_AP_SUM)
        row[c_avg] = Cell(_num(round(line["sum_avg"])), FILL_AP_SUM, align="right")
        row[c_peak] = Cell(_num(line["sum_peak"]), FILL_AP_SUM, align="right")
        if index == len(summary["lines"]) - 1:
            row[c_a] = Cell("เครื่องที่ปล่อยพัสดุได้เยอะสุด", FILL_AP_SUM, True, size=14)
        r = grid.add_row(28, row)
        grid.merges += [(r, 0, r, 1), (r, c_a, r, c_b_avg)]

    row = blank()
    row[0] = Cell("Avg.", WHITE, True)
    for i, h in enumerate(hours):
        row[2 + i] = Cell(_num(round(summary["avg"][h])), WHITE, align="right")
    for col, key in ((c_total, "total"), (c_eff, "effective"), (c_avg, "avg"), (c_peak, "peak")):
        row[col] = Cell(_num(round(summary["avg_max"][key])), WHITE, color=TEXT_AP_RED, align="right")
    for col, text, fill in ((c_a, "A", FILL_AP_YELLOW), (c_a_no, "เครื่อง", FILL_AP_YELLOW),
                            (c_a_avg, "Avg/Hr.", FILL_AP_YELLOW), (c_b, "B", FILL_AP_B_HEADER),
                            (c_b_no, "เครื่อง", FILL_AP_B_HEADER), (c_b_avg, "Avg/Hr", FILL_AP_B_HEADER)):
        row[col] = Cell(text, fill)
    r = grid.add_row(22, row)
    grid.merges.append((r, 0, r, 1))

    grand = summary["grand"]
    row = blank(FILL_AP_HEADER)
    row[0] = Cell("ผลรวม", FILL_AP_HEADER, True)
    for i, h in enumerate(hours):
        row[2 + i] = Cell(_num(grand["hours"][h]), FILL_AP_HEADER, align="right")
    row[c_gap] = Cell("", WHITE)
    row[c_total] = Cell(_num(grand["total"]), FILL_AP_HEADER, align="right")
    row[c_eff] = Cell(_num(grand["effective"]), FILL_AP_HEADER)
    row[c_avg] = Cell(_num(round(grand["avg"])), FILL_AP_HEADER, align="right")
    row[c_peak] = Cell(_num(grand["peak"]), FILL_AP_HEADER, align="right")
    (a_total, a_no, a_avg), (b_total, b_no, b_avg) = summary["best"]["A"], summary["best"]["B"]
    row[c_a] = Cell(_num(a_total), FILL_AP_A, align="right")
    row[c_a_no] = Cell(str(a_no) if a_total else "-", FILL_AP_A)
    row[c_a_avg] = Cell(_num(round(a_avg)), FILL_AP_A, align="right")
    row[c_b] = Cell(_num(b_total), FILL_AP_B, align="right")
    row[c_b_no] = Cell(str(b_no) if b_total else "-", FILL_AP_B)
    row[c_b_avg] = Cell(_num(round(b_avg)), FILL_AP_B, align="right")
    r = grid.add_row(32, row)
    grid.merges.append((r, 0, r, 1))
    return render_grid(grid, out_path, font_size=13, capture=capture)


# Sort Source ในไฟล์รายชั่วโมงเป็นภาษาจีน ส่วน Abnormal Detail เป็นภาษาอังกฤษ
SORT_SOURCE_ALIASES = {"无分拣计划": "No Plan", "最大循环件": "Max Cycles", "拦截件": "Intercept"}

# ชื่อชนิดความผิดปกติตามรายงาน manual — ชนิดที่ไม่รู้จักจะแสดงตามชื่อในไฟล์
ABNORMAL_LABELS = {
    "No Plan": "ไม่มีข้อมูลการคัดแยก\n无分拣计划",
    "Max Cycles": "เกินเวลาการคัดแยก\n最大循环件",
    "Intercept": "พัสดุถูกสกัด\n拦截件",
}


@dataclass
class AbnormalData:
    header: List[Any]
    rows: List[list]          # แถวในรอบหลังตัด Billcode ซ้ำ (เหมือนชีต DATA Abnormal)
    counts: Dict[str, int]    # จำนวนต่อชนิด (Sort Source)
    last: Optional[datetime] = None  # Sort Time ล่าสุดในรอบ — ใช้ดูว่าไฟล์ครอบคลุมถึงเวลาส่งไหม


def read_abnormal(path: str, window: Window) -> AbnormalData:
    """อ่าน Abnormal Detail แล้วนับ Billcode ไม่ซ้ำต่อ Sort Source

    ไฟล์นี้ export รวดเดียวทั้งรอบงาน (เช่น 15:01 ถึง 10:49 ของวันถัดไป)
    รายงาน manual นับทั้งไฟล์ ไม่ได้ตัดที่เวลาส่งรายงาน จึงกรองแค่ 24 ชม. นับจากต้นรอบ
    """
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"ไม่พบไฟล์ {path}")
    return abnormal_from_rows(read_sheet(path), window, path)


def abnormal_from_rows(rows: List[list], window: Window, source_name: str = "") -> AbnormalData:
    """ตัด Billcode ซ้ำ (เก็บแถวแรก) แล้วนับต่อ Sort Source — เหมือนชีต DATA Abnormal ในไฟล์ manual"""
    if not rows:
        return AbnormalData([], [], {})
    path = source_name
    header = list(rows[0])
    i_bill = _find_column(header, ("Billcode",), path)
    i_time = _find_column(header, ("Sort Time",), path)
    i_source = _find_column(header, ("Sort Source",), path)
    day_end = window.start + timedelta(days=1)

    kept: List[list] = []
    seen: set = set()
    counts: Dict[str, int] = {}
    last: Optional[datetime] = None
    for row in rows[1:]:
        if len(row) <= max(i_bill, i_time, i_source):
            continue
        sorted_at = parse_dt(row[i_time])
        if sorted_at is None or not window.start <= sorted_at < day_end:
            continue
        bill = str(row[i_bill] or "").strip()
        if not bill or bill in seen:
            continue
        seen.add(bill)
        last = sorted_at if last is None else max(last, sorted_at)
        kept.append(list(row))
        source = str(row[i_source] or "").strip() or "-"
        source = SORT_SOURCE_ALIASES.get(source, source)
        counts[source] = counts.get(source, 0) + 1
    return AbnormalData(header, kept, counts, last)


def find_abnormal(path: str, window: Window) -> Tuple[str, AbnormalData]:
    """path เป็นไฟล์ก็อ่านไฟล์นั้น เป็นโฟลเดอร์ก็หาไฟล์ Abnormal Detail ที่มีข้อมูลของรอบนี้

    ในโฟลเดอร์ Auto มีไฟล์ Abnormal Detail หลายชื่อ (all error.xls, Abnormal Detail.xls,
    2_Abnormal Detail.xls ...) ที่ export คนละวัน จึงเลือกไฟล์ที่มีพัสดุในรอบนี้มากที่สุด
    """
    if os.path.isfile(path):
        return path, read_abnormal(path, window)
    if not path or not os.path.isdir(path):
        raise FileNotFoundError(f"ไม่พบไฟล์/โฟลเดอร์ Abnormal Detail {path}")
    best: Optional[Tuple[int, float, str, AbnormalData]] = None
    for name in os.listdir(path):
        if not name.lower().endswith((".xls", ".xlsx")) or name.startswith("~$"):
            continue
        candidate = os.path.join(path, name)
        try:
            data = read_abnormal(candidate, window)
        except Exception:
            continue  # ไฟล์อื่นในโฟลเดอร์ (Billcode Detail ฯลฯ) ไม่มีคอลัมน์ Sort Source
        score = (sum(data.counts.values()), os.path.getmtime(candidate))
        if best is None or score > best[:2]:
            best = (score[0], score[1], candidate, data)
    if best is None or best[0] == 0:
        return path, AbnormalData([], [], {})
    return best[2], best[3]


def abnormal_table_values(counts: Dict[str, int]) -> Dict[str, Any]:
    """ค่าตามสูตรในไฟล์ "รายการความผิดปกติ Autopacking KKN"

    P11 Grand Total = COUNTA(A:A)-1 (ทุกแถวหลังตัดซ้ำ รวมชนิดอื่นอย่าง Intercept)
    Q9  = (P11-P10)/P11  และ  Q10 = (P11-P9)/P11  (ชนิดที่ไม่ได้แสดงจึงไปรวมใน % ทั้งสองแถว)
    Q11 = SUM(Q9:Q10) แสดงแบบ 0%
    """
    total = sum(counts.values())
    no_plan, max_cycles = counts.get("No Plan", 0), counts.get("Max Cycles", 0)
    pct_no_plan = (total - max_cycles) / total if total else 0
    pct_max = (total - no_plan) / total if total else 0
    return {"No Plan": (no_plan, pct_no_plan), "Max Cycles": (max_cycles, pct_max),
            "total": total, "total_pct": pct_no_plan + pct_max}


def render_abnormal_table(counts: Dict[str, int], business_date: date, out_path: str,
                          capture: Optional[list] = None) -> str:
    """ตารางแบบในไฟล์ "รายการความผิดปกติ Autopacking KKN" (O5:Q11)"""
    stamp = business_date.isoformat()
    values = abnormal_table_values(counts)
    grid = Grid([170, 130, 104], family="yahei")
    grid.add_row(58, [Cell(f"รายการความผิดปกติ Autopacking KKN {stamp}\n孔敬交叉带汇报异常情况 {stamp}",
                           WHITE, True, family="tahoma"), None, None])
    grid.merges.append((0, 0, 0, 2))
    grid.add_row(21, [Cell(t, FILL_RED_HEADER, True, color=WHITE) for t in ("异常原因", "分拣量", "百分比")])
    for source, height in (("No Plan", 46), ("Max Cycles", 44)):
        count, pct = values[source]
        grid.add_row(height, [Cell(ABNORMAL_LABELS[source], WHITE),
                              Cell(_num(count), WHITE),
                              Cell(f"{pct * 100:.2f}%", WHITE, color=TEXT_RED, size=19)])
    grid.add_row(27, [Cell("Grand Total", FILL_YELLOW, True),
                      Cell(_num(values["total"]), FILL_YELLOW, True),
                      Cell(f"{values['total_pct'] * 100:.0f}%" if values["total"] else "-",
                           FILL_YELLOW, color=TEXT_RED, size=19)])
    return render_grid(grid, out_path, font_size=15, capture=capture)


# =====================================================================
# ไฟล์ข้อมูลดิบรวม
# =====================================================================

@dataclass
class RawSheet:
    name: str
    header: List[Any]
    rows: List[list]
    grids: List["Grid"] = field(default_factory=list)  # มีค่า = ชีตตารางสรุป (สี/ตัวเลขเหมือนรูปที่ส่ง)


# ลำดับชีตในไฟล์ดิบ: ตารางสรุปก่อน แล้วตามด้วย data ทุกไฟล์ (ไม่มีข้อมูล = ชีตว่าง)
# (ชื่อชีต, หัวข้อที่เอาตารางมาวางเรียงลงมาในชีตนั้น)
TABLE_SHEETS = [("AUTO", ["autopacking"]), ("DWS", ["dws"]), ("DWS+DWSPDA", ["dws", "dws_pda"]),
                ("AUTOPDA", ["auto_pda"]), ("กระสอบ", ["pda_bag"]), ("ERROR", ["abnormal"])]
TABLE_GAP_ROWS = 2  # แถวว่างคั่นระหว่างตารางที่อยู่ชีตเดียวกัน
TABLE_SHEET_NAMES = {name for name, _ in TABLE_SHEETS}
DATA_SHEETS = (["AUTOPACKING Throughput", "AUTOPACKING Abnormal", "AUTO PDA DATA", "DWS PDA DATA",
                "PDA BAG DATA"] + [f"DWS{i}" for i in range(1, 9)] + ["DWS9-11"])
FONT_NAMES = {"yahei": "Microsoft YaHei", "jhenghei": "Microsoft JhengHei", "tahoma": "Tahoma"}
TABLE_TAB_COLOR = "#ED7D31"  # สีแท็บของชีตตาราง ให้แยกจากชีต data ได้ทันที


def window_file_sheet(name: str, path: str, time_column: str, window: "Window",
                      log: LogFunc = _noop) -> RawSheet:
    """ชีต data ของไฟล์ทั้งไฟล์ แต่เก็บเฉพาะแถวที่อยู่ในรอบ — ไฟล์ไม่มี/ข้อมูลเก่า = ชีตว่าง"""
    if not path or not os.path.isfile(path):
        return RawSheet(name, [], [])
    try:
        rows = read_sheet(path)
    except Exception as exc:
        _say(log, f"Daily summary: อ่าน {name} ไม่ได้ — {exc}", "WARN")
        return RawSheet(name, [], [])
    if not rows:
        return RawSheet(name, [], [])
    header = list(rows[0])
    try:
        i_time = _find_column(header, (time_column,), path)
    except ValueError:
        return RawSheet(name, header, [])
    kept = []
    for row in rows[1:]:
        moment = parse_dt(row[i_time]) if i_time < len(row) else None
        if moment is not None and window.slot_of(moment) is not None:
            kept.append(list(row))
    return RawSheet(name, header, kept)


def _excel_cell_value(text: str) -> Tuple[Any, Optional[str]]:
    """ข้อความในตาราง -> ค่าที่ Excel คำนวณต่อได้ ("1,234" -> 1234, "17.40%" -> 0.174)"""
    value = text.strip()
    if re.fullmatch(r"-?\d[\d,]*", value):
        return int(value.replace(",", "")), ("#,##0" if "," in value else "0")
    match = re.fullmatch(r"(-?\d+(?:\.(\d+))?)%", value)
    if match:
        decimals = len(match.group(2) or "")
        return float(match.group(1)) / 100, ("0." + "0" * decimals + "%") if decimals else "0%"
    return text, None


def write_grid_sheets(workbook, ws, grids: Sequence["Grid"]) -> None:
    """วางหลายตารางเรียงลงมาในชีตเดียว (เว้น TABLE_GAP_ROWS แถว) ความกว้างคอลัมน์ใช้ค่ามากสุด"""
    widths: Dict[int, int] = {}
    for grid in grids:
        for c, width in enumerate(grid.col_widths):
            widths[c] = max(widths.get(c, 0), width)
    for c, width in widths.items():
        ws.set_column(c, c, max(4, width / 7))
    formats: Dict[Tuple, Any] = {}
    row = 0
    for grid in grids:
        write_grid_sheet(workbook, ws, grid, row, formats)
        row += len(grid.rows) + TABLE_GAP_ROWS


def write_grid_sheet(workbook, ws, grid: "Grid", row_offset: int = 0,
                     formats: Optional[Dict[Tuple, Any]] = None) -> None:
    """เขียนตารางสรุปลงชีตตามลำดับแถว (ต้องเรียงแถวเพราะใช้ constant_memory)

    เซลล์ที่ผสานข้ามแถวทำแบบผสานจริงไม่ได้ในโหมดนี้ จึงลงสีเดียวกันทั้งช่วง
    ใส่ข้อความแถวกลาง และไม่ตีเส้นคั่นภายในช่วง ให้หน้าตาเหมือนผสาน
    """
    formats = {} if formats is None else formats

    def fmt(cell: "Cell", num: Optional[str], top: int = 1, bottom: int = 1):
        key = (cell.fill, cell.bold, cell.color, cell.align, num, "\n" in cell.text,
               cell.size, cell.family or grid.family, top, bottom)
        if key not in formats:
            spec = {
                "bg_color": "#%02X%02X%02X" % cell.fill, "bold": cell.bold,
                "font_color": "#%02X%02X%02X" % cell.color, "align": cell.align, "valign": "vcenter",
                "font_name": FONT_NAMES.get(cell.family or grid.family, "Microsoft YaHei"),
                "font_size": round(cell.size * 0.75) if cell.size else 11,
                "left": 1, "right": 1, "top": top, "bottom": bottom, "text_wrap": "\n" in cell.text,
            }
            if num:
                spec["num_format"] = num
            formats[key] = workbook.add_format(spec)
        return formats[key]

    covered: Dict[Tuple[int, int], Tuple[int, int, int, int]] = {}
    for r1, c1, r2, c2 in grid.merges:
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                covered[(r, c)] = (r1, c1, r2, c2)
    for r, (height, cells) in enumerate(grid.rows):
        ws.set_row(row_offset + r, height * 0.75 * 1.2)
        for c in range(len(grid.col_widths)):
            area = covered.get((r, c))
            if area and area[1] != c:
                continue  # เขียนพร้อมช่องซ้ายสุดของช่วงแล้ว
            r1, c1, r2, c2 = area if area else (r, c, r, c)
            cell = grid.rows[r1][1][c1] if c1 < len(grid.rows[r1][1]) else None
            cell = cell or Cell()
            show = r == r1 + (r2 - r1) // 2  # ข้อความอยู่แถวกลางของช่วง
            value, num = _excel_cell_value(cell.text) if show and cell.text else ("", None)
            style = fmt(cell, num, top=1 if r == r1 else 0, bottom=1 if r == r2 else 0)
            out_row = row_offset + r
            if c2 > c1:
                ws.merge_range(out_row, c1, out_row, c2, value, style)
            elif isinstance(value, (int, float)):
                ws.write_number(out_row, c, value, style)
            elif value != "":
                ws.write_string(out_row, c, str(value), style)
            else:
                ws.write_blank(out_row, c, None, style)


def autopacking_folder(raw_dir: str, business_date: date) -> Optional[str]:
    """โฟลเดอร์ไฟล์รายชั่วโมงของวันนั้น

    โปรแกรม auto export ย้ายไฟล์ของรอบที่จบแล้วไปโฟลเดอร์ชื่อวันที่ (เช่น 2026-10-18)
    ตอน 13:00 ของวันถัดไป รอบปัจจุบันจึงอยู่ที่โฟลเดอร์หลัก
    """
    dated = os.path.join(raw_dir, business_date.isoformat())
    if os.path.isdir(dated):
        return dated
    # โฟลเดอร์หลักเป็นของรอบล่าสุดเท่านั้น — วันที่เก่ากว่าโฟลเดอร์ที่เก็บไว้แล้วล่าสุด
    # (เช่นเกิน 7 วันที่ถูกลบไป) ห้ามใช้ ไม่งั้นจะได้ยอดของวันอื่นมาแทน
    archived = []
    try:
        for name in os.listdir(raw_dir):
            try:
                archived.append(datetime.strptime(name, "%Y-%m-%d").date())
            except ValueError:
                continue
    except OSError:
        return None
    if archived and business_date <= max(archived):
        return None
    return raw_dir


def autopacking_sheets(raw_dir: str, window: Window, file_hour_offset: int = -1,
                       log: LogFunc = _noop) -> List[RawSheet]:
    """รวมชีต Throughput / Abnormal ของไฟล์ 00-23.xlsx ที่อยู่ในรอบ"""
    if not raw_dir or not os.path.isdir(raw_dir):
        _say(log, f"Daily summary: ไม่พบโฟลเดอร์ AutoPacking {raw_dir}", "WARN")
        return []
    folder = autopacking_folder(raw_dir, window.business_date)
    if folder is None:
        _say(log, f"Daily summary: ไม่มีไฟล์ AutoPacking ของวันที่ {window.business_date} บน share แล้ว", "WARN")
        return []
    throughput = RawSheet("AUTOPACKING Throughput", [], [])
    abnormal = RawSheet("AUTOPACKING Abnormal", [], [])
    for hour in window.hours:
        if not window.slot_started(hour):
            continue
        file_hour = (hour - file_hour_offset) % 24
        path = os.path.join(folder, f"{file_hour:02d}.xlsx")
        if not os.path.isfile(path):
            continue
        try:
            from python_calamine import CalamineWorkbook
            workbook = CalamineWorkbook.from_path(path)
        except Exception as exc:
            _say(log, f"Daily summary: อ่าน {os.path.basename(path)} ไม่ได้ — {exc}", "WARN")
            continue
        for target, sheet_name in ((throughput, "Throughput"), (abnormal, "Abnormal")):
            if sheet_name not in workbook.sheet_names:
                continue
            data = workbook.get_sheet_by_name(sheet_name).to_python(skip_empty_area=False)
            if not data:
                continue
            if not target.header:
                target.header = ["ชั่วโมงข้อมูล", "ไฟล์"] + list(data[0])
            for row in data[1:]:
                if row and row[0] not in (None, ""):
                    target.rows.append([hour_label(hour), os.path.basename(path)] + list(row))
    return [sheet for sheet in (throughput, abnormal) if sheet.header]


def _cell_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return value


def write_raw_workbook(path: str, sheets: Sequence[RawSheet]) -> str:
    """เขียนไฟล์ดิบรวม — ข้อความที่ขึ้นต้นด้วย = ต้องเป็นข้อความ ไม่ใช่สูตร"""
    import xlsxwriter

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    handle, temp = tempfile.mkstemp(suffix=".xlsx", dir=os.path.dirname(os.path.abspath(path)))
    os.close(handle)
    try:
        workbook = xlsxwriter.Workbook(temp, {
            "constant_memory": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
            "strings_to_numbers": False,
        })
        bold = workbook.add_format({"bold": True, "bg_color": "#D9D9D9"})
        used = set()
        for sheet in sheets:
            title = sheet.name[:31]
            while title in used:
                title = title[:28] + f"_{len(used)}"
            used.add(title)
            ws = workbook.add_worksheet(title)
            if sheet.name in TABLE_SHEET_NAMES:
                ws.set_tab_color(TABLE_TAB_COLOR)  # ชีตตารางมีสีแท็บเสมอ แม้หัวข้อนั้นจะไม่มีตาราง
                if sheet.grids:
                    write_grid_sheets(workbook, ws, sheet.grids)
                continue
            if not sheet.header or not sheet.rows:
                continue  # ไม่มีไฟล์/ไม่มีข้อมูลของรอบนี้ (ข้อมูลไม่อัปเดต) = ชีตว่าง
            ws.write_row(0, 0, [_cell_value(v) for v in sheet.header], bold)
            ws.freeze_panes(1, 0)
            for i, row in enumerate(sheet.rows, start=1):
                ws.write_row(i, 0, [_cell_value(v) for v in row])
            ws.set_column(0, max(len(sheet.header) - 1, 0), 18)
        workbook.close()
        os.replace(temp, path)
        return path
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def fit_for_upload(path: str, max_mb: float, log: LogFunc = _noop) -> Optional[str]:
    """คืน path ที่ส่งได้: ไฟล์เดิม, .zip หรือ None ถ้ายังใหญ่เกิน"""
    limit = max_mb * 1024 * 1024
    if os.path.getsize(path) <= limit:
        return path
    zip_path = os.path.splitext(path)[0] + ".zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.write(path, os.path.basename(path))
    size_mb = os.path.getsize(zip_path) / 1024 / 1024
    if os.path.getsize(zip_path) <= limit:
        _say(log, f"Daily summary: ไฟล์ดิบใหญ่เกิน {max_mb:g}MB — บีบเป็น zip ({size_mb:.1f}MB)", "WARN")
        return zip_path
    _say(log, f"Daily summary: ไฟล์ดิบหลังบีบ zip ยังใหญ่ {size_mb:.1f}MB เกินลิมิต Feishu — ไม่ส่งไฟล์", "WARN")
    return None


# =====================================================================
# ตั้งค่า + ทำงานหนึ่งรอบ
# =====================================================================

@dataclass
class Settings:
    output_dir: str
    raw_dir: str
    name_auto: str
    name_dwspda: str
    name_dws: str
    autopacking_dir: str
    dws_files: List[Tuple[str, str]]  # (ชื่อเครื่อง, path) ของ DWS1-8
    abnormal_file: str
    app_id: str
    app_secret: str
    chat_id: str
    options: Dict[str, str]

    def opt(self, key: str) -> str:
        return str(self.options.get(key, DEFAULTS.get(key, ""))).strip()

    def opt_int(self, key: str) -> int:
        try:
            return int(float(self.opt(key)))
        except ValueError:
            return int(DEFAULTS[key])

    def opt_bool(self, key: str) -> bool:
        return self.opt(key).lower() in ("1", "true", "yes", "on")

    def custom_path(self, key: str) -> str:
        """path แยกของสรุปรายวัน — ว่าง หรือไม่ได้ติ๊กใช้ path แยก = ใช้ค่าจากหน้า ตั้งค่า"""
        if not self.opt_bool("custom_paths"):
            return ""
        return self.opt(f"path_{key}").replace('"', "")

    @property
    def auto_pda_file(self) -> str:
        return self.custom_path("auto_pda") or os.path.join(self.raw_dir, self.name_auto)

    @property
    def dws_pda_file(self) -> str:
        return self.custom_path("dws_pda") or os.path.join(self.raw_dir, self.name_dwspda)

    @property
    def dws911_file(self) -> str:
        return self.custom_path("dws911") or os.path.join(self.raw_dir, self.name_dws)

    @property
    def autopacking_folder(self) -> str:
        return self.custom_path("autopacking_dir") or self.autopacking_dir


def _clean(value: Any) -> str:
    return str(value or "").strip().replace('"', "")


def ensure_defaults(config: configparser.RawConfigParser) -> bool:
    """เติมค่า [DAILY_SUMMARY] ที่ยังไม่มี คืน True ถ้ามีการเปลี่ยน"""
    changed = False
    if SECTION not in config:
        config[SECTION] = {}
        changed = True
    for key, value in DEFAULTS.items():
        if config[SECTION].get(key) is None:
            config[SECTION][key] = value
            changed = True
    return changed


def settings_from_config(config: configparser.RawConfigParser) -> Settings:
    def get(section: str, key: str, default: str = "") -> str:
        if section not in config:
            return default
        return _clean(config[section].get(key, default)) or default

    options = dict(DEFAULTS)
    if SECTION in config:
        options.update({k: v for k, v in config[SECTION].items()})

    dws_files: List[Tuple[str, str]] = []
    try:
        from core import dws_mirror
        for job in dws_mirror.plan():
            if job.get("folder") and job.get("file"):
                dws_files.append((job["station"], os.path.join(job["folder"], job["file"])))
    except Exception:
        pass

    raw_dir = get("DWS_JMS", "raw_path") or get("PATH", "output_dir")
    autopacking_dir = get("PATH", "autopacking_dir")
    if not autopacking_dir:
        try:
            from metrics import core as metrics_core
            autopacking_dir = metrics_core.load_config()["autopacking"]["raw_dir"]
        except Exception:
            autopacking_dir = ""

    abnormal_file = _clean(options.get("abnormal_file"))

    return Settings(
        output_dir=get("PATH", "output_dir"),
        raw_dir=raw_dir,
        name_auto=get("DWS_JMS", "name_auto", "DWSXAUTOPDA.xlsx"),
        name_dwspda=get("DWS_JMS", "name_dwspda", "DWSPDA.xlsx"),
        name_dws=get("DWS_JMS", "name_dws", "DWS9-11.xlsx"),
        autopacking_dir=autopacking_dir,
        dws_files=dws_files,
        abnormal_file=abnormal_file,
        app_id=get("FEISHU", "APP_ID"),
        app_secret=get("FEISHU", "APP_SECRET"),
        chat_id=_clean(options.get("chat_id")) or get("FEISHU", "CHAT_ID"),
        options=options,
    )


@dataclass
class Section:
    key: str
    title: str
    image: Optional[str] = None
    note: str = ""
    grid: Optional["Grid"] = None  # ตารางที่วาดรูปนี้ — เขียนลงไฟล์ดิบเป็นชีตตารางด้วย


def _fresh_image(path: str, max_age_minutes: int, run_started: Optional[datetime]) -> Tuple[bool, str]:
    if not os.path.isfile(path):
        return False, "ไม่พบรูป (ต้องติ๊กขั้นตอน Excel Image)"
    modified = datetime.fromtimestamp(os.path.getmtime(path))
    if run_started is not None and modified >= run_started - timedelta(seconds=5):
        return True, ""
    age = (datetime.now() - modified).total_seconds() / 60
    if age <= max_age_minutes:
        return True, ""
    return False, f"รูปเก่า {age:.0f} นาที (เกิน {max_age_minutes} นาที)"


def build(settings: Settings, window: Window, work_dir: str,
          log: LogFunc = _noop, is_running: RunFunc = _always,
          run_started: Optional[datetime] = None) -> Dict[str, Any]:
    """คำนวณ + วาดรูป + เขียนไฟล์ดิบ คืน {sections, raw_file, warnings}"""
    warnings: List[str] = []
    sections: List[Section] = []
    raw_sheets: List[RawSheet] = []
    os.makedirs(work_dir, exist_ok=True)
    stamp = window.business_date.isoformat()

    wanted = [s.strip() for s in settings.opt("sections").split(",") if s.strip()]
    auto_rows: Optional[List[ScanRow]] = None
    dws_pda_rows: Optional[List[ScanRow]] = None
    abnormal: Optional[AbnormalData] = None
    abnormal_from_detail = False
    ap_sheets: Optional[List[RawSheet]] = None
    autopack_throughput: Optional[Dict[int, List[list]]] = None

    def load_ap_sheets() -> List[RawSheet]:
        nonlocal ap_sheets
        if ap_sheets is None:
            ap_sheets = autopacking_sheets(settings.autopacking_folder, window, log=log)
        return ap_sheets

    def load_autopack_throughput() -> Dict[int, List[list]]:
        nonlocal autopack_throughput
        if autopack_throughput is None:
            autopack_throughput = read_autopack_throughput(settings.autopacking_folder, window)
        return autopack_throughput

    # Use the complete populated AutoPacking columns for every title in the
    # post.  This keeps a partial first/last column in the picture while the
    # stated time range accurately describes the full working hours.
    labels = window.labels()
    if "autopacking" in wanted:
        throughput = load_autopack_throughput()
        if throughput:
            table = autopack_feeder_counts(throughput, window)
            labels = window.labels_for_hourly_totals(autopack_summary(table)["grand"]["hours"],
                                                     trailing_total=settings.opt_int("title_end_trim_total"))

    def title(key: str) -> str:
        return settings.opt(f"title_{key}").format(**labels)

    def load_auto() -> List[ScanRow]:
        nonlocal auto_rows
        if auto_rows is None:
            path = settings.auto_pda_file
            _say(log, f"Daily summary: อ่าน {os.path.basename(path)}", "START")
            auto_rows = read_scan_file(path, window)
            _say(log, f"Daily summary: {os.path.basename(path)} ในรอบ {len(auto_rows):,} แถว")
            if not auto_rows:
                warnings.append(f"{os.path.basename(path)}: ไม่มีข้อมูลในรอบนี้ (ไฟล์อาจเป็นของรอบเก่า)")
                _say(log, f"Daily summary: {os.path.basename(path)} ไม่มีข้อมูลในรอบนี้ (ไฟล์อาจเป็นของรอบเก่า)", "WARN")
        return auto_rows

    def load_dws_pda() -> List[ScanRow]:
        nonlocal dws_pda_rows
        if dws_pda_rows is None:
            path = settings.dws_pda_file
            _say(log, f"Daily summary: อ่าน {os.path.basename(path)}", "START")
            # query DWS PDA DATA ในไฟล์ manual เก็บเฉพาะแถวที่ไม่มีเลขกระสอบ (สแกนพัสดุรายชิ้น)
            dws_pda_rows = [r for r in read_scan_file(path, window) if not r.bag]
            _say(log, f"Daily summary: {os.path.basename(path)} ในรอบ {len(dws_pda_rows):,} แถว")
            if not dws_pda_rows:
                warnings.append(f"{os.path.basename(path)}: ไม่มีข้อมูลในรอบนี้ (ไฟล์อาจเป็นของรอบเก่า)")
                _say(log, f"Daily summary: {os.path.basename(path)} ไม่มีข้อมูลในรอบนี้ (ไฟล์อาจเป็นของรอบเก่า)", "WARN")
        return dws_pda_rows

    max_age = settings.opt_int("image_max_age_minutes")
    for key in wanted:
        if not is_running():
            break
        section = Section(key, title(key) if f"title_{key}" in DEFAULTS else key)
        captured: list = []
        try:
            if settings.opt(f"{key}_image"):
                path = os.path.join(settings.output_dir, settings.opt(f"{key}_image"))
                ok, reason = _fresh_image(path, max_age, run_started)
                if ok:
                    section.image = path
                else:
                    section.note = reason
            elif key == "autopacking":
                throughput = load_autopack_throughput()
                if not throughput:
                    section.note = f"ไม่พบไฟล์ AutoPacking รายชั่วโมงของรอบนี้ใน {settings.autopacking_folder}"
                else:
                    section.image = render_autopack_table(
                        autopack_feeder_counts(throughput, window),
                        os.path.join(work_dir, f"AUTOPACKING_{stamp}.png"), capture=captured)
            elif key == "dws":
                table, dws_warnings = build_dws_table(settings, window, load_dws_pda(), log)
                warnings.extend(f"dws: {w}" for w in dws_warnings)
                section.image = render_dws_table(table, os.path.join(work_dir, f"DWS_{stamp}.png"),
                                                 capture=captured)
            elif key == "auto_pda":
                names = machine_names(settings.opt("auto_pda_prefix"), settings.opt_int("auto_pda_count"))
                section.image = render_hourly_table(
                    hourly_counts(load_auto(), names, window), window,
                    os.path.join(work_dir, f"AUTO_PDA_{stamp}.png"), bold_values=True, capture=captured)
            elif key == "pda_bag":
                names = machine_names(settings.opt("auto_pda_prefix"), settings.opt_int("auto_pda_count"))
                section.image = render_bag_table(
                    bag_counts(load_auto(), names),
                    os.path.join(work_dir, f"PDA_BAG_{stamp}.png"), capture=captured)
            elif key == "abnormal":
                if settings.abnormal_file:
                    abnormal_from_detail = True
                    source, abnormal = find_abnormal(settings.abnormal_file, window)
                else:
                    # รวมชีต Abnormal รายชั่วโมง (คอลัมน์ 2 ช่องแรกเป็นชั่วโมง/ชื่อไฟล์ที่เติมเอง)
                    source = "AutoPacking Abnormal รายชั่วโมง"
                    hourly = next((sh for sh in load_ap_sheets() if sh.name == "AUTOPACKING Abnormal"), None)
                    abnormal = abnormal_from_rows(
                        [hourly.header] + hourly.rows if hourly else [], window, source)
                if not abnormal.counts:
                    section.note = (f"ไม่พบข้อมูล Abnormal ของรอบนี้ใน {source} "
                                    "(ไฟล์อาจยังไม่ถูก export ใหม่)")
                else:
                    _say(log, f"Daily summary: Abnormal จาก {os.path.basename(source)} "
                              f"(ข้อมูลถึง {abnormal.last:%Y-%m-%d %H:%M})")
                    # ไฟล์ที่ข้อมูลจบก่อนเวลาส่งนานเกินไป = export ไม่ครบรอบ ยอดจะต่ำกว่าจริง
                    if abnormal.last and abnormal.last < window.end - timedelta(hours=2):
                        warnings.append(f"abnormal: {os.path.basename(source)} มีข้อมูลถึง "
                                        f"{abnormal.last:%H:%M} เท่านั้น ยอดอาจไม่ครบรอบ")
                        _say(log, f"Daily summary: {warnings[-1]}", "WARN")
                    section.image = render_abnormal_table(
                        abnormal.counts, window.business_date,
                        os.path.join(work_dir, f"ABNORMAL_{stamp}.png"), capture=captured)
            elif key == "dws_pda":
                names = machine_names(settings.opt("dws_pda_prefix"), settings.opt_int("dws_pda_count"))
                section.image = render_hourly_table(
                    hourly_counts(load_dws_pda(), names, window), window,
                    os.path.join(work_dir, f"DWS_PDA_{stamp}.png"), capture=captured)
            else:
                section.note = f"ไม่รู้จักหัวข้อ '{key}'"
        except Exception as exc:
            section.note = f"สร้างตารางไม่ได้: {exc}"
        if section.note:
            warnings.append(f"{key}: {section.note}")
            _say(log, f"Daily summary: {key} — {section.note}", "WARN")
        section.grid = captured[-1] if captured else None
        sections.append(section)

    raw_file: Optional[str] = None
    want_raw = settings.opt_bool("send_raw_file") or bool(settings.opt("backup_dir"))
    if want_raw and is_running():
        _say(log, "Daily summary: รวมไฟล์ข้อมูลดิบ", "START")
        by_key = {sec.key: sec for sec in sections}
        for name, keys in TABLE_SHEETS:
            grids = [by_key[key].grid for key in keys if key in by_key and by_key[key].grid is not None]
            raw_sheets.append(RawSheet(name, [], [], grids=grids))

        data: Dict[str, RawSheet] = {}
        try:
            for sheet in load_ap_sheets():
                data[sheet.name] = sheet
        except Exception as exc:
            _say(log, f"Daily summary: อ่านไฟล์ AutoPacking ไม่ได้ — {exc}", "WARN")
        # ชีต PDA เก็บเฉพาะเครื่องที่แสดงในตาราง (ตัดพนักงาน/WCS ที่ไม่เกี่ยวออก ไฟล์เล็กลงมาก)
        auto_names = {n.lower() for n in machine_names(settings.opt("auto_pda_prefix"),
                                                       settings.opt_int("auto_pda_count"))}
        dws_names = {n.lower() for n in machine_names(settings.opt("dws_pda_prefix"),
                                                      settings.opt_int("dws_pda_count"))}
        try:
            rows = load_auto()
            data["AUTO PDA DATA"] = RawSheet("AUTO PDA DATA", RAW_SCAN_HEADER,
                                             [r.as_list() for r in rows if r.scanner.lower() in auto_names])
            # ตัดกระสอบซ้ำทั้งไฟล์ก่อน (เหมือน query) แล้วค่อยกรองเครื่อง ยอดจึงตรงกับตาราง
            data["PDA BAG DATA"] = RawSheet("PDA BAG DATA", RAW_SCAN_HEADER,
                                            [r.as_list() for r in unique_bag_rows(rows)
                                             if r.scanner.lower() in auto_names])
        except Exception as exc:
            _say(log, f"Daily summary: ไม่มีข้อมูล AUTO PDA — {exc}", "WARN")
        try:
            data["DWS PDA DATA"] = RawSheet("DWS PDA DATA", RAW_SCAN_HEADER,
                                            [r.as_list() for r in load_dws_pda() if r.scanner.lower() in dws_names])
        except Exception as exc:
            _say(log, f"Daily summary: ไม่มีข้อมูล DWS PDA — {exc}", "WARN")
        for station, path in dws_station_paths(settings).items():
            data[station] = window_file_sheet(station, path, "出秤时间", window, log)
        data["DWS9-11"] = window_file_sheet("DWS9-11", settings.dws911_file, "扫描时间", window, log)
        for name in DATA_SHEETS:
            sheet = data.get(name) or RawSheet(name, [], [])
            raw_sheets.append(sheet)
            if name == "AUTOPACKING Abnormal" and abnormal_from_detail and abnormal is not None:
                raw_sheets.append(RawSheet("AUTOPACKING Error", abnormal.header, abnormal.rows))
        if raw_sheets and is_running():
            name = settings.opt("raw_file_name").format(date=stamp)
            raw_file = write_raw_workbook(os.path.join(work_dir, name), raw_sheets)
            _say(log, f"Daily summary: ไฟล์ดิบ {os.path.basename(raw_file)} "
                      f"({os.path.getsize(raw_file) / 1024 / 1024:.1f}MB, {len(raw_sheets)} sheet)")

    return {"sections": sections, "raw_file": raw_file, "warnings": warnings,
            "title": settings.opt("title_post").format(**labels)}


def parse_send_time(value: str) -> Tuple[int, int]:
    text = str(value or "").strip() or DEFAULTS["send_time"]
    hour, _, minute = text.partition(":")
    try:
        return int(hour) % 24, int(minute or 0) % 60
    except ValueError:
        return 12, 0


def scheduled_send_at(report_start: datetime, send_time: str) -> datetime:
    """เวลาส่งของรอบงานที่เริ่มตอน report_start — เป็นครั้งแรกหลังต้นรอบที่ถึงเวลานี้

    เช่น รอบเริ่ม 2026-10-18 14:00 ส่ง 12:00 -> 2026-10-19 12:00
    """
    hour, minute = parse_send_time(send_time)
    send_at = report_start.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if send_at <= report_start:
        send_at += timedelta(days=1)
    return send_at


LATE_REASON = "เลยเวลาส่ง"
# ไม่ติ๊กส่งย้อนหลัง ก็ยังเผื่อไว้ไม่กี่นาที: ตัวตั้งเวลาเช็กทุก 30 วินาที และอาจต้องรอรอบรายชั่วโมงให้เสร็จ
ON_TIME_GRACE_MINUTES = 5


def late_limit_minutes(catch_up: Any, minutes: Any) -> int:
    """นาทีที่ยังส่งอัตโนมัติได้หลังเวลาส่ง ตามติ๊ก "ส่งย้อนหลัง" และจำนวนนาทีที่ตั้ง"""
    if str(catch_up).strip().lower() not in ("1", "true", "yes", "on"):
        return ON_TIME_GRACE_MINUTES
    try:
        return max(ON_TIME_GRACE_MINUTES, int(float(minutes)))
    except (TypeError, ValueError):
        return int(DEFAULTS["send_late_minutes"])


def due_to_send(report_start: datetime, report_end: datetime, send_time: str,
                last_sent_date: str, max_late_minutes: Optional[int] = None) -> Tuple[bool, str]:
    """รอบนี้ต้องส่งสรุปไหม คืน (ส่งไหม, เหตุผล)

    max_late_minutes: ส่งตามได้ไม่เกินกี่นาทีหลังเวลาส่ง (None = ส่งตามได้ทั้งรอบงาน)
    """
    business_date = report_start.date().isoformat()
    send_at = scheduled_send_at(report_start, send_time)
    if str(last_sent_date or "").strip() == business_date:
        return False, f"ส่งสรุปของวันที่ {business_date} ไปแล้ว"
    if report_end < send_at:
        return False, f"ยังไม่ถึงเวลาส่ง ({send_at:%Y-%m-%d %H:%M})"
    if max_late_minutes is not None and report_end > send_at + timedelta(minutes=max_late_minutes):
        return False, (f"{LATE_REASON} {send_at:%H:%M} เกิน {max_late_minutes} นาที "
                       f"— สรุปของวันที่ {business_date} ต้องกด \"ส่งสรุปตอนนี้\" เอง")
    return True, f"ถึงเวลาส่ง ({send_at:%Y-%m-%d %H:%M})"


def post_content(sections: Sequence[Section], image_keys: Dict[str, str]) -> list:
    content: list = []
    for section in sections:
        for line in section.title.split("|"):
            content.append([{"tag": "text", "text": line.strip(), "style": ["bold"]}])
        key = image_keys.get(section.key)
        if key:
            content.append([{"tag": "img", "image_key": key}])
        else:
            content.append([{"tag": "text", "text": f"(ไม่มีข้อมูล — {section.note or 'ไม่มีรูป'})"}])
        content.append([{"tag": "text", "text": " "}])  # เว้นบรรทัดก่อนหัวข้อถัดไป
    return content


def send(settings: Settings, result: Dict[str, Any], log: LogFunc = _noop,
         is_running: RunFunc = _always) -> None:
    import feishu_client

    if not settings.chat_id:
        raise Exception("Daily summary: ไม่มี Chat ID")
    token = feishu_client.get_token(settings.app_id, settings.app_secret, log=log)
    image_keys: Dict[str, str] = {}
    for section in result["sections"]:
        if not is_running():
            return
        if section.image:
            _say(log, f"Daily summary: อัปโหลดรูป {os.path.basename(section.image)}", "START")
            image_keys[section.key] = feishu_client.upload_image(token, section.image, log=log)
    if not is_running():
        return
    feishu_client.send_post(token, settings.chat_id, result["title"],
                            post_content(result["sections"], image_keys), log=log)
    _say(log, "Daily summary: ส่งข้อความสรุปแล้ว", "SUCCESS")

    raw_file = result.get("raw_file")
    if raw_file and settings.opt_bool("send_raw_file") and is_running():
        upload_path = fit_for_upload(raw_file, float(settings.opt("raw_file_max_mb") or 30), log)
        if upload_path:
            file_type = "xls" if upload_path.lower().endswith(".xlsx") else "stream"
            _say(log, f"Daily summary: อัปโหลดไฟล์ {os.path.basename(upload_path)}", "START")
            file_key = feishu_client.upload_file(token, upload_path, log=log, file_type=file_type)
            feishu_client.send_file(token, settings.chat_id, file_key, log=log)
            _say(log, "Daily summary: ส่งไฟล์ข้อมูลดิบแล้ว", "SUCCESS")


def save_excel_copy(result: Dict[str, Any], folder: str, log: LogFunc = _noop) -> Optional[str]:
    """บันทึกไฟล์ Excel ที่ใช้ส่งเข้ากลุ่มไว้ในโฟลเดอร์ที่เลือก (ไม่สร้างโฟลเดอร์ย่อย ไม่เก็บรูป)"""
    raw_file = result.get("raw_file")
    if not raw_file or not os.path.isfile(raw_file):
        return None
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, os.path.basename(raw_file))
    shutil.copy2(raw_file, target)
    _say(log, f"Daily summary: บันทึกไฟล์ Excel ไว้ที่ {target}", "SUCCESS")
    return target


def run(settings: Settings, start: datetime, end: datetime, log: LogFunc = _noop,
        is_running: RunFunc = _always, run_started: Optional[datetime] = None,
        send_to_feishu: bool = True, work_dir: Optional[str] = None) -> Dict[str, Any]:
    window = make_window(start, end, settings.opt_int("day_end_hour"), settings.opt_int("shift_b_start"))
    work_dir = work_dir or os.path.join(settings.output_dir or settings.raw_dir or ".", "DailySummary")
    _say(log, f"Daily summary: รอบ {window.start:%Y-%m-%d %H:%M} -> {window.end:%Y-%m-%d %H:%M}", "START")
    result = build(settings, window, work_dir, log=log, is_running=is_running, run_started=run_started)
    # บันทึกไฟล์ Excel ก่อนส่ง — ส่ง Feishu ไม่สำเร็จไฟล์ก็ยังอยู่ (ปุ่มสร้างตัวอย่างไม่บันทึก)
    if send_to_feishu and settings.opt("backup_dir") and is_running():
        try:
            result["saved_excel"] = save_excel_copy(result, settings.opt("backup_dir"), log)
        except Exception as exc:
            result["warnings"].append(f"บันทึกไฟล์ Excel: {exc}")
            _say(log, f"Daily summary: บันทึกไฟล์ Excel ไม่สำเร็จ — {exc}", "WARN")
    if send_to_feishu and is_running():
        if not any(s.image for s in result["sections"]):
            raise Exception("Daily summary: ไม่มีตารางให้ส่งเลย — " + "; ".join(result["warnings"]))
        send(settings, result, log=log, is_running=is_running)
    return result


# =====================================================================
# CLI สำหรับทดสอบ
# =====================================================================

def main(argv: Optional[List[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="สร้าง/ส่งสรุปยอดประจำวัน")
    parser.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini"))
    parser.add_argument("--start", required=True, help='เช่น "2026-10-18 15:00"')
    parser.add_argument("--end", required=True, help='เช่น "2026-10-19 10:00"')
    parser.add_argument("--raw-dir", help="ทับโฟลเดอร์ไฟล์ดิบ JMS")
    parser.add_argument("--out", help="โฟลเดอร์ผลลัพธ์")
    parser.add_argument("--no-send", action="store_true", help="สร้างรูป/ไฟล์อย่างเดียว ไม่ส่ง Feishu")
    args = parser.parse_args(argv)

    config = configparser.RawConfigParser()
    config.read(args.config, encoding="utf-8")
    settings = settings_from_config(config)
    if args.raw_dir:
        settings.raw_dir = args.raw_dir
    start, end = parse_dt(args.start), parse_dt(args.end)
    if start is None or end is None:
        parser.error("รูปแบบเวลาไม่ถูกต้อง")
    result = run(settings, start, end, send_to_feishu=not args.no_send, work_dir=args.out)
    for section in result["sections"]:
        print(f"- {section.key}: {section.image or section.note}")
    print("raw:", result["raw_file"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
