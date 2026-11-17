"""VIP AWB — ยอดพัสดุ 8 แบรนด์ VIP ที่เข้า KKN_GW รายชั่วโมง พร้อมสถานะออกแล้ว/คงค้าง

ย้ายมาจากโปรแกรมทดสอบ C:\\สร้างโปรแกรม\\VIP AWB\\VipBot (ทดสอบกับข้อมูลจริง 17-18/11/2026)

ขั้นตอนต่อรอบ (run)
1. ดึงรายการสแกนของ KKN_GW (สแกนลงรถ + สแกนส่งออก + สแกนบรรจุกระสอบ) จาก JMS
   operatingplatform/scanningContrast/queryOrder แบบแบ่งหน้า — ไม่ผ่านศูนย์ดาวน์โหลด จึงไม่ชนกับ
   การ export AUTO/PDA/Realtime ของโปรแกรมนี้ (ใช้แค่สแกนลงรถจะขาด ~27%)
2. เก็บเฉพาะเลขที่ขึ้นต้นตาม lookup_prefixes (864 / 829 — ตัด TikTok ซึ่งเป็น ~95% ทิ้ง)
3. ค้นแหล่งที่มาคำสั่งซื้อ/รหัสลูกค้าทีละเลข (order/getOrderDetail) เฉพาะเลขที่ยังไม่เคยค้น
4. จับแบรนด์ตาม brands (คำเต็ม เทียบ แหล่งที่มา | รหัสลูกค้า | ชื่อลูกค้า) นับเลขไม่ซ้ำในรอบวัน
5. ตรวจสถานะที่คลังด้วยกติกาเดียวกับ Worker Server "หาเลขไม่มีสถานะ" (hub_status.py)
6. ส่งการ์ด + รูปตาราง + Excel เข้า Feishu (เรียกจากขั้นตอน VIP AWB ของ pipeline หรือปุ่ม ส่งตอนนี้)

ข้อมูลสะสมใน vip_awb.db ข้าง config.ini (ลบรอบที่เก่ากว่า 3 วันเอง)
ทดสอบนอกโปรแกรม: python -m vip_awb --dry-run   (ดู __main__.py)
"""
from __future__ import annotations

import configparser
import json
import logging
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import requests

from . import hub_status
from .report import ReportData, render_png, write_excel

GATEWAY = "https://jmsgw.jtexpress.co.th"
FMT = "%Y-%m-%d %H:%M:%S"
SECTION = "VIP_AWB"

log = logging.getLogger("vip_awb")

# ค่าเริ่มต้นใน config.ini [VIP_AWB] — ensure_defaults เติมเฉพาะคีย์ที่ยังไม่มี
# ส่งเมื่อไหร่: ขั้นตอน "VIP AWB" ในลำดับการทำงานของหน้า BOT REPORT (ติ๊กแล้วเปิด Auto) — ไม่มีตัวตั้งเวลาของตัวเอง
DEFAULT_BRANDS = "\n".join([
    "UNIQLO = UNIQLO, UNIQLOTH, VIP111111385",
    "ADIDAS = ADIDAS, VIP111111383",
    "ม.กรุงเทพ = BANGKOK UNIVERSITY, มหาวิทยาลัยกรุงเทพ, ม.กรุงเทพ",
    "VIVO = VIVO",
    "XIAOMI = XIAOMI, VIP111111388, VIP111111389",
    "WATSONS = WATSONS, VIP111111344",
    "AIS = AIS",
    "OPPO = OPPO",
])
DEFAULTS = {
    "chat_id": "",               # ว่าง = ใช้ Chat ID ในหน้า ตั้งค่า
    "day_start_hour": "12",      # รอบวัน 12:00 - 11:59 ของวันถัดไป
    "title": "พัสดุ VIP/VVIP สแกนเข้า KKN_GW",
    "brands": DEFAULT_BRANDS,    # ชื่อที่แสดง = คำค้น, คำค้น  (บรรทัดละแบรนด์ ตารางเรียงตามนี้)
    "customer_code_prefix": "",  # ใส่ VIP111111 = นับลูกค้า VIP ทุกระดับ (ทางคลังต้องการแค่ 8 แบรนด์)
    "send_image": "true",
    "send_excel": "true",
    "scan_network_code": "999004",
    "hub_site": "KKN_GW",
    "scan_types": "卸车扫描, 发件扫描, 建包扫描",
    "lookup_prefixes": "864, 829",
    "lookup_workers": "4",
    "page_size": "1000",
    "overlap_minutes": "60",
}


class TokenError(Exception):
    """JMS token หมดอายุหรือไม่ถูกต้อง"""


@dataclass
class Config:
    chat_id: str
    scan_network_code: str
    hub_site: str
    scan_types: list[str]
    lookup_prefixes: list[str]
    lookup_workers: int
    page_size: int
    overlap_minutes: int
    day_start_hour: int
    title: str
    vip_code_prefix: str
    send_image: bool
    send_excel: bool
    keywords: dict[str, list[str]] = field(default_factory=dict)


def _split(value: str) -> list[str]:
    return [v.strip() for v in str(value).replace("\n", ",").split(",") if v.strip()]


def _bool(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _int(value: str, default: int) -> int:
    try:
        return int(str(value).strip())
    except ValueError:
        return default


def parse_brands(text: str) -> dict[str, list[str]]:
    """บรรทัดละแบรนด์: ชื่อที่แสดง = คำค้น, คำค้น (ไม่มี = ใช้ชื่อเป็นคำค้น)"""
    brands: dict[str, list[str]] = {}
    for line in str(text).splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        name, _, terms = line.partition("=")
        name = name.strip()
        if name:
            brands[name] = [t.upper() for t in _split(terms)] or [name.upper()]
    return brands


def ensure_defaults(config: configparser.ConfigParser) -> None:
    if SECTION not in config:
        config[SECTION] = {}
    for key, value in DEFAULTS.items():
        if key not in config[SECTION]:
            config[SECTION][key] = value


def load_settings(config: configparser.ConfigParser) -> Config:
    sec = config[SECTION] if SECTION in config else {}
    g = lambda k: str(sec.get(k, DEFAULTS[k]) if hasattr(sec, "get") else DEFAULTS[k]).strip()
    return Config(
        chat_id=g("chat_id"),
        scan_network_code=g("scan_network_code"),
        hub_site=g("hub_site"),
        scan_types=_split(g("scan_types")),
        lookup_prefixes=_split(g("lookup_prefixes")),
        lookup_workers=max(1, _int(g("lookup_workers"), 4)),
        page_size=max(100, _int(g("page_size"), 1000)),
        overlap_minutes=max(0, _int(g("overlap_minutes"), 60)),
        day_start_hour=_int(g("day_start_hour"), 12) % 24,
        title=g("title") or DEFAULTS["title"],
        vip_code_prefix=g("customer_code_prefix"),
        send_image=_bool(g("send_image")),
        send_excel=_bool(g("send_excel")),
        keywords=parse_brands(sec.get("brands", DEFAULT_BRANDS) if hasattr(sec, "get") else DEFAULT_BRANDS),
    )


# ---------------------------------------------------------------------------
# รอบวัน (business day)
# ---------------------------------------------------------------------------
def report_window(now: datetime, start_hour: int) -> tuple[datetime, datetime, datetime]:
    """คืน (period_start, period_end, data_until)

    data_until = ต้นชั่วโมงปัจจุบัน เช่นรัน 23:05 -> ข้อมูลถึง 23:00
    รอบวันคือรอบที่ครอบ data_until - 1 วินาที ดังนั้นรันตอน 12:05 จะได้รอบของเมื่อวาน
    ครบ 24 ชม. (ยอดปิดรอบ) ส่วน 13:05 คือชั่วโมงแรกของรอบใหม่
    """
    data_until = now.replace(minute=0, second=0, microsecond=0)
    ref = data_until - timedelta(seconds=1)
    start = ref.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    if ref < start:
        start -= timedelta(days=1)
    return start, start + timedelta(days=1), data_until


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    period_start TEXT NOT NULL,
    awb          TEXT NOT NULL,
    first_scan   TEXT NOT NULL,
    PRIMARY KEY (period_start, awb)
);
CREATE TABLE IF NOT EXISTS orders (
    awb           TEXT PRIMARY KEY,
    source        TEXT,
    customer_code TEXT,
    customer_name TEXT,
    status        TEXT NOT NULL,      -- ok | empty | error
    checked_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fetch_state (
    period_start  TEXT PRIMARY KEY,
    fetched_until TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sent (
    period_start TEXT NOT NULL,
    data_until   TEXT NOT NULL,
    sent_at      TEXT NOT NULL,
    PRIMARY KEY (period_start, data_until)
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


def open_db(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=30, check_same_thread=False)
    con.executescript(SCHEMA)
    con.execute("PRAGMA journal_mode=WAL")
    return con


# ---------------------------------------------------------------------------
# JMS
# ---------------------------------------------------------------------------
class Jms:
    def __init__(self, token: str):
        self.token = token
        self.local = threading.local()

    def _session(self) -> requests.Session:
        s = getattr(self.local, "s", None)
        if s is None:
            s = self.local.s = requests.Session()
        return s

    def _headers(self, route: str, lang: str) -> dict:
        return {
            "authtoken": self.token,
            "lang": lang,
            "langtype": lang,
            "timezone": "GMT+0700",
            "content-type": "application/json;charset=UTF-8",
            "origin": "https://jms.jtexpress.co.th",
            "referer": "https://jms.jtexpress.co.th/",
            "routename": route,
        }

    @staticmethod
    def _check(resp: requests.Response) -> dict:
        if resp.status_code in (401, 403) and "页面未配置" not in resp.text:
            raise TokenError(f"JMS ตอบ HTTP {resp.status_code}")
        resp.raise_for_status()
        data = resp.json()
        msg = str(data.get("msg") or "")
        if not data.get("succ", data.get("code") == 1):
            low = msg.lower()
            if any(w in low for w in ("token", "login", "登录", "过期", "unauthorized")):
                raise TokenError(f"JMS token ใช้ไม่ได้: {msg}")
            raise RuntimeError(f"JMS ตอบผิดพลาด: {msg or data.get('code')}")
        return data

    def post(self, path: str, route: str, payload: dict, lang: str = "TH", params: dict | None = None,
             retries: int = 3) -> dict:
        last: Exception | None = None
        for attempt in range(retries):
            try:
                r = self._session().post(GATEWAY + path, headers=self._headers(route, lang), json=payload,
                                         params=params, timeout=(10, 120))
                return self._check(r)
            except TokenError:
                raise
            except Exception as e:  # network / 5xx -> ลองใหม่
                last = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"เรียก {path} ไม่สำเร็จ: {last}")

    def scans(self, start: datetime, end: datetime, network: str, scan_type: str, page_size: int):
        """คืน (awb, scanDate) ทุกแถวในช่วงเวลา (แบ่งหน้า)"""
        current, pages = 1, 1
        while current <= pages:
            body = {
                "current": current, "size": page_size,
                "startTimeStr": start.strftime(FMT), "endTimeStr": end.strftime(FMT),
                "scanNetworkCode": network, "scanType": scan_type,
                "sortName": "scanDate", "sortOrder": "asc", "billType": 0,
                "countryId": "1", "contrastScanType": "发件扫描",
            }
            data = self.post("/operatingplatform/scanningContrast/queryOrder", "scanComparisonNew", body,
                             lang="CN").get("data") or {}
            pages = int(data.get("pages") or 0)
            for rec in data.get("records") or []:
                awb = str(rec.get("billNo") or "").strip()
                if awb:
                    yield awb, str(rec.get("scanDate") or "")
            current += 1

    def order_source(self, awb: str) -> tuple[str, str, str, str]:
        """คืน (status, orderSourceName, customerCode, customerName)"""
        data = self.post("/operatingplatform/order/getOrderDetail", "trackingExpress",
                         {"waybillNo": awb, "countryId": "1"}, params={"waybillNo": awb})
        d = ((data.get("data") or {}).get("details")) or {}
        src = str(d.get("orderSourceName") or "").strip()
        status = "ok" if src else "empty"
        return status, src, str(d.get("customerCode") or ""), str(d.get("customerName") or "")


# ---------------------------------------------------------------------------
# ขั้นตอนหลัก
# ---------------------------------------------------------------------------
def wanted(awb: str, prefixes: list[str]) -> bool:
    return "*" in prefixes or any(awb.startswith(p) for p in prefixes)


def fetch_scans(con: sqlite3.Connection, jms: Jms, cfg: Config, p_start: datetime, until: datetime) -> int:
    key = p_start.strftime(FMT)
    row = con.execute("SELECT fetched_until FROM fetch_state WHERE period_start=?", (key,)).fetchone()
    begin = p_start
    if row:
        begin = max(p_start, datetime.strptime(row[0], FMT) - timedelta(minutes=cfg.overlap_minutes))
    if begin >= until:
        return 0

    added = 0
    # ดึงทีละชั่วโมง ให้แต่ละคำขอเล็กและเร็ว
    chunk = begin
    while chunk < until:
        chunk_end = min(chunk + timedelta(hours=1), until)
        rows: dict[str, str] = {}
        for st in cfg.scan_types:
            for awb, scan_date in jms.scans(chunk, chunk_end - timedelta(seconds=1), cfg.scan_network_code,
                                            st, cfg.page_size):
                if wanted(awb, cfg.lookup_prefixes):
                    if awb not in rows or scan_date < rows[awb]:
                        rows[awb] = scan_date
        before = con.total_changes
        con.executemany(
            "INSERT INTO scans(period_start, awb, first_scan) VALUES(?,?,?) "
            "ON CONFLICT(period_start, awb) DO UPDATE SET first_scan=MIN(first_scan, excluded.first_scan)",
            [(key, a, d) for a, d in rows.items()],
        )
        added += con.total_changes - before
        con.execute("INSERT INTO fetch_state(period_start, fetched_until) VALUES(?,?) "
                    "ON CONFLICT(period_start) DO UPDATE SET fetched_until=excluded.fetched_until",
                    (key, chunk_end.strftime(FMT)))
        con.commit()
        log.info("scan %s -> %s : %d เลขที่ต้องค้น", chunk.strftime("%m-%d %H:%M"), chunk_end.strftime("%H:%M"),
                 len(rows))
        chunk = chunk_end
    return added


def lookup_sources(con: sqlite3.Connection, jms: Jms, cfg: Config, p_start: datetime) -> tuple[int, int]:
    key = p_start.strftime(FMT)
    todo = [r[0] for r in con.execute(
        "SELECT s.awb FROM scans s LEFT JOIN orders o ON o.awb=s.awb "
        "WHERE s.period_start=? AND (o.awb IS NULL OR o.status='error')", (key,))]
    if not todo:
        return 0, 0
    log.info("ค้นแหล่งที่มาคำสั่งซื้อ %d เลข", len(todo))
    errors = 0
    lock = threading.Lock()
    token_error: list[TokenError] = []

    def work(awb: str):
        nonlocal errors
        if token_error:
            return
        try:
            res = jms.order_source(awb)
        except TokenError as e:
            token_error.append(e)
            return
        except Exception as e:
            log.warning("ค้น %s ไม่สำเร็จ: %s", awb, e)
            res = ("error", "", "", "")
            with lock:
                errors += 1
        with lock:
            con.execute("INSERT OR REPLACE INTO orders VALUES(?,?,?,?,?,?)",
                        (awb, res[1], res[2], res[3], res[0], datetime.now().strftime(FMT)))

    with ThreadPoolExecutor(cfg.lookup_workers) as ex:
        list(ex.map(work, todo))
    con.commit()
    if token_error:
        raise token_error[0]
    return len(todo), errors


def build_report(con: sqlite3.Connection, cfg: Config, p_start: datetime, p_end: datetime,
                 until: datetime) -> ReportData:
    key = p_start.strftime(FMT)
    # จับทั้งแหล่งที่มาคำสั่งซื้อ รหัสลูกค้า และชื่อลูกค้า เพราะบางแบรนด์ (เช่น Xiaomi) แหล่งที่มา
    # เป็นแค่ "VIP" แต่ชื่อแบรนด์/รหัส VIP111111xxx อยู่ในข้อมูลลูกค้า
    rows = con.execute(
        "SELECT s.awb, s.first_scan, COALESCE(o.source,''), COALESCE(o.customer_code,''), "
        "COALESCE(o.customer_name,'') FROM scans s JOIN orders o ON o.awb=s.awb "
        "WHERE s.period_start=? AND s.first_scan < ? ORDER BY s.first_scan",
        (key, until.strftime(FMT))).fetchall()
    # ต้องเป็นคำเต็ม (ไม่ติดตัวอักษร/ตัวเลขภาษาอังกฤษ) กัน AIS ไปตรงกับ MaiShop888
    patterns = {name: [re.compile(r"(?<![A-Z0-9])" + re.escape(t) + r"(?![A-Z0-9])") for t in terms]
                for name, terms in cfg.keywords.items()}
    vip_prefix = cfg.vip_code_prefix.upper()
    found: list[dict] = []
    for awb, first, source, code, name in rows:
        text = f"{source} | {code} | {name}".upper()
        brand = next((b for b, pats in patterns.items() if any(p.search(text) for p in pats)), None)
        if not brand and vip_prefix and code.upper().startswith(vip_prefix):
            # ลูกค้า VIP รายอื่น: ใช้ชื่อลูกค้า ถ้าชื่อเป็นแค่รหัสใช้แหล่งที่มาคำสั่งซื้อแทน
            brand = name.strip() if name.strip() and name.strip().upper() != code.upper() else (source or code)
        if not brand:
            continue  # หนึ่งเลขนับให้แบรนด์แรกที่ตรงเท่านั้น
        found.append({"awb": awb, "brand": brand, "source": source, "customer_code": code,
                      "customer_name": name, "first_scan": first})

    # นับลูกค้า VIP ทุกราย: เรียงตามยอด แบรนด์ใน [KEYWORDS] ที่ยังเป็น 0 ไว้ท้ายตาราง
    counts: dict[str, int] = {}
    for a in found:
        counts[a["brand"]] = counts.get(a["brand"], 0) + 1
    if vip_prefix:
        brands = sorted(counts, key=lambda b: -counts[b]) + [b for b in cfg.keywords if b not in counts]
    else:
        brands = list(cfg.keywords)  # นับเฉพาะแบรนด์ที่กำหนด: ลำดับคงที่ตาม config
    n_hours = max(0, int((until - p_start).total_seconds() // 3600))
    hours = [p_start + timedelta(hours=i) for i in range(n_hours)]
    grid = {b: [0] * n_hours for b in brands}
    for a in found:
        idx = int((datetime.strptime(a["first_scan"], FMT) - p_start).total_seconds() // 3600)
        if 0 <= idx < n_hours:
            grid[a["brand"]][idx] += 1
    order = {b: i for i, b in enumerate(brands)}
    awbs = sorted(found, key=lambda a: (order[a["brand"]], a["first_scan"]))
    pending = con.execute(
        "SELECT COUNT(*) FROM scans s LEFT JOIN orders o ON o.awb=s.awb "
        "WHERE s.period_start=? AND (o.awb IS NULL OR o.status='error')", (key,)).fetchone()[0]
    return ReportData(cfg.title, p_start, p_end, until, brands, hours, grid, awbs, pending)


def attach_status(jms: "Jms", cfg: Config, d: ReportData) -> None:
    """ตรวจสถานะที่คลังของทุกเลข ณ ตอนนี้ (ติดตามพัสดุ ทีละ 100 เลข) แล้วใส่ลงใน d.awbs"""
    now = datetime.now()
    batch = 100
    for i in range(0, len(d.awbs), batch):
        chunk = d.awbs[i:i + batch]
        try:
            data = jms.post("/operatingplatform/podTracking/inner/query/keywordList", "trackingExpress",
                            {"keywordList": [a["awb"] for a in chunk], "trackingTypeEnum": "WAYBILL",
                             "countryId": "1"})
            found = hub_status.find_scan_lists(data)
        except TokenError:
            raise
        except Exception as e:
            log.warning("ตรวจสถานะไม่สำเร็จ %d เลข: %s", len(chunk), e)
            found = None
        for a in chunk:
            if found is None:
                a.update(status="ตรวจไม่ได้", group=hub_status.OTHER, out_time=None, last_type="",
                         last_time=None, last_site="", stuck_hours=None)
                continue
            r = hub_status.classify(found.get(a["awb"], []), cfg.hub_site)
            group = hub_status.GROUP.get(r["status"], hub_status.OTHER)
            first = datetime.strptime(a["first_scan"], FMT)
            a.update(status=r["status"], group=group, out_time=r["out_time"], last_type=r["last_type"],
                     last_time=r["last_time"], last_site=r["last_site"],
                     stuck_hours=round((now - first).total_seconds() / 3600, 1)
                     if group == hub_status.STUCK else None)
    d.status_checked_at = now
    log.info("ตรวจสถานะ %d เลข: %s", len(d.awbs),
             ", ".join(f"{g} {d.group_total(g)}" for g in hub_status.GROUPS))


def _md_row(cells: list[str], grey: bool = False) -> dict:
    """หนึ่งแถวของตารางใน Feishu card (column_set) คอลัมน์แรกชิดซ้าย ที่เหลือชิดขวา"""
    cols = [{"tag": "column", "width": "weighted", "weight": 2 if i == 0 else 1, "vertical_align": "center",
             "elements": [{"tag": "markdown", "text_align": "left" if i == 0 else "right", "content": c}]}
            for i, c in enumerate(cells)]
    row = {"tag": "column_set", "flex_mode": "none", "horizontal_spacing": "small", "columns": cols}
    if grey:
        row["background_style"] = "grey"
    return row


def build_card(d: ReportData, image_key: str | None, has_excel: bool) -> dict:
    S, K, M = hub_status.SENT, hub_status.STUCK, hub_status.MISSED
    info = f"รอบ **{d.period_label}**　|　" + (
        "**ยอดทั้งรอบ (ปิดรอบ)**" if d.closing else f"ข้อมูลถึง **{d.until:%d/%m %H:%M} น.**")
    if d.status_checked_at:
        info += f"　|　สถานะ ณ {d.status_checked_at:%H:%M} น."
    hour_sum = sum(d.last_hour(b) for b in d.brands)
    stuck, missed = d.group_total(K), d.group_total(M)
    summary = _md_row([
        f"เข้าคลัง\n**{d.grand_total:,}** ชิ้น" + (f" <font color='green'>+{hour_sum:,}</font>" if hour_sum else ""),
        f"สแกนออกแล้ว\n<font color='green'>**{d.group_total(S):,}**</font>",
        f"คงค้างในคลัง\n<font color='red'>**{stuck:,}**</font>" if stuck else "คงค้างในคลัง\n**0**",
        f"หลุดสแกนส่ง\n<font color='orange'>**{missed:,}**</font>" if missed else "หลุดสแกนส่ง\n**0**",
    ], grey=True)
    elements: list[dict] = [{"tag": "markdown", "content": info}, summary, {"tag": "hr"},
                            _md_row(["**แบรนด์**", "**เข้าคลัง**", "**ออกแล้ว**", "**คงค้าง**", "**หลุดสแกน**"])]
    for b in d.brands:
        tot, hr = d.total(b), d.last_hour(b)
        if not tot:
            g = lambda t: f"<font color='grey'>{t}</font>"
            elements.append(_md_row([g(b), g("0"), g("–"), g("–"), g("–")]))
            continue
        k, m = d.group_count(b, K), d.group_count(b, M)
        elements.append(_md_row([
            f"**{b}**",
            f"**{tot:,}**" + (f" <font color='green'>+{hr:,}</font>" if hr else ""),
            f"{d.group_count(b, S):,}",
            f"<font color='red'>**{k:,}**</font>" if k else "0",
            f"<font color='orange'>**{m:,}**</font>" if m else "0",
        ]))
    if image_key:
        elements += [{"tag": "hr"},
                     {"tag": "img", "img_key": image_key, "mode": "fit_horizontal", "preview": True,
                      "alt": {"tag": "plain_text", "content": "ตารางยอดรายชั่วโมง"}}]
    notes = ["+n = เข้าคลังในชั่วโมงล่าสุด · คงค้าง = ยังไม่มีสแกนส่งออกจาก KKN_GW"]
    if has_excel:
        notes.append("📎 ไฟล์ Excel (สรุป + รายการ AWB + เลขที่ค้าง) ส่งแยกด้านล่าง")
    if d.pending:
        notes.append(f"⚠️ ยังค้นแหล่งที่มาไม่ได้ {d.pending:,} เลข จะลองใหม่รอบหน้า")
    elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": "\n".join(notes)}]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "green" if d.closing else "red",
                   "title": {"tag": "plain_text", "content": ("✅ สรุปปิดรอบ · " if d.closing else "👑 ") + d.title}},
        "elements": elements,
    }


def build_message(d: ReportData) -> str:
    S, K, M = hub_status.SENT, hub_status.STUCK, hub_status.MISSED
    lines = [("✅ สรุปปิดรอบ " if d.closing else "") + d.title, f"รอบ {d.period_label}",
             "ยอดทั้งรอบ" if d.closing else f"ข้อมูลถึง {d.until:%d/%m %H:%M} น.", "",
             "แบรนด์: เข้าคลัง (+ชม.ล่าสุด) / ออกแล้ว / คงค้าง / หลุดสแกน"]
    for b in d.brands:
        lines.append(f"• {b}: {d.total(b):,} (+{d.last_hour(b):,}) / {d.group_count(b, S):,} / "
                     f"{d.group_count(b, K):,} / {d.group_count(b, M):,}")
    lines.append(f"รวม: {d.grand_total:,} / {d.group_total(S):,} / {d.group_total(K):,} / {d.group_total(M):,}")
    if d.pending:
        lines.append(f"⚠️ ยังค้นแหล่งที่มาไม่ได้ {d.pending:,} เลข จะลองใหม่รอบหน้า")
    return "\n".join(lines)



# ---------------------------------------------------------------------------
def prune(con: sqlite3.Connection, now: datetime, out_dir: str) -> None:
    con.execute("DELETE FROM scans WHERE period_start < ?", ((now - timedelta(days=3)).strftime(FMT),))
    con.execute("DELETE FROM fetch_state WHERE period_start < ?", ((now - timedelta(days=3)).strftime(FMT),))
    con.execute("DELETE FROM orders WHERE checked_at < ?", ((now - timedelta(days=7)).strftime(FMT),))
    con.execute("DELETE FROM sent WHERE period_start < ?", ((now - timedelta(days=7)).strftime(FMT),))
    con.commit()
    out = out_dir
    if os.path.isdir(out):
        for f in os.listdir(out):
            p = os.path.join(out, f)
            if time.time() - os.path.getmtime(p) > 3 * 86400:
                try:
                    os.remove(p)
                except OSError:
                    pass




# ---------------------------------------------------------------------------
# Feishu (ใช้ feishu_client ของโปรแกรมหลัก)
# ---------------------------------------------------------------------------
def send_report(app_id: str, app_secret: str, chat_id: str, d: ReportData, png: str | None,
                xlsx: str | None) -> None:
    import feishu_client  # import ตอนใช้ ให้ทดสอบส่วนอื่นได้โดยไม่ต้องมีโปรแกรมหลัก

    token = feishu_client.get_token(app_id, app_secret)
    image_key = None
    if png:
        try:
            image_key = feishu_client.upload_image(token, png)
        except Exception as e:
            log.error("อัปโหลดรูปไม่สำเร็จ (ส่งการ์ดโดยไม่มีรูป): %s", e)
    card = json.dumps(build_card(d, image_key, bool(xlsx)), ensure_ascii=False)
    feishu_client.send_card(token, chat_id, card)
    if xlsx:
        feishu_client.send_file(token, chat_id, feishu_client.upload_file(token, xlsx))


def send_text(app_id: str, app_secret: str, chat_id: str, text: str) -> None:
    import feishu_client

    feishu_client.send_text(feishu_client.get_token(app_id, app_secret), chat_id, text)


# ---------------------------------------------------------------------------
# หนึ่งรอบ
# ---------------------------------------------------------------------------
_run_lock = threading.Lock()


def run(cfg: Config, jms_token: str, base_dir: str, now: datetime | None = None, *, send: bool = False,
        force: bool = False, app_id: str = "", app_secret: str = "", chat_id: str = "") -> dict:
    """ดึง/ค้น/นับ/ตรวจสถานะ แล้ว (ถ้า send) ส่ง Feishu

    คืน dict: data (ReportData), text, png, xlsx, sent (bool), skipped (เหตุผลที่ไม่ส่ง หรือ "")
    โยน TokenError เมื่อ JMS token ใช้ไม่ได้ / RuntimeError เมื่อมีรอบอื่นรันอยู่
    """
    if not _run_lock.acquire(blocking=False):
        raise RuntimeError("VIP AWB กำลังทำงานอยู่ รอให้รอบก่อนหน้าเสร็จ")
    try:
        if not cfg.keywords:
            raise RuntimeError("ยังไม่ได้กำหนดแบรนด์ใน [VIP_AWB] brands")
        if not jms_token:
            raise TokenError("ไม่พบ JMS token")
        now = now or datetime.now()
        p_start, p_end, until = report_window(now, cfg.day_start_hour)
        out_dir = os.path.join(base_dir, "VipAwb")
        os.makedirs(out_dir, exist_ok=True)
        con = open_db(os.path.join(base_dir, "vip_awb.db"))
        try:
            log.info("VIP AWB รอบ %s ข้อมูลถึง %s", f"{p_start:%d/%m %H:%M}", f"{until:%d/%m %H:%M}")
            jms = Jms(jms_token)
            t0 = time.time()
            fetch_scans(con, jms, cfg, p_start, until)
            looked, errors = lookup_sources(con, jms, cfg, p_start)
            log.info("VIP AWB ค้นแหล่งที่มา %d เลข (ผิดพลาด %d) %.0f วินาที", looked, errors, time.time() - t0)
            data = build_report(con, cfg, p_start, p_end, until)
            attach_status(jms, cfg, data)
            text = build_message(data)
            stem = os.path.join(out_dir, f"VIP_KKN_{p_start:%Y%m%d_%H%M}_ถึง_{until:%Y%m%d_%H%M}")
            png = render_png(data, stem + ".png") if cfg.send_image else None
            xlsx = write_excel(data, stem + ".xlsx") if cfg.send_excel else None
            result = {"data": data, "text": text, "png": png, "xlsx": xlsx, "sent": False, "skipped": ""}
            key = (p_start.strftime(FMT), until.strftime(FMT))
            if send:
                if con.execute("SELECT 1 FROM sent WHERE period_start=? AND data_until=?", key).fetchone() \
                        and not force:
                    result["skipped"] = "รอบนี้ส่งไปแล้ว"
                else:
                    send_report(app_id, app_secret, chat_id, data, png, xlsx)
                    con.execute("INSERT OR REPLACE INTO sent VALUES(?,?,?)", (*key, datetime.now().strftime(FMT)))
                    con.commit()
                    result["sent"] = True
            prune(con, now, out_dir)
            return result
        finally:
            con.close()
    finally:
        _run_lock.release()
