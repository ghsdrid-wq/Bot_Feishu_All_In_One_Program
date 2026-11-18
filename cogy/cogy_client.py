# -*- coding: utf-8 -*-
"""
CoGY API Client (Auto-Login + Incremental Hourly Cache + Minimal-Column Fetcher)
รองรับการดึงข้อมูล 2 ระบบ:
1. WCS (device_code=101): ยอดคัดแยกแยกตามเครื่องจ่ายงาน (supNo 1..23) ผ่าน /report/sup/list (1 request/ชม.)
2. DWS (device_code=201): ยอดสแกนแยกตามเครื่อง DWS (dwsNo) ผ่าน /dws/log/assembly/sorting/list
   โดยเก็บเฉพาะคอลัมน์ scanTime และ dwsNo เท่านั้น
"""
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
import requests

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
TOKEN_FILE = CACHE_DIR / "cogy_token.json"
HOURLY_CACHE_FILE = CACHE_DIR / "hourly_cache.json"


INVENTORY_FILE = BASE_DIR / "cogy" / "device_inventory.json"
if not INVENTORY_FILE.exists():
    INVENTORY_FILE = BASE_DIR / "device_inventory.json"


def _load_inventory_maps() -> tuple[dict[str, str], dict[int, str], dict[str, str]]:
    dws_ip_map: dict[str, str] = {}
    wcs_no_map: dict[int, str] = {}
    wcs_ip_map: dict[str, str] = {}
    if INVENTORY_FILE.exists():
        try:
            inv = json.loads(INVENTORY_FILE.read_text(encoding="utf-8"))
            for row in inv.get("dws", []) + inv.get("lws", []):
                dws_ip_map[str(row["ip"])] = str(row["name"])
            for row in inv.get("autopacking", []):
                wcs_no_map[int(row["no"])] = str(row["name"])
                wcs_ip_map[str(row["ip"])] = str(row["name"])
        except Exception:
            pass
    return dws_ip_map, wcs_no_map, wcs_ip_map


_DWS_IP_MAP, _WCS_NO_MAP, _WCS_IP_MAP = _load_inventory_maps()


def _dws_sort_key(label: str):
    parts = str(label).split(".")
    if len(parts) == 4 and parts[-1].isdigit():
        return (0, int(parts[-1]), label)
    return (1, 0, str(label))


def format_dws_name(dws_ip: str, with_ip: bool = False) -> str:
    s = str(dws_ip).strip()
    name = _DWS_IP_MAP.get(s, s)
    return f"{name} ({s})" if with_ip and name != s else name


def format_wcs_name(wcs_key: str) -> str:
    s = str(wcs_key).strip()
    if s in _WCS_IP_MAP:
        return _WCS_IP_MAP[s]
    if s.isdigit():
        no = int(s)
        return _WCS_NO_MAP.get(no, f"Autopacking_KKN_{no:03d}_002")
    parts = s.split(".")
    if len(parts) == 4 and parts[-1].isdigit():
        no = int(parts[-1]) - 100
        if no >= 1:
            return _WCS_NO_MAP.get(no, f"Autopacking_KKN_{no:03d}_002")
    return s


def short_machine_name(name: str) -> str:
    """แปลงชื่อเครื่องเป็นชื่อย่ออ่านง่าย เช่น DWS 03, Auto 01, LWS 01"""
    s = str(name or "")
    for prefix, label in (("DWS_KKN_", "DWS"), ("Autopacking_KKN_", "Auto"), ("LWS_KKN_", "LWS")):
        if s.upper().startswith(prefix.upper()):
            num = s[len(prefix):].split("_")[0]
            return f"{label} {num[-2:].zfill(2)}"
    return s.replace("_KKN_", " ").replace("_002", "").replace("_", " ")


def build_hour_slots(start_dt: datetime, end_dt: datetime) -> list[tuple[datetime, datetime, str]]:
    """แบ่งช่วงเวลาเป็นสล็อตรายชั่วโมง [(slot_start, slot_end, slot_key), ...]"""
    slots = []
    cur = start_dt.replace(minute=0, second=0, microsecond=0)
    while cur <= end_dt:
        nxt = cur + timedelta(hours=1)
        s_from = max(cur, start_dt)
        s_to = min(nxt - timedelta(seconds=1), end_dt)
        if s_from <= s_to:
            slots.append((s_from, s_to, cur.strftime("%Y-%m-%d %H:00")))
        cur = nxt
    return slots


class CogyClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        wcs_device_code: str = "101",
        dws_device_code: str = "201",
        wcs_metric: str = "scanBarcodeQuantity",
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.wcs_device_code = str(wcs_device_code)
        self.dws_device_code = str(dws_device_code)
        self.wcs_metric = wcs_metric
        self.session = requests.Session()
        self.token = self._load_cached_token()

    def _load_cached_token(self) -> str:
        if TOKEN_FILE.exists():
            try:
                data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
                return str(data.get("token") or "")
            except Exception:
                pass
        return ""

    def _save_cached_token(self, token: str):
        try:
            TOKEN_FILE.write_text(
                json.dumps({"token": token, "updated_at": datetime.now().isoformat()}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def login(self) -> str:
        """ล็อกอินอัตโนมัติด้วย username/password ของระบบ CoGY"""
        url = f"{self.base_url}/basic/login"
        r = self.session.post(
            url,
            json={"username": self.username, "password": self.password},
            timeout=10,
        )
        r.raise_for_status()
        res = r.json()
        if res.get("code") != 20000:
            raise RuntimeError(f"CoGY Login failed: {res.get('msg')}")
        token = res["data"]["result"]["token"]
        self.token = token
        self._save_cached_token(token)
        return token

    def _post(self, path: str, device_code: str, payload: dict, timeout: int = 25) -> dict:
        if not self.token:
            self.login()
        url = f"{self.base_url}{path}"
        for attempt in range(2):
            headers = {
                "token": self.token,
                "device_code": str(device_code),
                "Content-Type": "application/json",
            }
            r = self.session.post(url, headers=headers, json=payload, timeout=timeout)
            if r.status_code == 401 and attempt == 0:
                self.login()
                continue
            r.raise_for_status()
            data = r.json()
            if data.get("code") == 401 and attempt == 0:
                self.login()
                continue
            return data
        raise RuntimeError(f"API call failed after re-login: {path}")

    def _load_hourly_cache(self) -> dict:
        if HOURLY_CACHE_FILE.exists():
            try:
                return json.loads(HOURLY_CACHE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"wcs": {}, "dws": {}}

    def _save_hourly_cache(self, cache: dict):
        try:
            # เก็บย้อนหลังไม่เกิน 3 วัน (72 ชม.) เพื่อลดขนาดไฟล์และโหลดไวเมื่อรัน 24/7
            for kind in ("wcs", "dws"):
                keys = sorted(cache.get(kind, {}).keys())
                if len(keys) > 72:
                    for old_k in keys[:-72]:
                        cache[kind].pop(old_k, None)
            HOURLY_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _fetch_wcs_slot(self, s_from: datetime, s_to: datetime) -> dict[str, int]:
        """ดึงยอด WCS 1 ช่วงเวลา (เฉพาะ supNo และจำนวน)"""
        is_full_hour = (
            s_from.minute == 0
            and s_from.second == 0
            and s_to.minute == 59
            and s_to.second == 59
            and s_from.hour == s_to.hour
        )
        if is_full_hour:
            res = self._post(
                "/report/sup/list",
                self.wcs_device_code,
                {
                    "startTime": s_from.strftime("%Y-%m-%d %H:%M:%S"),
                    "endTime": s_to.strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            if res.get("code") == 20000:
                counts: dict[str, int] = {}
                for row in (res.get("data") or {}).get("result") or []:
                    sup_no = row.get("supNo")
                    if sup_no is None:
                        continue
                    val = int(row.get(self.wcs_metric) or 0)
                    counts[str(sup_no)] = counts.get(str(sup_no), 0) + val
                return counts

        # Fallback: กรณีช่วงเวลาไม่เต็มชั่วโมง ใช้ sorting/list และเก็บเฉพาะ supNo
        counts_counter: Counter = Counter()
        cur_page = 1
        page_size = 5000
        while True:
            res = self._post(
                "/log/assembly/sorting/list",
                self.wcs_device_code,
                {
                    "turns": "",
                    "chuteNo": "",
                    "supNo": "",
                    "exceptionCode": "",
                    "startTime": s_from.strftime("%Y-%m-%d %H:%M:%S"),
                    "endTime": s_to.strftime("%Y-%m-%d %H:%M:%S"),
                    "barcodeList": [],
                    "packageNumber": "",
                    "carNo": "",
                    "dbCode": self.wcs_device_code,
                    "businessTimeType": "",
                    "dwsNo": "",
                    "pageSize": page_size,
                    "curPage": cur_page,
                },
            )
            result = (res.get("data") or {}).get("result") or {}
            records = result.get("records") or []
            for rec in records:
                sup_no = rec.get("supNo")
                if sup_no is not None:
                    counts_counter[str(sup_no)] += 1
            total = int(result.get("total") or 0)
            if cur_page * page_size >= total or not records:
                break
            cur_page += 1
        return dict(counts_counter)

    def _fetch_dws_slot(self, s_from: datetime, s_to: datetime) -> dict[str, int]:
        """ดึงยอด DWS 1 ช่วงเวลา (เก็บเฉพาะ dwsNo และ scanTime)"""
        counts: Counter = Counter()
        cur_page = 1
        page_size = 5000
        while True:
            res = self._post(
                "/dws/log/assembly/sorting/list",
                self.dws_device_code,
                {
                    "turns": "",
                    "chuteNo": "",
                    "supNo": "",
                    "exceptionCode": "",
                    "startTime": s_from.strftime("%Y-%m-%d %H:%M:%S"),
                    "endTime": s_to.strftime("%Y-%m-%d %H:%M:%S"),
                    "barcodeList": [],
                    "packageNumber": "",
                    "carNo": "",
                    "dbCode": self.dws_device_code,
                    "businessTimeType": "",
                    "dwsNo": "",
                    "pageSize": page_size,
                    "curPage": cur_page,
                },
            )
            if res.get("code") != 20000:
                break
            result = (res.get("data") or {}).get("result") or {}
            records = result.get("records") or []
            for rec in records:
                dws_no = rec.get("dwsNo")
                if dws_no:
                    counts[str(dws_no)] += 1
            total = int(result.get("total") or 0)
            if cur_page * page_size >= total or not records:
                break
            cur_page += 1
        return dict(counts)

    def fetch_hourly_matrices(
        self,
        start_dt: datetime,
        end_dt: datetime,
        use_cache: bool = True,
    ) -> dict:
        """
        ดึงข้อมูลทั้ง WCS และ DWS แยกเป็นรายชั่วโมง
        คืนค่า dict:
        {
          "slots": ["2026-11-13 12:00", ...],
          "wcs": {slot_key: {sup_no_str: count}},
          "dws": {slot_key: {dws_ip: count}},
          "wcs_machines": [...],
          "dws_machines": [...],
        }
        """
        now = datetime.now()
        slots = build_hour_slots(start_dt, end_dt)
        cache = self._load_hourly_cache() if use_cache else {"wcs": {}, "dws": {}}

        wcs_data: dict[str, dict[str, int]] = {}
        dws_data: dict[str, dict[str, int]] = {}
        cache_dirty = False

        for s_from, s_to, slot_key in slots:
            is_full_hour = (
                s_from.minute == 0
                and s_from.second == 0
                and s_to.minute == 59
                and s_to.second == 59
                and s_from.hour == s_to.hour
            )
            is_finalized = is_full_hour and (now > s_to + timedelta(minutes=2))

            # WCS
            if use_cache and is_full_hour and slot_key in cache.get("wcs", {}):
                wcs_data[slot_key] = cache["wcs"][slot_key]
            else:
                w_counts = self._fetch_wcs_slot(s_from, s_to)
                wcs_data[slot_key] = w_counts
                if is_finalized:
                    cache.setdefault("wcs", {})[slot_key] = w_counts
                    cache_dirty = True

            # DWS
            if use_cache and is_full_hour and slot_key in cache.get("dws", {}):
                dws_data[slot_key] = cache["dws"][slot_key]
            else:
                d_counts = self._fetch_dws_slot(s_from, s_to)
                dws_data[slot_key] = d_counts
                if is_finalized:
                    cache.setdefault("dws", {})[slot_key] = d_counts
                    cache_dirty = True

        if cache_dirty:
            self._save_hourly_cache(cache)

        wcs_machines = sorted(
            {m for slot in wcs_data.values() for m in slot.keys()},
            key=lambda x: (0, int(x)) if str(x).isdigit() else (1, str(x)),
        )
        dws_machines = sorted(
            {m for slot in dws_data.values() for m in slot.keys()},
            key=_dws_sort_key,
        )

        return {
            "start_dt": start_dt,
            "end_dt": end_dt,
            "slots": [s[2] for s in slots],
            "wcs": wcs_data,
            "dws": dws_data,
            "wcs_machines": wcs_machines,
            "dws_machines": dws_machines,
        }
