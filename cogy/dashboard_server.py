# -*- coding: utf-8 -*-
"""
CoGY Real-Time Live Dashboard Server (Figma-Style Operations Command Center)
รันบนพอร์ต 8505 (เข้าได้จากเครื่องนี้และวง LAN: http://<LAN_IP>:8505)
ดึงยอดชั่วโมงปัจจุบันแบบสดๆ (Real-time) และใช้ Cache สำหรับชั่วโมงที่จบแล้ว
"""
import atexit
import gc
import hashlib
import hmac
import json
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .cogy_client import CogyClient, format_dws_name, format_wcs_name
from .report_builder import compute_summary

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "cogy_config.json"
if not CONFIG_FILE.exists() and (BASE_DIR / "cogy_config.example.json").exists():
    CONFIG_FILE.write_text((BASE_DIR / "cogy_config.example.json").read_text(encoding="utf-8"), encoding="utf-8")
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
ACCESS_LOG_FILE = LOGS_DIR / "access_security.log"
APP_EVENTS_LOG_FILE = LOGS_DIR / "app_events.log"
JOB_STATE_FILE = BASE_DIR / "cache" / "job_state.json"

_CONFIG_LOCK = threading.Lock()
_EVENT_LOG_LOCK = threading.Lock()
_JOB_LOCK = threading.Lock()
_API_HEALTH_LOCK = threading.Lock()
_API_HEALTH = {
    "ok": True,
    "first_fail_ts": 0.0,
    "last_ok_ts": 0.0,
    "consecutive_fails": 0,
    "alert_logged": False,
    "last_error": "",
}
_JOB_STATE: dict = {}
_FAILED_LOCK = threading.Lock()
_FAILED_LOGINS: dict[str, dict] = {}
_VISITOR_LOCK = threading.Lock()
_VIEWERS: dict[str, dict] = {}
_RATE_LOCK = threading.Lock()
_REQ_TIMESTAMPS: dict[str, list[float]] = {}

_TUNNEL_PROC = None
_SERVER_REF = None
_PUBLIC_URL = ""
_LOCKDOWN = False
_LOCKDOWN_REASON = ""

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 300
SESSION_TTL_SECONDS = 43200  # 12 hours
FLOOD_MAX_REQ_10S = 80
SUSPICIOUS_PATH_RE = re.compile(
    r"(\.env|\.git|wp-login|wp-admin|phpmyadmin|etc/passwd|\.\./|<script|union\+select|union%20select|/cgi-bin|/actuator|/shell|/xmlrpc\.php)",
    re.IGNORECASE,
)


def get_lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.30.36.12", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def load_config() -> dict:
    with _CONFIG_LOCK:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def update_config_field(key: str, value) -> None:
    with _CONFIG_LOCK:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        cfg[key] = value
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def get_session_secret() -> bytes:
    cfg = load_config()
    sec = cfg.get("dashboard_session_secret", "")
    if not sec:
        sec = secrets.token_hex(32)
        update_config_field("dashboard_session_secret", sec)
    return sec.encode("utf-8")


import base64


def make_session_token(viewer_name: str = "ผู้ใช้งาน") -> tuple[str, str]:
    exp = int(time.time()) + SESSION_TTL_SECONDS
    sid = secrets.token_hex(8)
    clean_name = (viewer_name or "ผู้ใช้งาน").strip()[:40] or "ผู้ใช้งาน"
    b64_name = base64.urlsafe_b64encode(clean_name.encode("utf-8")).decode("ascii").rstrip("=")
    payload = f"{exp}.{sid}.{b64_name}"
    sig = hmac.new(get_session_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}", sid


def parse_session_token(token: str) -> dict | None:
    if not token or token.count(".") != 3:
        return None
    try:
        exp_str, sid, b64_name, sig = token.split(".", 3)
        if int(exp_str) < int(time.time()):
            return None
        payload = f"{exp_str}.{sid}.{b64_name}"
        expected = hmac.new(get_session_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        pad = "=" * ((4 - len(b64_name) % 4) % 4)
        name = base64.urlsafe_b64decode(b64_name + pad).decode("utf-8")
        return {"sid": sid, "name": name}
    except Exception:
        return None


def log_security_event(event: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(ACCESS_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {event}\n")
    except Exception:
        pass
    log_app_event("SECURITY", "warn", event)


def log_app_event(category: str, level: str, message: str) -> None:
    """บันทึกเหตุการณ์ลง logs/app_events.log พร้อมหมุนไฟล์เมื่อเกิน 2MB"""
    rec = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cat": (category or "SYSTEM").upper(),
        "level": (level or "info").lower(),
        "msg": str(message),
    }
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with _EVENT_LOG_LOCK:
        try:
            if APP_EVENTS_LOG_FILE.exists() and APP_EVENTS_LOG_FILE.stat().st_size > 2 * 1024 * 1024:
                lines = APP_EVENTS_LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
                APP_EVENTS_LOG_FILE.write_text("\n".join(lines[-1000:]) + "\n", encoding="utf-8")
            with open(APP_EVENTS_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass


def get_app_events_tail(limit: int = 200, category: str = "ALL") -> list[dict]:
    cat_filter = (category or "ALL").upper()
    with _EVENT_LOG_LOCK:
        if not APP_EVENTS_LOG_FILE.exists():
            return []
        try:
            raw_lines = APP_EVENTS_LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return []
    out: list[dict] = []
    for ln in reversed(raw_lines):
        if not ln.strip():
            continue
        try:
            item = json.loads(ln)
        except Exception:
            continue
        if cat_filter != "ALL" and item.get("cat", "SYSTEM").upper() != cat_filter:
            continue
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _record_api_success() -> None:
    now_ts = time.time()
    recover_msg = ""
    with _API_HEALTH_LOCK:
        was_alerted = _API_HEALTH["alert_logged"]
        fails = _API_HEALTH["consecutive_fails"]
        down_mins = int((now_ts - _API_HEALTH["first_fail_ts"]) // 60) if _API_HEALTH["first_fail_ts"] else 0
        _API_HEALTH["ok"] = True
        _API_HEALTH["first_fail_ts"] = 0.0
        _API_HEALTH["last_ok_ts"] = now_ts
        _API_HEALTH["consecutive_fails"] = 0
        _API_HEALTH["alert_logged"] = False
        _API_HEALTH["last_error"] = ""
        if was_alerted or fails >= 3:
            recover_msg = f"เชื่อมต่อ CoGY Server กลับมาปกติแล้ว (ขาดการติดต่อไป {down_mins} นาที)"
    if recover_msg:
        log_app_event("API", "ok", recover_msg)


def _record_api_failure(err_msg: str) -> None:
    now_ts = time.time()
    alert_msg = ""
    with _API_HEALTH_LOCK:
        _API_HEALTH["ok"] = False
        _API_HEALTH["consecutive_fails"] += 1
        _API_HEALTH["last_error"] = str(err_msg)[:200]
        if _API_HEALTH["first_fail_ts"] == 0.0:
            _API_HEALTH["first_fail_ts"] = now_ts
        down_sec = now_ts - _API_HEALTH["first_fail_ts"]
        if down_sec >= 600 and not _API_HEALTH["alert_logged"]:
            _API_HEALTH["alert_logged"] = True
            alert_msg = f"แจ้งเตือน: CoGY Server ขาดการเชื่อมต่อนานเกิน {int(down_sec // 60)} นาที ({_API_HEALTH['last_error']})"
    if alert_msg:
        log_app_event("ALERT", "error", alert_msg)


def parse_device(ua: str) -> str:
    u = (ua or "").lower()
    if "iphone" in u:
        return "iPhone"
    if "ipad" in u:
        return "iPad"
    if "android" in u:
        return "Android"
    if "windows" in u:
        return "Windows PC"
    if "macintosh" in u or "mac os" in u:
        return "Mac"
    if "linux" in u:
        return "Linux"
    return "Other"


def record_viewer_activity(sid: str, name: str, ip: str, ua: str, is_login: bool = False) -> None:
    now_ts = time.time()
    now_str = datetime.now().strftime("%H:%M:%S")
    dev = parse_device(ua)
    with _VISITOR_LOCK:
        rec = _VIEWERS.get(sid)
        if not rec:
            rec = {
                "sid": sid,
                "name": name,
                "ip": ip,
                "device": dev,
                "login_at": datetime.now().strftime("%d/%m %H:%M:%S"),
                "last_seen": now_str,
                "last_ts": now_ts,
                "hits": 0,
            }
        rec["name"] = name
        rec["ip"] = ip
        rec["device"] = dev
        rec["last_seen"] = now_str
        rec["last_ts"] = now_ts
        rec["hits"] += 1
        _VIEWERS[sid] = rec
    if is_login:
        log_security_event(f"LOGIN_OK name={name} ip={ip} device={dev}")


def get_viewers_snapshot() -> dict:
    now_ts = time.time()
    with _VISITOR_LOCK:
        rows = sorted(_VIEWERS.values(), key=lambda x: x["last_ts"], reverse=True)[:25]
        out = []
        active_cnt = 0
        for r in rows:
            online = (now_ts - r["last_ts"]) <= 20
            if online:
                active_cnt += 1
            out.append({
                "name": r["name"],
                "ip": r["ip"],
                "device": r["device"],
                "login_at": r["login_at"],
                "last_seen": r["last_seen"],
                "hits": r["hits"],
                "online": online,
            })
        return {"active_count": active_cnt, "total_sessions": len(_VIEWERS), "viewers": out}


def trigger_cyber_lockdown(attack_type: str, ip: str, path: str, ua: str) -> None:
    global _LOCKDOWN, _LOCKDOWN_REASON
    if _LOCKDOWN:
        return
    _LOCKDOWN = True
    _LOCKDOWN_REASON = attack_type
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_security_event(f"CYBER_LOCKDOWN type={attack_type} ip={ip} path={path} ua={ua}")
    print(f"[SECURITY ALERT] {now_str} | {attack_type} | IP={ip} | Path={path} -> ปิด Live Dashboard ทันที!")

    def _alert_and_stop():
        try:
            from .feishu_dispatcher import FeishuDispatcher
            cfg = load_config()
            if cfg.get("feishu_app_id") and cfg.get("feishu_chat_id"):
                bot = FeishuDispatcher(
                    app_id=cfg["feishu_app_id"],
                    app_secret=cfg["feishu_app_secret"],
                    chat_id=cfg["feishu_chat_id"],
                    webhook_url=cfg.get("feishu_webhook", ""),
                )
                card = {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "template": "red",
                        "title": {"tag": "plain_text", "content": "แจ้งเตือนภัยไซเบอร์ — ปิด Live Dashboard ทันที"},
                        "subtitle": {"tag": "plain_text", "content": f"ตรวจพบพฤติกรรมโจมตีระบบ ({now_str})"},
                    },
                    "elements": [
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": (
                                    f"**ประเภทการโจมตี:** <font color='red'>**{attack_type}**</font>\n"
                                    f"**IP ต้นทาง:** `{ip}`\n"
                                    f"**อุปกรณ์ (UA):** `{(ua or '-')[:120]}`\n"
                                    f"**Path เป้าหมาย:** `{(path or '-')[:120]}`\n"
                                    f"**การดำเนินการ:** ปิด Cloudflare Tunnel และหยุด Live Dashboard Server อัตโนมัติแล้ว"
                                ),
                            },
                        }
                    ],
                }
                bot.send_message("interactive", card)
        except Exception as e:
            log_security_event(f"FEISHU_ALERT_ERR err={e}")
        time.sleep(0.8)
        if _TUNNEL_PROC and _TUNNEL_PROC.poll() is None:
            _TUNNEL_PROC.terminate()
        if _SERVER_REF is not None:
            _SERVER_REF.shutdown()

    threading.Thread(target=_alert_and_stop, daemon=True).start()


def check_request_threat(ip: str, path: str, ua: str) -> str | None:
    if SUSPICIOUS_PATH_RE.search(path or ""):
        return "Exploit / Path Scan Probe"
    now = time.time()
    with _RATE_LOCK:
        ts_list = [t for t in _REQ_TIMESTAMPS.get(ip, []) if now - t <= 10.0]
        ts_list.append(now)
        _REQ_TIMESTAMPS[ip] = ts_list
        if len(ts_list) > FLOOD_MAX_REQ_10S:
            return f"HTTP Flood / DoS ({len(ts_list)} reqs/10s)"
    return None


def is_rate_limited(ip: str) -> int:
    now = time.time()
    with _FAILED_LOCK:
        rec = _FAILED_LOGINS.get(ip)
        if not rec:
            return 0
        if rec.get("locked_until", 0) > now:
            return int(rec["locked_until"] - now) + 1
        if rec.get("locked_until", 0) and rec["locked_until"] <= now:
            _FAILED_LOGINS.pop(ip, None)
        return 0


def record_login_failure(ip: str, ua: str = "") -> int:
    now = time.time()
    with _FAILED_LOCK:
        rec = _FAILED_LOGINS.get(ip)
        if not rec or (now - rec.get("first_ts", 0) > LOCKOUT_SECONDS):
            rec = {"count": 1, "first_ts": now, "locked_until": 0}
        else:
            rec["count"] += 1
        if rec["count"] >= MAX_LOGIN_ATTEMPTS:
            rec["locked_until"] = now + LOCKOUT_SECONDS
        _FAILED_LOGINS[ip] = rec
        left = max(0, MAX_LOGIN_ATTEMPTS - rec["count"])
        total_recent_fails = sum(
            r["count"] for r in _FAILED_LOGINS.values() if now - r.get("first_ts", 0) <= LOCKOUT_SECONDS
        )
    log_security_event(f"LOGIN_FAIL ip={ip} count={rec['count']} total_recent={total_recent_fails}")
    if rec["count"] >= MAX_LOGIN_ATTEMPTS:
        trigger_cyber_lockdown(f"Brute-Force Login ({rec['count']} ครั้งจาก IP เดียว)", ip, "/login", ua)
    elif total_recent_fails >= 10:
        trigger_cyber_lockdown(f"Distributed Brute-Force ({total_recent_fails} ครั้ง)", ip, "/login", ua)
    return left


def clear_login_failures(ip: str) -> None:
    with _FAILED_LOCK:
        _FAILED_LOGINS.pop(ip, None)


_LIVE_LOCK = threading.Lock()
_LIVE_CACHE = {"ts": 0.0, "key": "", "payload": None}


def build_live_payload(start_str: str | None = None, end_str: str | None = None) -> dict:
    cfg = load_config()
    now = datetime.now()
    shift_a_start = int(cfg.get("shift_a_start", 12))
    shift_b_start = int(cfg.get("shift_b_start", 0))

    if start_str and end_str:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
        end_dt = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")
    else:
        start_dt = now.replace(hour=shift_a_start, minute=0, second=0, microsecond=0)
        if now.hour < shift_a_start:
            start_dt -= timedelta(days=1)
        end_dt = now.replace(minute=59, second=59, microsecond=0)

    cache_key = f"{start_dt:%Y%m%d%H%M}_{end_dt:%Y%m%d%H%M}"
    with _LIVE_LOCK:
        if _LIVE_CACHE["payload"] is not None and _LIVE_CACHE["key"] == cache_key and (time.time() - _LIVE_CACHE["ts"] < 2.5):
            return _LIVE_CACHE["payload"]

    client = CogyClient(
        base_url=cfg["cogy_base_url"],
        username=cfg["cogy_username"],
        password=cfg["cogy_password"],
        wcs_device_code=cfg.get("wcs_device_code", "101"),
        dws_device_code=cfg.get("dws_device_code", "201"),
        wcs_metric=cfg.get("wcs_metric", "scanBarcodeQuantity"),
    )

    try:
        data = client.fetch_hourly_matrices(start_dt, end_dt, use_cache=True)
        _record_api_success()
    except Exception as ex:
        _record_api_failure(str(ex))
        raise
    summary = compute_summary(data, shift_a_start=shift_a_start, shift_b_start=shift_b_start)

    slots = data["slots"]
    cur_slot = now.strftime("%Y-%m-%d %H:00")
    if cur_slot not in slots and slots:
        cur_slot = slots[-1]

    # เช็กยอด 5 นาทีล่าสุดเพื่อดูว่าเครื่องไหนกำลังคัดแยกอยู่สดๆ ตอนนี้
    recent_from = now - timedelta(minutes=5)
    try:
        recent_wcs = client._fetch_wcs_slot(recent_from, now)
    except Exception:
        recent_wcs = {}
    try:
        recent_dws = client._fetch_dws_slot(recent_from, now)
    except Exception:
        recent_dws = {}

    wcs_rows = []
    for m in data["wcs_machines"]:
        ms = summary["wcs"]["per_machine"][m]
        cur_val = int(data["wcs"].get(cur_slot, {}).get(m, 0))
        rec_val = int(recent_wcs.get(m, 0))
        is_live = rec_val > 0 or (cur_slot == now.strftime("%Y-%m-%d %H:00") and cur_val > 0)
        wcs_rows.append({
            "id": m,
            "name": format_wcs_name(m),
            "total": ms["total"],
            "shift_a": ms["shift_a"],
            "shift_b": ms["shift_b"],
            "avg_hr": ms["avg_hr"],
            "peak_hr": ms["peak_hr"],
            "active_hours": ms["active_hours"],
            "cur_hour": cur_val,
            "recent_5m": rec_val,
            "is_live": is_live,
            "hourly": [int(data["wcs"].get(s, {}).get(m, 0)) for s in slots],
        })

    dws_rows = []
    for m in data["dws_machines"]:
        ms = summary["dws"]["per_machine"][m]
        cur_val = int(data["dws"].get(cur_slot, {}).get(m, 0))
        rec_val = int(recent_dws.get(m, 0))
        is_live = rec_val > 0 or (cur_slot == now.strftime("%Y-%m-%d %H:00") and cur_val > 0)
        dws_rows.append({
            "id": m,
            "name": format_dws_name(m, with_ip=False),
            "ip": m,
            "total": ms["total"],
            "shift_a": ms["shift_a"],
            "shift_b": ms["shift_b"],
            "avg_hr": ms["avg_hr"],
            "peak_hr": ms["peak_hr"],
            "active_hours": ms["active_hours"],
            "cur_hour": cur_val,
            "recent_5m": rec_val,
            "is_live": is_live,
            "hourly": [int(data["dws"].get(s, {}).get(m, 0)) for s in slots],
        })

    hour_labels = [s.split(" ")[1][:5] for s in slots]
    wcs_hourly = [summary["wcs"]["per_hour"].get(s, 0) for s in slots]
    dws_hourly = [summary["dws"]["per_hour"].get(s, 0) for s in slots]
    tot_hourly = [w + d for w, d in zip(wcs_hourly, dws_hourly)]
    cum_hourly = []
    acc = 0
    for v in tot_hourly:
        acc += v
        cum_hourly.append(acc)

    peak_idx = max(range(len(tot_hourly)), key=lambda i: tot_hourly[i]) if tot_hourly else 0

    payload = {
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "branch": cfg.get("branch_name", "KKN"),
        "start": start_dt.strftime("%Y-%m-%d %H:%M"),
        "end": end_dt.strftime("%Y-%m-%d %H:%M"),
        "cur_slot": cur_slot.split(" ")[1][:5] if " " in cur_slot else cur_slot,
        "slots": slots,
        "hour_labels": hour_labels,
        "wcs_hourly": wcs_hourly,
        "dws_hourly": dws_hourly,
        "tot_hourly": tot_hourly,
        "cum_hourly": cum_hourly,
        "wcs_total": summary["wcs"]["total"],
        "wcs_shift_a": summary["wcs"]["shift_a"],
        "wcs_shift_b": summary["wcs"]["shift_b"],
        "dws_total": summary["dws"]["total"],
        "dws_shift_a": summary["dws"]["shift_a"],
        "dws_shift_b": summary["dws"]["shift_b"],
        "grand_total": summary["wcs"]["total"] + summary["dws"]["total"],
        "peak_hour_label": hour_labels[peak_idx] if hour_labels else "-",
        "peak_hour_val": tot_hourly[peak_idx] if tot_hourly else 0,
        "wcs_live_count": sum(1 for r in wcs_rows if r["is_live"]),
        "dws_live_count": sum(1 for r in dws_rows if r["is_live"]),
        "wcs_machines": wcs_rows,
        "dws_machines": dws_rows,
    }

    with _LIVE_LOCK:
        _LIVE_CACHE["ts"] = time.time()
        _LIVE_CACHE["key"] = cache_key
        _LIVE_CACHE["payload"] = payload
        _LIVE_CACHE["data"] = data
        _LIVE_CACHE["summary"] = summary

    return payload


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="th">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>J&T KKN คลัง 2 — Live Operations Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Noto+Sans+Thai:wght@400;500;600;700&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
  <style>
    :root {
      --bg: #F4F6F9;
      --surface: #FFFFFF;
      --surface-2: #F8FAFC;
      --border: #E2E8F0;
      --text: #0F172A;
      --muted: #64748B;
      --red: #D32F2F;
      --red-soft: #FEF2F2;
      --orange: #FF6D00;
      --orange-soft: #FFF7ED;
      --green: #10B981;
      --green-soft: #ECFDF5;
      --shadow: 0 4px 20px rgba(15, 23, 42, 0.05);
    }
    body.dark {
      --bg: #0B1120;
      --surface: #111827;
      --surface-2: #1E293B;
      --border: #1F2937;
      --text: #F8FAFC;
      --muted: #94A3B8;
      --red-soft: rgba(211, 47, 47, 0.15);
      --orange-soft: rgba(255, 109, 0, 0.15);
      --green-soft: rgba(16, 185, 129, 0.15);
      --shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
      width: 100%;
      max-width: 100%;
      overflow-x: hidden;
    }
    body {
      font-family: 'Inter', 'Noto Sans Thai', Tahoma, sans-serif;
      background: var(--bg);
      color: var(--text);
      transition: background 0.25s, color 0.25s;
      padding-bottom: 40px;
    }
    .topbar {
      background: linear-gradient(135deg, #B71C1C 0%, #D32F2F 55%, #FF6D00 100%);
      color: #fff;
      padding: 16px 28px;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      box-shadow: 0 4px 18px rgba(183, 28, 28, 0.25);
    }
    .brand { display: flex; align-items: center; gap: 14px; }
    .brand-badge {
      background: rgba(255,255,255,0.18);
      backdrop-filter: blur(8px);
      padding: 8px 14px;
      border-radius: 12px;
      font-weight: 800;
      font-size: 16px;
      letter-spacing: 0.5px;
    }
    .brand-title h1 { font-size: 18px; font-weight: 700; }
    .brand-title p { font-size: 12.5px; opacity: 0.9; margin-top: 2px; }
    .controls { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .live-pill {
      display: inline-flex; align-items: center; gap: 8px;
      background: rgba(16, 185, 129, 0.2);
      border: 1px solid rgba(16, 185, 129, 0.6);
      padding: 6px 14px; border-radius: 999px;
      font-size: 12.5px; font-weight: 600;
    }
    .dot-pulse {
      width: 9px; height: 9px; border-radius: 50%; background: #34D399;
      box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.8);
      animation: pulse 1.6s infinite;
    }
    @keyframes pulse {
      0% { box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.8); }
      70% { box-shadow: 0 0 0 10px rgba(52, 211, 153, 0); }
      100% { box-shadow: 0 0 0 0 rgba(52, 211, 153, 0); }
    }
    .btn {
      background: rgba(255,255,255,0.16);
      border: 1px solid rgba(255,255,255,0.3);
      color: #fff; padding: 7px 14px; border-radius: 10px;
      font-size: 12.5px; font-weight: 600; cursor: pointer;
      transition: 0.15s;
    }
    .btn:hover { background: rgba(255,255,255,0.28); }
    .container { width: 100%; max-width: 1440px; margin: 22px auto 0; padding: 0 22px; overflow-x: hidden; }

    /* KPI Grid */
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
      gap: 16px;
      margin-bottom: 20px;
    }
    .kpi-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 18px 20px;
      box-shadow: var(--shadow);
      position: relative;
      overflow: hidden;
    }
    .kpi-card::before {
      content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 5px;
      background: var(--accent, var(--red));
    }
    .kpi-label { font-size: 12.5px; font-weight: 600; color: var(--muted); display: flex; justify-content: space-between; align-items: center; }
    .kpi-val { font-size: 30px; font-weight: 800; margin: 8px 0 6px; letter-spacing: -0.5px; }
    .kpi-sub { font-size: 12px; color: var(--muted); }
    .badge {
      display: inline-flex; align-items: center; gap: 5px;
      padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 700;
    }
    .badge-live { background: var(--green-soft); color: var(--green); }
    .badge-red { background: var(--red-soft); color: var(--red); }
    .badge-orange { background: var(--orange-soft); color: var(--orange); }

    /* Panels */
    .grid-2 {
      display: grid;
      grid-template-columns: 1.55fr 1fr;
      gap: 18px;
      margin-bottom: 20px;
    }
    @media (max-width: 1024px) { .grid-2 { grid-template-columns: 1fr; } }
    .grid-2 > * { min-width: 0; }
    .panel {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 20px;
      box-shadow: var(--shadow);
      margin-bottom: 20px;
      min-width: 0;
    }
    .panel-hdr {
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 14px; flex-wrap: wrap; gap: 10px;
    }
    .panel-title { font-size: 15.5px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
    .tab-group { display: inline-flex; background: var(--surface-2); padding: 3px; border-radius: 10px; border: 1px solid var(--border); }
    .tab-btn {
      border: none; background: transparent; color: var(--muted);
      padding: 5px 12px; border-radius: 8px; font-size: 12px; font-weight: 600; cursor: pointer;
    }
    .tab-btn.active { background: var(--surface); color: var(--text); box-shadow: 0 1px 4px rgba(0,0,0,0.08); }

    /* Live Machine Cards */
    .machine-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(185px, 1fr));
      gap: 12px;
    }
    .m-card {
      background: var(--surface-2);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 12px 14px;
      transition: transform 0.15s, border-color 0.15s;
    }
    .m-card:hover { transform: translateY(-2px); }
    .m-card.live { border-color: rgba(16, 185, 129, 0.55); background: var(--green-soft); }
    .m-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
    .m-name { font-weight: 700; font-size: 13.5px; }
    .m-tot { font-size: 20px; font-weight: 800; }
    .m-meta { font-size: 11.5px; color: var(--muted); margin-top: 4px; display: flex; justify-content: space-between; }
    .prog-bg { height: 5px; background: var(--border); border-radius: 99px; margin-top: 8px; overflow: hidden; }
    .prog-fill { height: 100%; border-radius: 99px; transition: width 0.4s ease; }

    /* Table */
    .tbl-wrap { width: 100%; max-width: 100%; overflow-x: auto; border-radius: 12px; border: 1px solid var(--border); }
    table { width: 100%; border-collapse: collapse; font-size: 12px; text-align: center; }
    th, td { padding: 8px 10px; border-bottom: 1px solid var(--border); border-right: 1px solid var(--border); white-space: nowrap; }
    th { background: var(--surface-2); font-weight: 700; color: var(--text); position: sticky; top: 0; }
    td:first-child, th:first-child { position: sticky; left: 0; background: var(--surface); font-weight: 700; text-align: left; z-index: 2; }
    tr:hover td { filter: brightness(0.97); }

    /* Game-style floating +N delta animation */
    .kpi-card, .m-card { position: relative; overflow: visible; min-width: 0; }
    .chart-stage { position: relative; width: 100%; max-width: 100%; height: 310px; overflow: hidden; }
    .chart-stage canvas { width: 100% !important; max-width: 100% !important; }
    .game-pop {
      position: absolute;
      pointer-events: none;
      z-index: 30;
      font-weight: 900;
      font-size: 14px;
      line-height: 1;
      padding: 4px 9px;
      border-radius: 999px;
      color: #FFFFFF;
      background: linear-gradient(135deg, #10B981, #059669);
      box-shadow: 0 4px 14px rgba(16, 185, 129, 0.45);
      border: 1.5px solid rgba(255, 255, 255, 0.85);
      white-space: nowrap;
      transform: translate(-50%, 0);
      animation: gameFloatPop 1.65s cubic-bezier(0.22, 1, 0.36, 1) forwards;
    }
    .game-pop.wcs {
      background: linear-gradient(135deg, #EF4444, #B91C1C);
      box-shadow: 0 4px 14px rgba(239, 68, 68, 0.45);
    }
    .game-pop.dws {
      background: linear-gradient(135deg, #F97316, #EA580C);
      box-shadow: 0 4px 14px rgba(249, 115, 22, 0.45);
    }
    @keyframes gameFloatPop {
      0%   { opacity: 0; transform: translate(-50%, 8px) scale(0.65); }
      18%  { opacity: 1; transform: translate(-50%, -6px) scale(1.16); }
      35%  { opacity: 1; transform: translate(-50%, -14px) scale(1.0); }
      100% { opacity: 0; transform: translate(-50%, -46px) scale(0.95); }
    }
    .tbl-wrap { position: relative; }
    .cell-flash {
      animation: cellPulse 1.3s ease-out;
    }
    @keyframes cellPulse {
      0%   { box-shadow: inset 0 0 0 2px #10B981, 0 0 10px rgba(16, 185, 129, 0.55); }
      100% { box-shadow: inset 0 0 0 0 transparent, 0 0 0 transparent; }
    }
    /* Car Odometer Rolling Digits */
    .odo-wrap { display: inline-flex; align-items: baseline; font-variant-numeric: tabular-nums; }
    .odo-col {
      display: inline-block;
      height: 1.15em;
      line-height: 1.15em;
      overflow: hidden;
      vertical-align: bottom;
    }
    .odo-strip {
      display: flex;
      flex-direction: column;
      transition: transform 0.72s cubic-bezier(0.22, 1, 0.36, 1);
      will-change: transform;
    }
    .odo-digit {
      height: 1.15em;
      line-height: 1.15em;
      text-align: center;
    }
    .odo-sep { display: inline-block; line-height: 1.15em; }

    /* Mobile overrides (must stay at end of stylesheet to override desktop rules) */
    @media (max-width: 640px) {
      .topbar { padding: 10px 12px; gap: 8px; }
      .brand { gap: 8px; }
      .brand-badge { padding: 5px 9px; font-size: 12px; }
      .brand-title h1 { font-size: 14.5px; }
      .brand-title p { font-size: 11px; }
      .top-actions { width: 100%; gap: 6px; justify-content: flex-start; }
      .btn { padding: 5px 9px; font-size: 11px; }
      .live-pill { padding: 5px 9px; font-size: 11px; }
      .container { padding: 0 10px; margin-top: 10px; }
      .kpi-grid { grid-template-columns: repeat(2, 1fr); gap: 8px; margin-bottom: 10px; }
      .kpi-card { padding: 10px 11px; border-radius: 12px; }
      .kpi-label { font-size: 11px; flex-wrap: wrap; gap: 4px; }
      .kpi-val { font-size: 21px; margin: 3px 0; }
      .kpi-sub { font-size: 10px; }
      .grid-2 { gap: 10px; margin-bottom: 10px; }
      .panel { padding: 12px; border-radius: 12px; margin-bottom: 10px; }
      .panel-hdr { margin-bottom: 10px; gap: 8px; }
      .panel-title { font-size: 13.5px; }
      .tab-btn { padding: 4px 9px; font-size: 11px; }
      .chart-stage { height: 230px; }
      .machine-grid { grid-template-columns: repeat(2, 1fr); gap: 8px; }
      .m-card { padding: 9px 10px; border-radius: 10px; }
      .m-name { font-size: 11.5px; }
      .m-tot { font-size: 17px; }
      .m-meta { font-size: 10px; }
      th, td { padding: 6px 7px; font-size: 11px; }
      .game-pop { font-size: 12px; padding: 3px 7px; }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand">
      <div class="brand-badge">J&T EXPRESS</div>
      <div class="brand-title">
        <h1>KKN คลัง 2 Live Dashboard</h1>
        <p id="subInfo">กำลังเชื่อมต่อข้อมูลสด KKN คลัง 2 ...</p>
      </div>
    </div>
    <div class="controls">
      <div class="live-pill">
        <span class="dot-pulse"></span>
        <span id="liveTimer">สด • อาจมีดีเลย์ 5-10 วิ</span>
      </div>
      <button class="btn" onclick="fetchLive(true)">รีเฟรชทันที</button>
      <button class="btn" onclick="toggleTheme()">สลับโหมดสว่าง/มืด</button>
      <button class="btn" onclick="logout()">ออกจากระบบ</button>
    </div>
  </header>

  <main class="container">
    <!-- 4 KPI Cards -->
    <section class="kpi-grid">
      <div class="kpi-card" style="--accent: #D32F2F;">
        <div class="kpi-label">WCS (AutoPacking) <span class="badge badge-red" id="kpiWcsLive">0/0 ทำงาน</span></div>
        <div class="kpi-val" id="kpiWcsTotal">-</div>
        <div class="kpi-sub" id="kpiWcsSub">กะ A: - | กะ B: -</div>
      </div>
      <div class="kpi-card" style="--accent: #FF6D00;">
        <div class="kpi-label">DWS (เครื่องสแกน) <span class="badge badge-orange" id="kpiDwsLive">0/0 ทำงาน</span></div>
        <div class="kpi-val" id="kpiDwsTotal">-</div>
        <div class="kpi-sub" id="kpiDwsSub">กะ A: - | กะ B: -</div>
      </div>
      <div class="kpi-card" style="--accent: #10B981;">
        <div class="kpi-label">ชั่วโมงที่กำลังทำงาน <span class="badge badge-live" id="kpiCurSlot">LIVE</span></div>
        <div class="kpi-val" id="kpiCurHourVal">-</div>
        <div class="kpi-sub" id="kpiPeakSub">Peak สูงสุด: -</div>
      </div>
      <div class="kpi-card" style="--accent: #3B82F6;">
        <div class="kpi-label">ยอดรวมทั้งหมด (WCS + DWS)</div>
        <div class="kpi-val" id="kpiGrandTotal">-</div>
        <div class="kpi-sub" id="kpiUpdated">อัปเดตล่าสุด: -</div>
      </div>
    </section>

    <!-- Charts -->
    <section class="grid-2">
      <div class="panel" style="margin-bottom:0;">
        <div class="panel-hdr">
          <div class="panel-title">กราฟยอดคัดแยกรายชั่วโมง & แนวโน้มยอดสะสม</div>
        </div>
        <div class="chart-stage" id="hourlyStage"><canvas id="hourlyChart"></canvas></div>
      </div>
      <div class="panel" style="margin-bottom:0;">
        <div class="panel-hdr">
          <div class="panel-title">อันดับประสิทธิภาพรายเครื่อง</div>
          <div class="tab-group">
            <button class="tab-btn active" onclick="setRankTab('dws', this)">DWS</button>
            <button class="tab-btn" onclick="setRankTab('wcs', this)">WCS</button>
          </div>
        </div>
        <div class="chart-stage" id="rankStage"><canvas id="rankChart"></canvas></div>
      </div>
    </section>

    <!-- Live Machine Status Grid -->
    <section class="panel">
      <div class="panel-hdr">
        <div class="panel-title">สถานะเครื่องแบบเรียลไทม์ (เครื่องไหนกำลังคัดแยกอยู่ตอนนี้)</div>
        <div class="tab-group">
          <button class="tab-btn active" onclick="setLiveTab('all', this)">ทั้งหมด</button>
          <button class="tab-btn" onclick="setLiveTab('live', this)">กำลังทำงานเท่านั้น</button>
          <button class="tab-btn" onclick="setLiveTab('wcs', this)">เฉพาะ WCS</button>
          <button class="tab-btn" onclick="setLiveTab('dws', this)">เฉพาะ DWS</button>
        </div>
      </div>
      <div class="machine-grid" id="machineGrid"></div>
    </section>

    <!-- Full Matrix Table -->
    <section class="panel">
      <div class="panel-hdr">
        <div class="panel-title">ตารางสรุปรายชั่วโมง & วิเคราะห์กะ A / กะ B</div>
        <div class="tab-group">
          <button class="tab-btn active" onclick="setTableTab('dws', this)">ตาราง DWS</button>
          <button class="tab-btn" onclick="setTableTab('wcs', this)">ตาราง WCS</button>
        </div>
      </div>
      <div class="tbl-wrap" id="tableWrap"></div>
    </section>

    <!-- Active Viewers & Security Panel -->
    <section class="panel">
      <div class="panel-hdr">
        <div class="panel-title">ผู้เข้าดู Dashboard & ความปลอดภัย</div>
        <span class="badge badge-live" id="viewerCountBadge">กำลังดู 0 คน</span>
      </div>
      <div class="tbl-wrap" id="viewerWrap"></div>
    </section>
  </main>

  <script>
    let latestData = null;
    let rankTab = 'dws';
    let liveTab = 'all';
    let tableTab = 'dws';
    let hourlyChartObj = null;
    let rankChartObj = null;
    let countdown = 10;

    function fmt(n) { return Number(n || 0).toLocaleString('en-US'); }

    function toggleTheme() {
      document.body.classList.toggle('dark');
      if (latestData) renderCharts(latestData);
    }

    function setRankTab(t, btn) {
      rankTab = t;
      btn.parentElement.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      if (latestData) renderRankChart(latestData, true);
    }

    function setLiveTab(t, btn) {
      liveTab = t;
      btn.parentElement.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      if (latestData) renderMachineGrid(latestData);
    }

    function setTableTab(t, btn) {
      tableTab = t;
      btn.parentElement.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      if (latestData) renderTable(latestData);
    }

    async function logout() {
      try { await fetch('/logout', { method: 'POST' }); } catch (e) {}
      location.reload();
    }

    async function fetchLive(manual = false) {
      try {
        const res = await fetch('/api/live');
        if (res.status === 401) {
          location.reload();
          return;
        }
        const data = await res.json();
        latestData = data;
        renderAll(data);
        countdown = 10;
      } catch (e) {
        document.getElementById('subInfo').textContent = 'เชื่อมต่อ API ชั่วคราวไม่สำเร็จ';
      }
    }

    const prevNums = {};
    let prevHourlyTot = null;
    const prevRankTotals = { dws: null, wcs: null };

    function spawnGamePop(container, x, y, delta, variant = '') {
      if (!container || !delta || delta <= 0) return;
      const badge = document.createElement('div');
      badge.className = 'game-pop' + (variant ? ' ' + variant : '');
      badge.textContent = `+${fmt(delta)}`;
      badge.style.left = typeof x === 'number' ? `${x}px` : x;
      badge.style.top = typeof y === 'number' ? `${y}px` : y;
      container.appendChild(badge);
      setTimeout(() => badge.remove(), 1700);
    }

    function shortName(name) {
      const s = String(name || '');
      let m = s.match(/^DWS_KKN_(\d+)/i);
      if (m) return `DWS ${m[1].slice(-2).padStart(2, '0')}`;
      m = s.match(/^Autopacking_KKN_(\d+)/i);
      if (m) return `Auto ${m[1].slice(-2).padStart(2, '0')}`;
      m = s.match(/^LWS_KKN_(\d+)/i);
      if (m) return `LWS ${m[1].slice(-2).padStart(2, '0')}`;
      return s.replace(/_KKN_/g, ' ').replace(/_002$/g, '').replace(/_/g, ' ');
    }

    function renderOdometer(el, targetVal) {
      if (!el) return;
      const str = fmt(Math.max(0, Math.round(Number(targetVal || 0))));
      const prevStr = el.dataset.odoStr || '';
      if (prevStr === str && el.querySelector('.odo-wrap')) return;
      el.dataset.odoStr = str;

      const existingWrap = el.querySelector('.odo-wrap');
      const needsRebuild = !existingWrap || prevStr.length !== str.length ||
        [...str].some((ch, idx) => (/\d/.test(ch) !== /\d/.test(prevStr[idx] || '')));

      if (needsRebuild) {
        let html = '<span class="odo-wrap">';
        for (const ch of str) {
          if (/\d/.test(ch)) {
            html += `<span class="odo-col"><span class="odo-strip" style="transform:translateY(0em)">` +
              [0,1,2,3,4,5,6,7,8,9].map(d => `<span class="odo-digit">${d}</span>`).join('') +
              `</span></span>`;
          } else {
            html += `<span class="odo-sep">${ch}</span>`;
          }
        }
        html += '</span>';
        el.innerHTML = html;
        const strips = el.querySelectorAll('.odo-strip');
        let dIdx = 0;
        requestAnimationFrame(() => {
          for (const ch of str) {
            if (/\d/.test(ch)) {
              const st = strips[dIdx++];
              if (st) st.style.transform = `translateY(-${Number(ch) * 1.15}em)`;
            }
          }
        });
      } else {
        const strips = existingWrap.querySelectorAll('.odo-strip');
        let dIdx = 0;
        for (const ch of str) {
          if (/\d/.test(ch)) {
            const st = strips[dIdx++];
            if (st) st.style.transform = `translateY(-${Number(ch) * 1.15}em)`;
          }
        }
      }
    }

    function animateNumber(el, targetVal, duration = 650, variant = '') {
      if (!el) return;
      const key = el.id || el.dataset.animKey;
      const hadPrev = key && prevNums[key] !== undefined;
      const startVal = hadPrev ? prevNums[key] : 0;
      const endVal = Number(targetVal || 0);
      if (key) prevNums[key] = endVal;
      if (hadPrev && endVal > startVal) {
        const card = el.closest('.kpi-card');
        if (card) spawnGamePop(card, '78%', '14px', endVal - startVal, variant);
      }
      renderOdometer(el, endVal);
    }

    function renderAll(d) {
      document.getElementById('subInfo').textContent = `สาขา ${d.branch}  |  ${d.start} - ${d.end}`;
      animateNumber(document.getElementById('kpiWcsTotal'), d.wcs_total, 650, 'wcs');
      document.getElementById('kpiWcsSub').textContent = `กะ A: ${fmt(d.wcs_shift_a)}  |  กะ B: ${fmt(d.wcs_shift_b)}`;
      document.getElementById('kpiWcsLive').textContent = `${d.wcs_live_count}/${d.wcs_machines.length} กำลังทำงาน`;

      animateNumber(document.getElementById('kpiDwsTotal'), d.dws_total, 650, 'dws');
      document.getElementById('kpiDwsSub').textContent = `กะ A: ${fmt(d.dws_shift_a)}  |  กะ B: ${fmt(d.dws_shift_b)}`;
      document.getElementById('kpiDwsLive').textContent = `${d.dws_live_count}/${d.dws_machines.length} กำลังทำงาน`;

      const curIdx = d.hour_labels.indexOf(d.cur_slot);
      const curVal = curIdx >= 0 ? d.tot_hourly[curIdx] : 0;
      document.getElementById('kpiCurSlot').textContent = `ชม. ${d.cur_slot}`;
      animateNumber(document.getElementById('kpiCurHourVal'), curVal, 650, '');
      document.getElementById('kpiPeakSub').textContent = `Peak: ${d.peak_hour_label} น. (${fmt(d.peak_hour_val)})`;

      animateNumber(document.getElementById('kpiGrandTotal'), d.grand_total, 650, '');
      document.getElementById('kpiUpdated').textContent = `อัปเดตล่าสุด: ${d.updated_at}`;

      renderCharts(d);
      renderMachineGrid(d);
      renderTable(d);
      renderViewers(d.security_viewers);
    }

    function fmtK(v) {
      const n = Number(v || 0);
      if (n >= 1000) {
        const k = n / 1000;
        return (k % 1 === 0 ? k.toFixed(0) : k.toFixed(1)) + 'k';
      }
      return String(n);
    }

    function renderCharts(d) {
      const isDark = document.body.classList.contains('dark');
      const isMobile = window.innerWidth <= 640;
      const gridColor = isDark ? 'rgba(255,255,255,0.08)' : 'rgba(15,23,42,0.07)';
      const tickColor = isDark ? '#94A3B8' : '#475569';
      const lineColor = isDark ? '#38BDF8' : '#1E293B';

      if (!hourlyChartObj) {
        const ctx1 = document.getElementById('hourlyChart').getContext('2d');
        hourlyChartObj = new Chart(ctx1, {
          data: {
            labels: d.hour_labels,
            datasets: [
              { type: 'bar', label: 'WCS', data: d.wcs_hourly, backgroundColor: '#D32F2F', borderRadius: 4, barPercentage: 0.8, categoryPercentage: 0.86, maxBarThickness: 26, stack: 's1' },
              { type: 'bar', label: 'DWS', data: d.dws_hourly, backgroundColor: '#FF6D00', borderRadius: 4, barPercentage: 0.8, categoryPercentage: 0.86, maxBarThickness: 26, stack: 's1' },
              { type: 'line', label: 'ยอดสะสม', data: d.cum_hourly, borderColor: lineColor, backgroundColor: lineColor, borderWidth: 2, tension: 0.3, pointRadius: isMobile ? 2 : 3, yAxisID: 'y1' },
            ]
          },
          options: {
            animation: false,
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            layout: { padding: { top: 8, left: 0, right: 0, bottom: 0 } },
            scales: {
              x: {
                ticks: {
                  color: tickColor,
                  font: { size: isMobile ? 10 : 11 },
                  maxRotation: 0,
                  minRotation: 0,
                  autoSkip: true,
                  maxTicksLimit: isMobile ? 7 : 18
                },
                grid: { display: false }
              },
              y: {
                stacked: true,
                min: 0,
                beginAtZero: true,
                grace: '12%',
                ticks: { color: tickColor, font: { size: isMobile ? 10 : 11 }, maxTicksLimit: 6, callback: fmtK },
                grid: { color: gridColor }
              },
              y1: {
                position: 'right',
                min: 0,
                beginAtZero: true,
                grace: '10%',
                ticks: { color: tickColor, font: { size: isMobile ? 10 : 11 }, maxTicksLimit: 6, callback: fmtK },
                grid: { display: false }
              }
            },
            plugins: {
              legend: {
                labels: { color: tickColor, boxWidth: 12, boxHeight: 8, padding: 10, font: { size: isMobile ? 11 : 12 } }
              }
            }
          }
        });
      } else {
        hourlyChartObj.data.labels = d.hour_labels;
        hourlyChartObj.data.datasets[0].data = d.wcs_hourly;
        hourlyChartObj.data.datasets[1].data = d.dws_hourly;
        hourlyChartObj.data.datasets[2].data = d.cum_hourly;
        hourlyChartObj.data.datasets[2].borderColor = lineColor;
        hourlyChartObj.data.datasets[2].backgroundColor = lineColor;
        hourlyChartObj.options.scales.x.ticks.color = tickColor;
        hourlyChartObj.options.scales.x.ticks.maxTicksLimit = isMobile ? 7 : 18;
        hourlyChartObj.options.scales.y.ticks.color = tickColor;
        hourlyChartObj.options.scales.y.grid.color = gridColor;
        hourlyChartObj.options.scales.y1.ticks.color = tickColor;
        hourlyChartObj.options.plugins.legend.labels.color = tickColor;
        hourlyChartObj.update('none');
      }

      if (prevHourlyTot && prevHourlyTot.length === d.tot_hourly.length) {
        const stage = document.getElementById('hourlyStage');
        const metaWcs = hourlyChartObj.getDatasetMeta(0);
        const metaDws = hourlyChartObj.getDatasetMeta(1);
        d.tot_hourly.forEach((val, idx) => {
          const delta = val - (prevHourlyTot[idx] || 0);
          if (delta > 0) {
            const barEl = (d.dws_hourly[idx] > 0 ? metaDws.data[idx] : metaWcs.data[idx]) || metaDws.data[idx] || metaWcs.data[idx];
            const x = barEl ? barEl.x : '50%';
            const y = barEl ? Math.max(8, barEl.y - 18) : 20;
            const variant = (d.wcs_hourly[idx] > 0 && d.dws_hourly[idx] === 0) ? 'wcs' : (d.dws_hourly[idx] > 0 && d.wcs_hourly[idx] === 0) ? 'dws' : '';
            spawnGamePop(stage, x, y, delta, variant);
          }
        });
      }
      prevHourlyTot = [...d.tot_hourly];

      renderRankChart(d);
    }

    function renderRankChart(d, isTabSwitch = false) {
      const isDark = document.body.classList.contains('dark');
      const gridColor = isDark ? 'rgba(255,255,255,0.08)' : 'rgba(15,23,42,0.07)';
      const tickColor = isDark ? '#94A3B8' : '#475569';
      const rows = [...(rankTab === 'dws' ? d.dws_machines : d.wcs_machines)].sort((a, b) => b.total - a.total);
      const color = rankTab === 'dws' ? '#FF6D00' : '#D32F2F';

      if (!rankChartObj) {
        const ctx2 = document.getElementById('rankChart').getContext('2d');
        rankChartObj = new Chart(ctx2, {
          type: 'bar',
          data: {
            labels: rows.map(r => shortName(r.name)),
            datasets: [{ label: 'ยอดรวม (ชิ้น)', data: rows.map(r => r.total), backgroundColor: color, borderRadius: 5 }]
          },
          options: {
            animation: false,
            indexAxis: 'y', responsive: true, maintainAspectRatio: false,
            scales: {
              x: {
                min: 0,
                beginAtZero: true,
                ticks: { color: tickColor, font: { size: window.innerWidth <= 640 ? 10 : 11 }, maxTicksLimit: 6, callback: fmtK },
                grid: { color: gridColor }
              },
              y: { ticks: { color: tickColor, font: { size: window.innerWidth <= 640 ? 10 : 11 } }, grid: { display: false } }
            },
            plugins: { legend: { display: false } }
          }
        });
      } else {
        rankChartObj.data.labels = rows.map(r => shortName(r.name));
        rankChartObj.data.datasets[0].data = rows.map(r => r.total);
        rankChartObj.data.datasets[0].backgroundColor = color;
        rankChartObj.options.scales.x.ticks.color = tickColor;
        rankChartObj.options.scales.x.grid.color = gridColor;
        rankChartObj.options.scales.y.ticks.color = tickColor;
        rankChartObj.update('none');
      }

      const prevMap = prevRankTotals[rankTab];
      if (prevMap && !isTabSwitch) {
        const stage = document.getElementById('rankStage');
        const meta = rankChartObj.getDatasetMeta(0);
        let popped = 0;
        rows.forEach((r, idx) => {
          const prevVal = prevMap[r.name];
          if (prevVal !== undefined && r.total > prevVal && popped < 6) {
            const barEl = meta.data[idx];
            if (barEl) {
              const x = Math.min(stage.clientWidth - 36, Math.max(40, barEl.x - 10));
              const y = Math.max(4, barEl.y - 12);
              setTimeout(() => spawnGamePop(stage, x, y, r.total - prevVal, rankTab), popped * 90);
              popped++;
            }
          }
        });
      }
      const nextMap = {};
      rows.forEach(r => { nextMap[r.name] = r.total; });
      prevRankTotals[rankTab] = nextMap;
    }

    let prevGridKey = '';
    let prevGridStats = {};

    function renderMachineGrid(d) {
      const all = [
        ...d.wcs_machines.map(r => ({ ...r, kind: 'WCS' })),
        ...d.dws_machines.map(r => ({ ...r, kind: 'DWS' })),
      ];
      const filtered = all.filter(r => {
        if (liveTab === 'live') return r.is_live;
        if (liveTab === 'wcs') return r.kind === 'WCS';
        if (liveTab === 'dws') return r.kind === 'DWS';
        return true;
      });
      const maxTot = Math.max(1, ...filtered.map(r => r.total));
      const grid = document.getElementById('machineGrid');
      if (!filtered.length) {
        prevGridKey = '';
        prevGridStats = {};
        grid.innerHTML = '<div style="color:var(--muted);padding:16px;">ไม่มีเครื่องที่กำลังทำงานในขณะนี้</div>';
        return;
      }

      const gridKey = liveTab + '|' + filtered.map(r => `${r.kind}:${r.name}`).join(',');
      if (gridKey !== prevGridKey) {
        grid.innerHTML = filtered.map(r => {
          const pct = Math.round((r.total / maxTot) * 100);
          const barColor = r.kind === 'WCS' ? '#D32F2F' : '#FF6D00';
          const statusBadge = r.is_live
            ? `<span class="badge badge-live">● กำลังทำงาน (+${fmt(r.recent_5m)})</span>`
            : `<span class="badge" style="background:var(--border);color:var(--muted);">○ พัก/รอ</span>`;
          return `
            <div class="m-card ${r.is_live ? 'live' : ''}">
              <div class="m-top">
                <span class="m-name">${escHtml(shortName(r.name))}</span>
                <span class="m-badge-wrap">${statusBadge}</span>
              </div>
              <div class="m-tot">${fmt(r.total)}</div>
              <div class="m-meta">
                <span>ชม.นี้: <b class="m-cur">${fmt(r.cur_hour)}</b></span>
                <span>เฉลี่ย: <b class="m-avg">${fmt(r.avg_hr)}/ชม.</b></span>
              </div>
              <div class="prog-bg"><div class="prog-fill" style="width:${pct}%;background:${barColor};"></div></div>
            </div>
          `;
        }).join('');
        prevGridKey = gridKey;
        prevGridStats = {};
        filtered.forEach(r => {
          prevGridStats[`${r.kind}:${r.name}`] = {
            total: r.total, cur_hour: r.cur_hour, avg_hr: r.avg_hr,
            is_live: r.is_live, recent_5m: r.recent_5m,
            pct: Math.round((r.total / maxTot) * 100)
          };
        });
        return;
      }

      const cards = grid.children;
      filtered.forEach((r, idx) => {
        const mkey = `${r.kind}:${r.name}`;
        const prev = prevGridStats[mkey] || {};
        const cardEl = cards[idx];
        if (!cardEl) return;
        const pct = Math.round((r.total / maxTot) * 100);
        if (r.total !== prev.total) {
          const totEl = cardEl.querySelector('.m-tot');
          if (totEl) renderOdometer(totEl, r.total);
          if (prev.total !== undefined && r.total > prev.total) {
            cardEl.classList.remove('cell-flash');
            void cardEl.offsetWidth;
            cardEl.classList.add('cell-flash');
            spawnGamePop(cardEl, '76%', '10px', r.total - prev.total, r.kind.toLowerCase());
          }
        }
        if (r.cur_hour !== prev.cur_hour) {
          const curEl = cardEl.querySelector('.m-cur');
          if (curEl) curEl.textContent = fmt(r.cur_hour);
        }
        if (r.avg_hr !== prev.avg_hr) {
          const avgEl = cardEl.querySelector('.m-avg');
          if (avgEl) avgEl.textContent = `${fmt(r.avg_hr)}/ชม.`;
        }
        if (r.is_live !== prev.is_live || r.recent_5m !== prev.recent_5m) {
          cardEl.classList.toggle('live', !!r.is_live);
          const wrap = cardEl.querySelector('.m-badge-wrap');
          if (wrap) {
            wrap.innerHTML = r.is_live
              ? `<span class="badge badge-live">● กำลังทำงาน (+${fmt(r.recent_5m)})</span>`
              : `<span class="badge" style="background:var(--border);color:var(--muted);">○ พัก/รอ</span>`;
          }
        }
        if (pct !== prev.pct) {
          const fill = cardEl.querySelector('.prog-fill');
          if (fill) fill.style.width = `${pct}%`;
        }
        prevGridStats[mkey] = {
          total: r.total, cur_hour: r.cur_hour, avg_hr: r.avg_hr,
          is_live: r.is_live, recent_5m: r.recent_5m, pct
        };
      });
    }

    function rygColor(val, vmax) {
      if (!val || val <= 0) return '#F8696B';
      if (vmax <= 0) return '#63BE7B';
      const t = Math.max(0, Math.min(1, val / vmax));
      let r, g, b;
      if (t <= 0.5) {
        const p = t * 2;
        r = Math.round(248 + (255 - 248) * p);
        g = Math.round(105 + (235 - 105) * p);
        b = Math.round(107 + (132 - 107) * p);
      } else {
        const p = (t - 0.5) * 2;
        r = Math.round(255 + (99 - 255) * p);
        g = Math.round(235 + (190 - 235) * p);
        b = Math.round(132 + (123 - 132) * p);
      }
      return `rgb(${r},${g},${b})`;
    }

    let prevTableKey = '';
    let prevTableVals = {};

    function renderTable(d) {
      const rows = tableTab === 'dws' ? d.dws_machines : d.wcs_machines;
      const wrap = document.getElementById('tableWrap');
      if (!rows.length) {
        prevTableKey = '';
        prevTableVals = {};
        wrap.innerHTML = '<div style="color:var(--muted);padding:16px;">ไม่มีข้อมูลเครื่องในหมวดนี้</div>';
        return;
      }

      let activeHourIdxs = d.hour_labels
        .map((_, i) => i)
        .filter(i => (Number(d.tot_hourly[i] || 0) > 0) || d.hour_labels[i] === d.cur_slot);
      if (!activeHourIdxs.length && d.hour_labels.length) {
        activeHourIdxs = [d.hour_labels.length - 1];
      }

      const allVals = [];
      rows.forEach(r => activeHourIdxs.forEach(i => {
        const v = Number(r.hourly[i] || 0);
        if (v > 0) allVals.push(v);
      }));
      const vmax = allVals.length ? Math.max(...allVals) : 1;

      const newVals = {};
      const cellMeta = {};
      rows.forEach(r => {
        activeHourIdxs.forEach(i => {
          const v = Number(r.hourly[i] || 0);
          const ck = `${r.name}:h${i}`;
          newVals[ck] = v;
          cellMeta[ck] = { bg: rygColor(v, vmax), pop: true };
        });
        newVals[`${r.name}:a`] = Number(r.shift_a || 0);
        newVals[`${r.name}:b`] = Number(r.shift_b || 0);
        newVals[`${r.name}:tot`] = Number(r.total || 0);
        newVals[`${r.name}:avg`] = Number(r.avg_hr || 0);
        newVals[`${r.name}:peak`] = Number(r.peak_hr || 0);
      });

      const colTotals = activeHourIdxs.map(i => rows.reduce((s, r) => s + Number(r.hourly[i] || 0), 0));
      let totA = 0, totB = 0, totAll = 0, maxHrAll = 0;
      const activeAvgs = [];
      rows.forEach(r => {
        totA += Number(r.shift_a || 0);
        totB += Number(r.shift_b || 0);
        totAll += Number(r.total || 0);
        if (r.avg_hr > 0) activeAvgs.push(r.avg_hr);
        if (r.peak_hr > maxHrAll) maxHrAll = r.peak_hr;
      });
      const nMach = Math.max(1, rows.length);
      const overallAvg = activeAvgs.length ? Math.round(activeAvgs.reduce((a, b) => a + b, 0) / activeAvgs.length) : 0;

      activeHourIdxs.forEach((i, pos) => {
        newVals[`SUM:h${i}`] = colTotals[pos];
        newVals[`AVG:h${i}`] = Math.round(colTotals[pos] / nMach);
      });
      newVals['SUM:a'] = totA;
      newVals['SUM:b'] = totB;
      newVals['SUM:tot'] = totAll;
      cellMeta['SUM:tot'] = { pop: true };
      newVals['SUM:avg'] = overallAvg;
      newVals['SUM:peak'] = maxHrAll;
      newVals['AVG:a'] = Math.round(totA / nMach);
      newVals['AVG:b'] = Math.round(totB / nMach);
      newVals['AVG:tot'] = Math.round(totAll / nMach);

      const tableKey = tableTab + '|' + activeHourIdxs.map(i => d.hour_labels[i]).join(',') + '|' + rows.map(r => r.name).join(',');
      if (tableKey !== prevTableKey) {
        const thHours = activeHourIdxs.map(i => `<th style="background:#38761D;color:#FFFFFF;border-color:#1E293B;">${d.hour_labels[i]}</th>`).join('');
        const bodyRows = rows.map(r => {
          const tds = activeHourIdxs.map(i => {
            const v = Number(r.hourly[i] || 0);
            const bg = rygColor(v, vmax);
            return `<td data-cell="${escHtml(r.name)}:h${i}" style="background:${bg};color:#111827;border-color:#334155;font-weight:500;">${fmt(v)}</td>`;
          }).join('');
          return `<tr>
            <td style="background:#FFFFFF;color:#111827;border-color:#334155;font-weight:700;">${escHtml(shortName(r.name))}</td>
            ${tds}
            <td data-cell="${escHtml(r.name)}:a" style="background:#E2EFDA;color:#111827;border-color:#334155;">${fmt(r.shift_a)}</td>
            <td data-cell="${escHtml(r.name)}:b" style="background:#E2EFDA;color:#111827;border-color:#334155;">${fmt(r.shift_b)}</td>
            <td data-cell="${escHtml(r.name)}:tot" style="background:#E2EFDA;color:#111827;border-color:#334155;font-weight:800;">${fmt(r.total)}</td>
            <td data-cell="${escHtml(r.name)}:avg" style="background:#FFF2CC;color:#111827;border-color:#334155;">${fmt(r.avg_hr)}</td>
            <td data-cell="${escHtml(r.name)}:peak" style="background:#FFF2CC;color:#111827;border-color:#334155;">${fmt(r.peak_hr)}</td>
          </tr>`;
        }).join('');

        const sumTds = activeHourIdxs.map((i, pos) => `<td data-cell="SUM:h${i}" style="background:#FFE699;color:#111827;border-color:#334155;font-weight:700;">${fmt(colTotals[pos])}</td>`).join('');
        const avgTds = activeHourIdxs.map((i, pos) => `<td data-cell="AVG:h${i}" style="background:#FFF2CC;color:#111827;border-color:#334155;font-weight:700;">${fmt(Math.round(colTotals[pos] / nMach))}</td>`).join('');

        const footerRows = `
          <tr>
            <td style="background:#FFE699;color:#111827;border-color:#334155;font-weight:800;">รวมทั้งหมด</td>
            ${sumTds}
            <td data-cell="SUM:a" style="background:#FFE699;color:#111827;border-color:#334155;font-weight:800;">${fmt(totA)}</td>
            <td data-cell="SUM:b" style="background:#FFE699;color:#111827;border-color:#334155;font-weight:800;">${fmt(totB)}</td>
            <td data-cell="SUM:tot" style="background:#FFE699;color:#111827;border-color:#334155;font-weight:800;">${fmt(totAll)}</td>
            <td data-cell="SUM:avg" style="background:#FFE699;color:#111827;border-color:#334155;font-weight:800;">${fmt(overallAvg)}</td>
            <td data-cell="SUM:peak" style="background:#FFE699;color:#111827;border-color:#334155;font-weight:800;">${fmt(maxHrAll)}</td>
          </tr>
          <tr>
            <td style="background:#FFF2CC;color:#111827;border-color:#334155;font-weight:700;">เฉลี่ย/เครื่อง</td>
            ${avgTds}
            <td data-cell="AVG:a" style="background:#FFF2CC;color:#111827;border-color:#334155;font-weight:700;">${fmt(Math.round(totA / nMach))}</td>
            <td data-cell="AVG:b" style="background:#FFF2CC;color:#111827;border-color:#334155;font-weight:700;">${fmt(Math.round(totB / nMach))}</td>
            <td data-cell="AVG:tot" style="background:#FFF2CC;color:#111827;border-color:#334155;font-weight:700;">${fmt(Math.round(totAll / nMach))}</td>
            <td style="background:#FFF2CC;color:#111827;border-color:#334155;">-</td>
            <td style="background:#FFF2CC;color:#111827;border-color:#334155;">-</td>
          </tr>
        `;

        wrap.innerHTML = `
          <table style="border-color:#334155;">
            <thead>
              <tr>
                <th style="background:#38761D;color:#FFFFFF;border-color:#1E293B;z-index:3;">เครื่อง</th>
                ${thHours}
                <th style="background:#1F4E78;color:#FFFFFF;border-color:#1E293B;">กะ A</th>
                <th style="background:#1F4E78;color:#FFFFFF;border-color:#1E293B;">กะ B</th>
                <th style="background:#1F4E78;color:#FFFFFF;border-color:#1E293B;">รวม</th>
                <th style="background:#1F4E78;color:#FFFFFF;border-color:#1E293B;">Avg/Hr</th>
                <th style="background:#1F4E78;color:#FFFFFF;border-color:#1E293B;">Max/Hr</th>
              </tr>
            </thead>
            <tbody>${bodyRows}${footerRows}</tbody>
          </table>
        `;
        prevTableKey = tableKey;
        prevTableVals = newVals;
        return;
      }

      const tdMap = {};
      wrap.querySelectorAll('td[data-cell]').forEach(el => {
        tdMap[el.getAttribute('data-cell')] = el;
      });
      const variant = tableTab === 'wcs' ? 'wcs' : 'dws';
      Object.keys(newVals).forEach(ck => {
        const val = newVals[ck];
        const prev = prevTableVals[ck];
        const td = tdMap[ck];
        if (!td) return;
        const meta = cellMeta[ck];
        if (meta && meta.bg) {
          td.style.background = meta.bg;
        }
        if (prev !== undefined && val !== prev) {
          td.textContent = fmt(val);
          if (val > prev) {
            td.classList.remove('cell-flash');
            void td.offsetWidth;
            td.classList.add('cell-flash');
            if (meta && meta.pop) {
              const px = (td.offsetLeft + td.offsetWidth / 2) + 'px';
              const py = Math.max(8, td.offsetTop - 2) + 'px';
              spawnGamePop(wrap, px, py, val - prev, variant);
            }
          }
        }
      });
      prevTableVals = newVals;
    }

    function escHtml(s) {
      return String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    function renderViewers(info) {
      if (!info) return;
      const badge = document.getElementById('viewerCountBadge');
      if (badge) badge.textContent = `กำลังดู ${info.active_count || 0} คน (ทั้งหมด ${info.total_sessions || 0} เซสชัน)`;
      const wrap = document.getElementById('viewerWrap');
      if (!wrap) return;
      const rows = (info.viewers || []).map(v => {
        const status = v.online
          ? '<span class="badge badge-live">● ออนไลน์</span>'
          : '<span class="badge" style="background:var(--border);color:var(--muted);">ออฟไลน์</span>';
        return `<tr>
          <td>${escHtml(v.name)}</td>
          <td>${status}</td>
          <td>${escHtml(v.device)}</td>
          <td>${escHtml(v.ip)}</td>
          <td>${escHtml(v.login_at)}</td>
          <td>${escHtml(v.last_seen)}</td>
        </tr>`;
      }).join('') || '<tr><td colspan="6" style="text-align:center;color:var(--muted);">ยังไม่มีประวัติการเข้าดู</td></tr>';
      wrap.innerHTML = `
        <table>
          <thead>
            <tr>
              <th>ชื่อผู้เข้าดู</th>
              <th>สถานะ</th>
              <th>อุปกรณ์</th>
              <th>IP Address</th>
              <th>เข้าใช้งาน</th>
              <th>อัปเดตล่าสุด</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    }

    fetchLive();
    setInterval(() => {
      fetchLive();
    }, 3000);
  </script>
</body>
</html>
"""


LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="th">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>J&T Express KKN คลัง 2 — ยืนยันตัวตน</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, sans-serif; }
    body {
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 18px;
      background: linear-gradient(135deg, #B71C1C 0%, #D32F2F 50%, #F57C00 100%);
    }
    .card {
      width: 100%;
      max-width: 390px;
      background: #FFFFFF;
      border-radius: 18px;
      padding: 26px 22px;
      box-shadow: 0 18px 45px rgba(0, 0, 0, 0.28);
    }
    .badge {
      display: inline-block;
      background: #D32F2F;
      color: #FFFFFF;
      font-weight: 800;
      font-size: 12px;
      padding: 5px 11px;
      border-radius: 8px;
      letter-spacing: 0.4px;
      margin-bottom: 12px;
    }
    h1 { font-size: 20px; color: #0F172A; margin-bottom: 6px; }
    p { font-size: 13px; color: #64748B; margin-bottom: 18px; line-height: 1.45; }
    label { display: block; font-size: 12.5px; font-weight: 700; color: #334155; margin-bottom: 6px; }
    input, select {
      width: 100%;
      padding: 12px 14px;
      font-size: 16px;
      border: 1.5px solid #CBD5E1;
      border-radius: 11px;
      outline: none;
      margin-bottom: 14px;
      background: #FFFFFF;
      color: #0F172A;
    }
    input:focus, select:focus { border-color: #D32F2F; }
    button {
      width: 100%;
      padding: 12px;
      border: none;
      border-radius: 11px;
      background: linear-gradient(135deg, #D32F2F, #E65100);
      color: #FFFFFF;
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
    }
    button:disabled { opacity: 0.6; cursor: wait; }
    .err {
      display: none;
      margin-top: 12px;
      padding: 10px 12px;
      border-radius: 10px;
      background: #FEE2E2;
      color: #991B1B;
      font-size: 12.5px;
      font-weight: 600;
    }
    .foot {
      margin-top: 16px;
      font-size: 11px;
      color: #94A3B8;
      text-align: center;
    }
  </style>
</head>
<body>
  <form class="card" id="loginForm">
    <div class="badge">J&T EXPRESS • KKN คลัง 2</div>
    <h1>KKN คลัง 2 Live Dashboard</h1>
    <p>กรุณาระบุชื่อและรหัสผ่านสำหรับดูยอดคัดแยกแบบเรียลไทม์ (บันทึกประวัติผู้เข้าชมเพื่อความปลอดภัย)</p>
    <label for="viewerRole">ผู้เข้าดู</label>
    <select id="viewerRole" required>
      <option value="" disabled selected>-- เลือกผู้เข้าดู --</option>
      <option value="หัวหน้าคลัง / Manager">หัวหน้าคลัง / Manager</option>
      <option value="Supervisor กะ A">Supervisor กะ A</option>
      <option value="Supervisor กะ B">Supervisor กะ B</option>
      <option value="CoGY / WCS Operator">CoGY / WCS Operator</option>
      <option value="IT Support">IT Support</option>
      <option value="ผู้บริหาร / Admin">ผู้บริหาร / Admin</option>
      <option value="__custom__">อื่นๆ (กรอกชื่อเอง)</option>
    </select>
    <input id="viewerCustom" type="text" maxlength="36" placeholder="กรอกชื่อผู้เข้าดู" style="display:none;" />
    <label for="passcode">รหัสผ่านเข้าดูข้อมูล</label>
    <input id="passcode" type="password" autocomplete="current-password" placeholder="กรอกรหัสผ่าน" required />
    <button type="submit" id="submitBtn">เข้าสู่ระบบ Dashboard</button>
    <div class="err" id="errBox"></div>
    <div class="foot">เข้ารหัส HTTPS • ตรวจจับการโจมตีและปิดระบบอัตโนมัติ</div>
  </form>
  <script>
    const roleEl = document.getElementById('viewerRole');
    const customEl = document.getElementById('viewerCustom');
    function syncCustomField() {
      const isCustom = roleEl.value === '__custom__';
      customEl.style.display = isCustom ? 'block' : 'none';
      customEl.required = isCustom;
    }
    roleEl.addEventListener('change', syncCustomField);
    const savedRole = localStorage.getItem('cogy_viewer_role') || '';
    const savedCustom = localStorage.getItem('cogy_viewer_custom') || '';
    if (savedRole) {
      roleEl.value = savedRole;
      if (savedRole === '__custom__' && savedCustom) customEl.value = savedCustom;
      syncCustomField();
    }
    document.getElementById('loginForm').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const btn = document.getElementById('submitBtn');
      const err = document.getElementById('errBox');
      const roleVal = roleEl.value;
      const viewer_name = (roleVal === '__custom__' ? customEl.value : roleVal).trim();
      if (!viewer_name) {
        err.textContent = 'กรุณาเลือกหรือกรอกชื่อผู้เข้าดู';
        err.style.display = 'block';
        return;
      }
      localStorage.setItem('cogy_viewer_role', roleVal);
      if (roleVal === '__custom__') localStorage.setItem('cogy_viewer_custom', viewer_name);
      const passcode = document.getElementById('passcode').value;
      err.style.display = 'none';
      btn.disabled = true;
      try {
        const res = await fetch('/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ viewer_name, passcode })
        });
        const data = await res.json();
        if (res.ok && data.ok) {
          location.reload();
        } else {
          err.textContent = data.error || 'รหัสผ่านไม่ถูกต้อง';
          err.style.display = 'block';
        }
      } catch (e) {
        err.textContent = 'ไม่สามารถเชื่อมต่อเซิร์ฟเวอร์ได้';
        err.style.display = 'block';
      } finally {
        btn.disabled = false;
      }
    });
  </script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    def _get_client_ip(self) -> str:
        cf_ip = self.headers.get("CF-Connecting-IP")
        if cf_ip:
            return cf_ip.strip()
        xff = self.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()
        return self.client_address[0]

    def _is_https(self) -> bool:
        proto = (self.headers.get("X-Forwarded-Proto") or "").lower()
        return proto == "https"

    def _get_session(self) -> dict | None:
        cfg = load_config()
        if not cfg.get("dashboard_passcode"):
            return {"sid": "open", "name": "ผู้ใช้งาน"}
        raw_cookie = self.headers.get("Cookie", "")
        if not raw_cookie:
            return None
        c = SimpleCookie()
        try:
            c.load(raw_cookie)
        except Exception:
            return None
        morsel = c.get("cogy_session")
        return parse_session_token(morsel.value) if morsel else None

    def _guard_request(self) -> bool:
        if _LOCKDOWN:
            self._send_json(503, {"error": "ระบบปิดชั่วคราวเนื่องจากตรวจพบภัยคุกคาม"})
            return False
        ip = self._get_client_ip()
        ua = self.headers.get("User-Agent", "")
        threat = check_request_threat(ip, self.path, ua)
        if threat:
            trigger_cyber_lockdown(threat, ip, self.path, ua)
            self._send_json(403, {"error": "ตรวจพบคำขอผิดปกติ ระบบปิดการเชื่อมต่อทันที"})
            return False
        return True

    def _send_security_headers(self, no_store: bool = False):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if no_store:
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")

    def _send_json(self, status: int, obj: dict, extra_headers: list[tuple[str, str]] | None = None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_security_headers(no_store=True)
        if extra_headers:
            for k, v in extra_headers:
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._guard_request():
            return
        parsed = urlparse(self.path)
        session = self._get_session()
        ip = self._get_client_ip()
        ua = self.headers.get("User-Agent", "")
        if parsed.path == "/":
            if session:
                record_viewer_activity(session["sid"], session["name"], ip, ua)
            html = DASHBOARD_HTML if session else LOGIN_HTML
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._send_security_headers(no_store=not bool(session))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/live":
            if not session:
                self._send_json(401, {"error": "unauthorized"})
                return
            record_viewer_activity(session["sid"], session["name"], ip, ua)
            qs = parse_qs(parsed.query)
            s = qs.get("start", [None])[0]
            e = qs.get("end", [None])[0]
            try:
                payload = dict(build_live_payload(s, e))
                payload["security_viewers"] = get_viewers_snapshot()
                self._send_json(200, payload)
            except Exception:
                self._send_json(500, {"error": "ไม่สามารถดึงข้อมูลจากระบบได้ชั่วคราว"})
        else:
            self.send_response(404)
            self._send_security_headers()
            self.end_headers()

    def do_POST(self):
        if not self._guard_request():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            ip = self._get_client_ip()
            ua = self.headers.get("User-Agent", "")
            wait_sec = is_rate_limited(ip)
            if wait_sec > 0:
                self._send_json(429, {"ok": False, "error": f"กรอกรหัสผิดหลายครั้ง กรุณารอ {wait_sec} วินาที"})
                return
            try:
                length = min(4096, int(self.headers.get("Content-Length", "0")))
                raw = self.rfile.read(length).decode("utf-8")
                data = json.loads(raw) if raw else {}
            except Exception:
                self._send_json(400, {"ok": False, "error": "รูปแบบคำขอไม่ถูกต้อง"})
                return
            cfg = load_config()
            expected = str(cfg.get("dashboard_passcode", ""))
            provided = str(data.get("passcode", ""))
            viewer_name = str(data.get("viewer_name", "")).strip() or "ผู้ใช้งาน"
            if expected and hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
                clear_login_failures(ip)
                token, sid = make_session_token(viewer_name)
                record_viewer_activity(sid, viewer_name, ip, ua, is_login=True)
                cookie_val = f"cogy_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL_SECONDS}"
                if self._is_https():
                    cookie_val += "; Secure"
                self._send_json(200, {"ok": True}, extra_headers=[("Set-Cookie", cookie_val)])
            else:
                left = record_login_failure(ip, ua=ua)
                msg = f"รหัสผ่านไม่ถูกต้อง (เหลือโอกาสอีก {left} ครั้ง)" if left > 0 else "ตรวจพบการเดารหัสผ่าน ระบบปิด Live Dashboard และแจ้งเตือนแล้ว"
                self._send_json(401, {"ok": False, "error": msg})
        elif parsed.path == "/logout":
            cookie_val = "cogy_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
            if self._is_https():
                cookie_val += "; Secure"
            self._send_json(200, {"ok": True}, extra_headers=[("Set-Cookie", cookie_val)])
        else:
            self.send_response(404)
            self._send_security_headers()
            self.end_headers()

    def log_message(self, format, *args):
        pass


def find_cloudflared() -> str | None:
    found = shutil.which("cloudflared")
    if found:
        return found
    for p in (
        r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
        r"C:\Program Files\cloudflared\cloudflared.exe",
    ):
        if Path(p).exists():
            return p
    return None


def start_cloudflare_tunnel(port: int) -> None:
    global _TUNNEL_PROC, _PUBLIC_URL
    exe = find_cloudflared()
    if not exe:
        print(" [WARN] ไม่พบ cloudflared.exe — เปิดใช้งานได้เฉพาะวง LAN")
        return
    proc = subprocess.Popen(
        [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    _TUNNEL_PROC = proc

    def _cleanup():
        if _TUNNEL_PROC and _TUNNEL_PROC.poll() is None:
            _TUNNEL_PROC.terminate()

    atexit.register(_cleanup)

    def _reader():
        global _PUBLIC_URL
        pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
        for line in proc.stdout:
            m = pattern.search(line)
            if m and not _PUBLIC_URL:
                _PUBLIC_URL = m.group(0)
                update_config_field("dashboard_url", _PUBLIC_URL)
                print(f" ลิงก์ออนไลน์ HTTPS : {_PUBLIC_URL}")
                print(f" (อัปเดตลง config.json สำหรับปุ่ม Feishu อัตโนมัติแล้ว)")
                print(f"====================================================")

    t = threading.Thread(target=_reader, daemon=True)
    t.start()


LIVE_CARD_STATE_FILE = BASE_DIR / "cache" / "feishu_live_card.json"


def _delta_tag(val: int) -> str:
    return f" <font color='green'>**(+{val:,})**</font>" if val and val > 0 else ""


def _short_machine_name(name: str) -> str:
    s = str(name or "")
    for prefix, label in (("DWS_KKN_", "DWS"), ("Autopacking_KKN_", "Auto"), ("LWS_KKN_", "LWS")):
        if s.upper().startswith(prefix.upper()):
            num = s[len(prefix):].split("_")[0]
            return f"{label} {num[-2:].zfill(2)}"
    return s.replace("_KKN_", " ").replace("_002", "").replace("_", " ")


_CARD_I18N = {
    "th_th": {
        "title": "LIVE KKN คลัง 2 • อัปเดตเรียลไทม์",
        "sub": "อัปเดตล่าสุด {ts}  |  รหัสเว็บ: {pw}",
        "btn": "เปิด Live Dashboard (ดูกราฟสด)",
        "active": "ทำงาน",
        "pcs": "ชิ้น",
        "total": "รวมทั้งหมด",
        "hour": "ชม.",
        "now_hdr": "ยอดรายเครื่อง ชม. {slot} ({cnt} เครื่อง):",
        "this_hr": "ชม.นี้",
        "sum": "รวม",
        "idle": "ยังไม่มีเครื่องคัดแยกในชั่วโมงนี้",
        "alt": "ตารางสรุปยอด WCS และ DWS แบบเรียลไทม์",
    },
    "zh_cn": {
        "title": "LIVE KKN 2号仓 • 实时更新",
        "sub": "最后更新 {ts}  |  网页密码: {pw}",
        "btn": "打开实时看板 (查看动态图表)",
        "active": "运行中",
        "pcs": "件",
        "total": "总计",
        "hour": "时段",
        "now_hdr": "{slot} 时段各设备 ({cnt} 台):",
        "this_hr": "本小时",
        "sum": "累计",
        "idle": "本小时暂无分拣数据",
        "alt": "WCS 与 DWS 实时汇总表",
    },
    "en_us": {
        "title": "LIVE KKN WH2 • Real-Time",
        "sub": "Updated {ts}  |  Passcode: {pw}",
        "btn": "Open Live Dashboard",
        "active": "active",
        "pcs": "pcs",
        "total": "Total",
        "hour": "Hour",
        "now_hdr": "Machines in {slot} ({cnt}):",
        "this_hr": "this hr",
        "sum": "total",
        "idle": "No sorting volume in this hour yet",
        "alt": "Real-Time WCS & DWS Summary Table",
    },
}


def build_live_feishu_card(p: dict, dashboard_url: str, table_img_key: str = "", deltas: dict | None = None) -> dict:
    deltas = deltas or {}
    m_deltas = deltas.get("machines", {})
    cur_idx = p["hour_labels"].index(p["cur_slot"]) if p.get("cur_slot") in p.get("hour_labels", []) else -1
    cur_val = p["tot_hourly"][cur_idx] if cur_idx >= 0 else 0
    passcode = load_config().get("dashboard_passcode", "")

    wcs_hour = sorted([m for m in p.get("wcs_machines", []) if m.get("cur_hour", 0) > 0], key=lambda x: x.get("cur_hour", 0), reverse=True)
    dws_hour = sorted([m for m in p.get("dws_machines", []) if m.get("cur_hour", 0) > 0], key=lambda x: x.get("cur_hour", 0), reverse=True)
    hour_list = wcs_hour + dws_hour

    def _fmt_machine_line(m: dict, lang_t: dict) -> str:
        dot = "🟢" if m.get("is_live") else "⚪"
        d_txt = _delta_tag(m_deltas.get(m["name"], 0))
        return f"{dot} **{_short_machine_name(m['name'])}** : **{m['cur_hour']:,}**{d_txt} *({lang_t['sum']} {m['total']:,})*"

    def _build_lang_elements(lang: str) -> list[dict]:
        t = _CARD_I18N[lang]
        hdr_line = t["now_hdr"].format(slot=p["cur_slot"], cnt=len(hour_list))

        elems = []
        if dashboard_url:
            elems.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": t["btn"]},
                        "type": "primary",
                        "url": dashboard_url,
                    }
                ],
            })

        elems.extend([
            {
                "tag": "column_set",
                "flex_mode": "bisect",
                "background_style": "grey",
                "horizontal_spacing": "8px",
                "columns": [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [
                            {
                                "tag": "div",
                                "text": {
                                    "tag": "lark_md",
                                    "content": (
                                        f"**WCS** ({p['wcs_live_count']}/{len(p['wcs_machines'])} {t['active']})\n"
                                        f"<font color='red'>**{p['wcs_total']:,}**</font> {t['pcs']}{_delta_tag(deltas.get('wcs_total', 0))}\n"
                                        f"<font color='grey'>A {p['wcs_shift_a']:,} · B {p['wcs_shift_b']:,}</font>"
                                    ),
                                },
                            }
                        ],
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [
                            {
                                "tag": "div",
                                "text": {
                                    "tag": "lark_md",
                                    "content": (
                                        f"**DWS** ({p['dws_live_count']}/{len(p['dws_machines'])} {t['active']})\n"
                                        f"<font color='orange'>**{p['dws_total']:,}**</font> {t['pcs']}{_delta_tag(deltas.get('dws_total', 0))}\n"
                                        f"<font color='grey'>A {p['dws_shift_a']:,} · B {p['dws_shift_b']:,}</font>"
                                    ),
                                },
                            }
                        ],
                    },
                ],
            },
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**{t['total']}:** **{p['grand_total']:,}** {t['pcs']}{_delta_tag(deltas.get('grand_total', 0))}  |  "
                        f"**{t['hour']} {p['cur_slot']}:** **{cur_val:,}** {t['pcs']}{_delta_tag(deltas.get('cur_val', 0))}"
                        + (f"\n{t['idle']}" if not hour_list else "")
                    ),
                },
            },
        ])

        if hour_list:
            left_body = "\n".join(_fmt_machine_line(m, t) for m in wcs_hour) if wcs_hour else "<font color='grey'>-</font>"
            right_body = "\n".join(_fmt_machine_line(m, t) for m in dws_hour) if dws_hour else "<font color='grey'>-</font>"
            left_lines = f"**WCS / Auto ({len(wcs_hour)})**\n{left_body}"
            right_lines = f"**DWS ({len(dws_hour)})**\n{right_body}"
            elems.append({
                "tag": "collapsible_panel",
                "expanded": False,
                "background_color": "grey",
                "header": {
                    "title": {
                        "tag": "markdown",
                        "content": f"**{hdr_line} — คลิกเพื่อกางดูรายเครื่อง ▾**",
                    },
                    "vertical_align": "center",
                    "padding": "6px 0px 6px 8px",
                },
                "border": {"color": "grey", "corner_radius": "5px"},
                "vertical_spacing": "6px",
                "elements": [
                    {
                        "tag": "column_set",
                        "flex_mode": "bisect",
                        "horizontal_spacing": "8px",
                        "columns": [
                            {
                                "tag": "column",
                                "width": "weighted",
                                "weight": 1,
                                "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": left_lines}}],
                            },
                            {
                                "tag": "column",
                                "width": "weighted",
                                "weight": 1,
                                "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": right_lines}}],
                            },
                        ],
                    }
                ],
            })

        if table_img_key:
            elems.append({
                "tag": "img",
                "img_key": table_img_key,
                "alt": {"tag": "plain_text", "content": t["alt"]},
            })
        return elems

    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": "red",
            "title": {
                "tag": "plain_text",
                "i18n": {k: v["title"] for k, v in _CARD_I18N.items()},
            },
            "subtitle": {
                "tag": "plain_text",
                "i18n": {
                    k: v["sub"].format(ts=p["updated_at"], pw=passcode)
                    for k, v in _CARD_I18N.items()
                },
            },
        },
        "i18n_elements": {
            lang: _build_lang_elements(lang)
            for lang in _CARD_I18N
        },
    }


def get_target_feishu_chat_id(cfg: dict) -> tuple[str, bool]:
    """คืน (chat_id, is_test_mode) โดยแยกห้องทดสอบออกจากห้องจริง"""
    is_test = bool(cfg.get("feishu_test_mode", False))
    test_id = str(cfg.get("feishu_test_chat_id", "") or "").strip()
    if is_test and test_id:
        return test_id, True
    return str(cfg.get("feishu_chat_id", "") or "").strip(), False


def get_target_feishu_chat_ids(cfg: dict) -> tuple[list[str], bool]:
    """คืน (รายการ chat_id ทั้งหมดที่เปิดส่ง, is_test_mode)"""
    primary, is_test = get_target_feishu_chat_id(cfg)
    if is_test:
        return ([primary] if primary else []), True
    rooms = cfg.get("feishu_rooms")
    if isinstance(rooms, list) and len(rooms) > 0:
        out: list[str] = []
        for r in rooms:
            if isinstance(r, dict) and r.get("enabled", True):
                cid = str(r.get("chat_id", "") or "").strip()
                if cid and cid not in out:
                    out.append(cid)
        return out, False
    out = []
    if primary:
        out.append(primary)
    for extra in (cfg.get("feishu_extra_chat_ids") or []):
        cid = str(extra or "").strip()
        if cid and cid not in out:
            out.append(cid)
    return out, False


def start_feishu_live_card_loop() -> None:
    def _loop():
        from feishu_dispatcher import FeishuDispatcher
        last_sig = ""
        prev_metrics = None
        cached_img_key = ""
        last_img_ts = 0.0
        last_img_slot = ""
        last_img_total = -1
        last_excel_ts = 0.0
        last_excel_total = -1
        time.sleep(4)
        while not _LOCKDOWN:
            try:
                cfg = load_config()
                if not cfg.get("enable_feishu_live_card", True):
                    time.sleep(10)
                    continue
                if load_job_state().get("state") != "ACTIVE":
                    time.sleep(15)
                    continue
                target_chat_ids, is_test = get_target_feishu_chat_ids(cfg)
                if not cfg.get("feishu_app_id") or not target_chat_ids:
                    time.sleep(15)
                    continue
                payload = build_live_payload()
                dash_url = cfg.get("dashboard_url", "")
                sig = f"{is_test}|{','.join(target_chat_ids)}|{payload['grand_total']}|{payload['wcs_live_count']}|{payload['dws_live_count']}|{payload['cur_slot']}|{dash_url}"
                state = {}
                if LIVE_CARD_STATE_FILE.exists():
                    try:
                        state = json.loads(LIVE_CARD_STATE_FILE.read_text(encoding="utf-8"))
                    except Exception:
                        state = {}
                cur_shift_tag = now_dt.strftime("%Y-%m-%d") if (now_dt := datetime.now()).hour >= int(cfg.get("shift_a_start", 12)) else (now_dt - timedelta(days=1)).strftime("%Y-%m-%d")
                # รอให้ Auto Job ส่งรายงานรายชั่วโมง + Excel เสร็จก่อน (~35 วินาทีแรกของชั่วโมง) แล้วค่อยส่ง Live Card ใบใหม่อยู่ล่างสุด
                allow_new_slot_card = (now_dt.minute > 0) or (now_dt.second >= 35)
                cur_card_tag = f"{cur_shift_tag}_{payload['cur_slot']}"
                cards_by_chat = state.setdefault("cards_by_chat", {})
                if not allow_new_slot_card:
                    first_entry = cards_by_chat.get(target_chat_ids[0], {}) if target_chat_ids else {}
                    if first_entry.get("shift_key") and first_entry.get("shift_key") != cur_card_tag:
                        cur_card_tag = first_entry["shift_key"]
                all_have_msg = all(
                    cards_by_chat.get(cid, {}).get("message_id") and cards_by_chat.get(cid, {}).get("shift_key") == cur_card_tag
                    for cid in target_chat_ids
                )
                if all_have_msg and sig == last_sig:
                    time.sleep(15)
                    continue

                cur_idx = payload["hour_labels"].index(payload["cur_slot"]) if payload.get("cur_slot") in payload.get("hour_labels", []) else -1
                cur_val = payload["tot_hourly"][cur_idx] if cur_idx >= 0 else 0
                cur_machines = {
                    m["name"]: m.get("cur_hour", 0)
                    for m in (payload.get("wcs_machines", []) + payload.get("dws_machines", []))
                }
                deltas = {}
                if prev_metrics:
                    prev_m = prev_metrics.get("machines", {})
                    deltas = {
                        "wcs_total": max(0, payload["wcs_total"] - prev_metrics.get("wcs_total", payload["wcs_total"])),
                        "dws_total": max(0, payload["dws_total"] - prev_metrics.get("dws_total", payload["dws_total"])),
                        "grand_total": max(0, payload["grand_total"] - prev_metrics.get("grand_total", payload["grand_total"])),
                        "cur_val": max(0, cur_val - prev_metrics.get("cur_val", cur_val)),
                        "machines": {
                            name: max(0, val - prev_m.get(name, val))
                            for name, val in cur_machines.items()
                        },
                    }
                prev_metrics = {
                    "wcs_total": payload["wcs_total"],
                    "dws_total": payload["dws_total"],
                    "grand_total": payload["grand_total"],
                    "cur_val": cur_val,
                    "machines": cur_machines,
                }

                from .report_builder import build_excel_report, build_table_image
                bot = FeishuDispatcher(
                    app_id=cfg["feishu_app_id"],
                    app_secret=cfg["feishu_app_secret"],
                    chat_id=target_chat_ids[0],
                )
                now_ts = time.time()
                need_img_upload = (
                    (not cached_img_key)
                    or (payload["cur_slot"] != last_img_slot)
                    or (payload["grand_total"] != last_img_total)
                )
                with _LIVE_LOCK:
                    raw_data = _LIVE_CACHE.get("data")
                    raw_summary = _LIVE_CACHE.get("summary")
                if need_img_upload and raw_data and raw_summary:
                    live_tbl_path = build_table_image(raw_data, raw_summary)
                    if live_tbl_path and live_tbl_path.exists():
                        uploaded_key = bot.upload_image(live_tbl_path)
                        if uploaded_key:
                            cached_img_key = uploaded_key
                            last_img_ts = now_ts
                            last_img_slot = payload["cur_slot"]
                            last_img_total = payload["grand_total"]
                        try:
                            live_tbl_path.unlink()
                        except Exception:
                            pass
                card = build_live_feishu_card(payload, dash_url, table_img_key=cached_img_key, deltas=deltas)
                if is_test and isinstance(card.get("header"), dict):
                    card["header"]["template"] = "orange"
                    t_obj = card["header"].get("title", {})
                    if isinstance(t_obj, dict):
                        if isinstance(t_obj.get("i18n"), dict):
                            t_obj["i18n"] = {lk: f"[TEST MODE] {lv}" for lk, lv in t_obj["i18n"].items()}
                        elif t_obj.get("content"):
                            t_obj["content"] = "[TEST MODE] " + t_obj["content"]

                LIVE_CARD_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                for cid in target_chat_ids:
                    bot.chat_id = cid
                    c_entry = cards_by_chat.setdefault(cid, {})
                    c_msg_id = c_entry.get("message_id", "") if c_entry.get("shift_key") == cur_card_tag else ""
                    if c_msg_id:
                        ok, _ = bot.update_card(c_msg_id, card)
                        if not ok:
                            c_msg_id = ""
                    if not c_msg_id:
                        c_msg_id = bot.send_message("interactive", card)
                        c_entry["message_id"] = c_msg_id
                        c_entry["shift_key"] = cur_card_tag
                LIVE_CARD_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
                last_sig = sig
            except Exception as ex:
                log_security_event(f"FEISHU_LIVE_CARD_ERR err={ex}")
            time.sleep(15)

    threading.Thread(target=_loop, daemon=True).start()


def _default_job_state() -> dict:
    return {
        "state": "STANDBY",
        "shift_key": "",
        "active_since_ts": 0.0,
        "last_activity_ts": 0.0,
        "last_grand_total": 0,
        "baseline_total": 0,
        "sent_hours": [],
        "last_checked_slot": "",
        "idle_minutes": 0,
        "last_cleanup_ts": 0.0,
        "pending_eod_summary": False,
        "pending_eod_idle_min": 0,
    }


def load_job_state() -> dict:
    with _JOB_LOCK:
        if _JOB_STATE:
            return dict(_JOB_STATE)
        st = _default_job_state()
        if JOB_STATE_FILE.exists():
            try:
                loaded = json.loads(JOB_STATE_FILE.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    st.update(loaded)
            except Exception:
                pass
        _JOB_STATE.clear()
        _JOB_STATE.update(st)
        return dict(_JOB_STATE)


def save_job_state(st: dict) -> None:
    with _JOB_LOCK:
        _JOB_STATE.clear()
        _JOB_STATE.update(st)
        try:
            JOB_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            JOB_STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


def get_job_status() -> dict:
    st = load_job_state()
    cfg = load_config()
    with _API_HEALTH_LOCK:
        api_ok = _API_HEALTH["ok"]
        first_fail = _API_HEALTH["first_fail_ts"]
        last_err = _API_HEALTH["last_error"]
    down_mins = int((time.time() - first_fail) // 60) if (not api_ok and first_fail > 0) else 0
    return {
        "state": st.get("state", "STANDBY"),
        "idle_minutes": int(st.get("idle_minutes", 0)),
        "idle_finish_minutes": int(cfg.get("idle_finish_minutes", 180)),
        "active_since": datetime.fromtimestamp(st["active_since_ts"]).strftime("%H:%M") if st.get("active_since_ts", 0) > 0 else "",
        "sent_hours_count": len(st.get("sent_hours", [])),
        "last_grand_total": int(st.get("last_grand_total", 0)),
        "api_ok": api_ok,
        "api_down_minutes": down_mins,
        "api_last_error": last_err,
    }


def evaluate_job_transition(
    job_state: dict,
    payload: dict,
    cfg: dict,
    now_ts: float,
    now_dt: datetime,
) -> tuple[dict, list[dict]]:
    """คำนวณสถานะงาน STANDBY <-> ACTIVE และคืนรายการ Action ที่ต้องทำ"""
    st = dict(_default_job_state())
    st.update(job_state or {})
    actions: list[dict] = []

    min_total_delta = int(cfg.get("start_min_total_delta", 50))
    min_machines = int(cfg.get("start_min_machines", 2))
    min_per_machine = int(cfg.get("start_min_per_machine", 30))
    idle_finish_min = int(cfg.get("idle_finish_minutes", 180))
    shift_a_start = int(cfg.get("shift_a_start", 12))

    shift_dt = now_dt.replace(hour=shift_a_start, minute=0, second=0, microsecond=0)
    if now_dt.hour < shift_a_start:
        shift_dt -= timedelta(days=1)
    shift_key = shift_dt.strftime("%Y-%m-%d_%H")

    grand_total = int(payload.get("grand_total", 0))
    cur_slot = payload.get("cur_slot", "")
    hour_labels = payload.get("hour_labels", [])
    tot_hourly = payload.get("tot_hourly", [])
    cur_idx = hour_labels.index(cur_slot) if cur_slot in hour_labels else -1
    cur_hour_total = int(tot_hourly[cur_idx]) if cur_idx >= 0 else 0

    all_machines = (payload.get("wcs_machines", []) or []) + (payload.get("dws_machines", []) or [])
    active_m_count = sum(1 for m in all_machines if int(m.get("cur_hour", 0)) >= min_per_machine)

    if st["state"] == "STANDBY" and st.get("shift_key") != shift_key:
        st["shift_key"] = shift_key
        st["baseline_total"] = 0
        st["last_grand_total"] = grand_total
        st["sent_hours"] = []
        st["idle_minutes"] = 0

    if st["state"] == "STANDBY":
        eod_hour = int(cfg.get("eod_summary_hour", 8))
        if st.get("pending_eod_summary") and eod_hour <= now_dt.hour < shift_a_start:
            idle_m = int(st.get("pending_eod_idle_min", idle_finish_min))
            st["pending_eod_summary"] = False
            st["pending_eod_idle_min"] = 0
            actions.append({
                "type": "FINISH_JOB",
                "idle_minutes": idle_m,
                "grand_total": grand_total,
                "scheduled_08": True,
            })
        if int(st.get("baseline_total", 0)) == 0 and cur_hour_total < min_total_delta and active_m_count < min_machines and grand_total > 0:
            st["baseline_total"] = grand_total
        baseline = int(st.get("baseline_total", 0))
        delta_from_base = max(0, grand_total - baseline)
        trigger_by_cur_hour = (cur_hour_total >= min_total_delta) and (baseline == 0 or delta_from_base > 0)
        trigger_by_delta = delta_from_base >= min_total_delta
        trigger_by_machines = (active_m_count >= min_machines) and (baseline == 0 or delta_from_base > 0)
        if trigger_by_cur_hour or trigger_by_delta or trigger_by_machines:
            st["state"] = "ACTIVE"
            st["shift_key"] = shift_key
            st["active_since_ts"] = now_ts
            st["last_activity_ts"] = now_ts
            st["last_grand_total"] = grand_total
            st["idle_minutes"] = 0
            actions.append({
                "type": "ENTER_ACTIVE",
                "cur_hour_total": cur_hour_total,
                "active_machines": active_m_count,
                "grand_total": grand_total,
            })
        else:
            st["last_grand_total"] = grand_total
    elif st["state"] == "ACTIVE":
        if grand_total > int(st.get("last_grand_total", 0)):
            st["last_grand_total"] = grand_total
            st["last_activity_ts"] = now_ts
            st["idle_minutes"] = 0
        else:
            last_act = float(st.get("last_activity_ts") or now_ts)
            st["idle_minutes"] = max(0, int((now_ts - last_act) // 60))

        prev_slot = st.get("last_checked_slot", "")
        sent_hours = list(st.get("sent_hours", []))
        if prev_slot and cur_slot and prev_slot != cur_slot and prev_slot not in sent_hours:
            prev_vol = 0
            if prev_slot in hour_labels:
                prev_vol = int(tot_hourly[hour_labels.index(prev_slot)])
            if prev_vol > 0:
                sent_hours.append(prev_slot)
                st["sent_hours"] = sent_hours
                actions.append({"type": "SEND_HOURLY", "slot": prev_slot, "volume": prev_vol})

        if st["idle_minutes"] >= idle_finish_min:
            eod_hour = int(cfg.get("eod_summary_hour", 8))
            idle_m = st["idle_minutes"]
            st["state"] = "STANDBY"
            st["baseline_total"] = grand_total
            st["last_grand_total"] = grand_total
            st["active_since_ts"] = 0.0
            st["last_activity_ts"] = 0.0
            st["idle_minutes"] = 0
            st["sent_hours"] = []
            if now_dt.hour < eod_hour:
                st["pending_eod_summary"] = True
                st["pending_eod_idle_min"] = idle_m
                actions.append({
                    "type": "QUEUE_EOD_SUMMARY",
                    "idle_minutes": idle_m,
                    "grand_total": grand_total,
                    "eod_hour": eod_hour,
                })
            else:
                st["pending_eod_summary"] = False
                st["pending_eod_idle_min"] = 0
                actions.append({
                    "type": "FINISH_JOB",
                    "idle_minutes": idle_m,
                    "grand_total": grand_total,
                    "scheduled_08": False,
                })

    if cur_slot:
        st["last_checked_slot"] = cur_slot
    return st, actions


def _cleanup_old_reports_and_memory(retention_days: int = 30) -> int:
    deleted = 0
    try:
        import matplotlib.pyplot as plt
        plt.close("all")
    except Exception:
        pass
    gc.collect()
    reports_dir = BASE_DIR / "reports"
    if reports_dir.exists() and retention_days > 0:
        cutoff = time.time() - (retention_days * 86400)
        for fp in reports_dir.iterdir():
            try:
                if fp.is_file() and fp.stat().st_mtime < cutoff:
                    fp.unlink()
                    deleted += 1
            except Exception:
                pass
    return deleted


def reset_job_to_standby(current_grand_total: int | None = None) -> dict:
    st = load_job_state()
    if current_grand_total is None:
        with _LIVE_LOCK:
            p = _LIVE_CACHE.get("payload") or {}
        current_grand_total = int(p.get("grand_total", st.get("last_grand_total", 0)))
    st["state"] = "STANDBY"
    st["baseline_total"] = int(current_grand_total)
    st["last_grand_total"] = int(current_grand_total)
    st["active_since_ts"] = 0.0
    st["last_activity_ts"] = 0.0
    st["idle_minutes"] = 0
    st["sent_hours"] = []
    save_job_state(st)
    log_app_event("JOB", "info", f"รีเซ็ตสถานะเป็น STANDBY ด้วยตนเอง (ยอดฐาน {current_grand_total:,} ชิ้น)")
    return st


def _dispatch_job_report(cfg: dict, raw_data: dict, raw_summary: dict, mode_label: str, auto_recall_prev: bool, drop_empty_hours: bool) -> None:
    from .feishu_dispatcher import FeishuDispatcher
    from .report_builder import build_excel_report, build_hourly_chart, build_table_image

    target_chat_ids, is_test = get_target_feishu_chat_ids(cfg)
    if not cfg.get("feishu_app_id") or not target_chat_ids:
        log_app_event("FEISHU", "warn", f"ข้ามการส่ง ({mode_label}) เนื่องจากยังไม่ได้ตั้งค่า Feishu App/Chat ID")
        return

    final_label = f"[TEST MODE] {mode_label}" if is_test else mode_label
    shift_a = int(cfg.get("shift_a_start", 12))
    shift_b = int(cfg.get("shift_b_start", 0))
    tbl_path = build_table_image(raw_data, raw_summary)
    excel_path = None
    if drop_empty_hours or cfg.get("send_excel_to_feishu", True):
        excel_path = build_excel_report(
            raw_data,
            raw_summary,
            shift_a_start=shift_a,
            shift_b_start=shift_b,
            drop_empty_hours=drop_empty_hours,
        )

    total_recalled = 0
    for cid in target_chat_ids:
        bot = FeishuDispatcher(
            app_id=cfg["feishu_app_id"],
            app_secret=cfg["feishu_app_secret"],
            chat_id=cid,
        )
        res = bot.send_hourly_report(
            branch_name=cfg.get("branch_name", "KKN"),
            data=raw_data,
            summary=raw_summary,
            chart_path=None,
            table_image_path=tbl_path,
            excel_path=excel_path,
            mode_label=final_label,
            auto_recall_prev=auto_recall_prev,
            dashboard_url=cfg.get("dashboard_url", ""),
        )
        total_recalled += len(res.get("recalled_prev") or [])
    extra = f" | Recall ข้อความก่อนหน้า {total_recalled} รายการ" if total_recalled else ""
    room_tag = "[ห้องทดสอบ] " if is_test else f"[{len(target_chat_ids)} ห้อง] "
    log_app_event("FEISHU", "ok", f"ส่งรายงาน Feishu {room_tag}สำเร็จ: {final_label}{extra}")


def start_auto_job_loop() -> None:
    def _loop():
        log_app_event("JOB", "info", "เปิดระบบ Auto Job Engine โหมด STANDBY พร้อมตรวจจับยอดคัดแยกอัตโนมัติ")
        time.sleep(6)
        while not _LOCKDOWN:
            try:
                cfg = load_config()
                payload = build_live_payload()
                now_ts = time.time()
                now_dt = datetime.now()
                cur_state = load_job_state()
                next_state, actions = evaluate_job_transition(cur_state, payload, cfg, now_ts, now_dt)

                if now_ts - float(next_state.get("last_cleanup_ts", 0.0)) >= 3600:
                    deleted = _cleanup_old_reports_and_memory(int(cfg.get("report_retention_days", 30)))
                    next_state["last_cleanup_ts"] = now_ts
                    if deleted > 0:
                        log_app_event("SYSTEM", "info", f"ล้างไฟล์รายงานเก่าเกิน {cfg.get('report_retention_days', 30)} วัน จำนวน {deleted} ไฟล์ และคืนหน่วยความจำแล้ว")

                save_job_state(next_state)

                with _LIVE_LOCK:
                    raw_data = _LIVE_CACHE.get("data")
                    raw_summary = _LIVE_CACHE.get("summary")

                for act in actions:
                    atype = act["type"]
                    if atype == "ENTER_ACTIVE":
                        log_app_event(
                            "JOB",
                            "ok",
                            f"ตรวจพบงานคัดแยกจริง! เปลี่ยนสถานะเป็น ACTIVE (ชม.นี้ {act['cur_hour_total']:,} ชิ้น | เครื่อง >=30 ชิ้น: {act['active_machines']} เครื่อง)",
                        )
                    elif atype == "SEND_HOURLY" and raw_data and raw_summary:
                        slot_label = act["slot"][11:16] if len(act["slot"]) >= 16 else act["slot"]
                        if not cfg.get("enable_feishu_hourly_report", True):
                            log_app_event("JOB", "info", f"ครบรอบชั่วโมง {slot_label} (ปิดโหมดรายชั่วโมงไว้ จึงข้ามการส่ง)")
                            continue
                        log_app_event("JOB", "info", f"ครบรอบชั่วโมง {slot_label} (ยอด {act['volume']:,} ชิ้น) กำลังส่งรายงาน Feishu...")
                        _dispatch_job_report(
                            cfg,
                            raw_data,
                            raw_summary,
                            mode_label=f"อัตโนมัติรายชั่วโมง ({slot_label})",
                            auto_recall_prev=bool(cfg.get("auto_recall_previous_hour", True)),
                            drop_empty_hours=False,
                        )
                    elif atype == "QUEUE_EOD_SUMMARY":
                        log_app_event(
                            "JOB",
                            "ok",
                            f"ยอดนิ่งครบ {act['idle_minutes']} นาที — จบงานแล้ว กลับสู่ STANDBY และรอส่งสรุปผล+Excel เวลา {act['eod_hour']:02d}:00 น.",
                        )
                    elif atype == "FINISH_JOB" and raw_data and raw_summary:
                        idle_m = act["idle_minutes"]
                        lbl = f"สรุปผลการคัดแยกประจำวัน ({int(cfg.get('eod_summary_hour', 8)):02d}:00 น.)" if act.get("scheduled_08") else f"สรุปจบงานอัตโนมัติ (ยอดนิ่ง {idle_m} นาที)"
                        log_app_event("JOB", "ok", f"{lbl} — ส่งสรุปผล + Excel (ตัดชั่วโมงยอด 0 ออก) และสแตนบายรอวันใหม่")
                        _dispatch_job_report(
                            cfg,
                            raw_data,
                            raw_summary,
                            mode_label=lbl,
                            auto_recall_prev=False,
                            drop_empty_hours=True,
                        )
            except Exception as ex:
                log_app_event("API", "warn", f"รอบตรวจจับงานพบข้อผิดพลาด: {ex}")
            time.sleep(20)

    threading.Thread(target=_loop, daemon=True).start()


def main():
    global _SERVER_REF
    cfg = load_config()
    port = int(cfg.get("dashboard_port", 8505))
    lan_ip = get_lan_ip()
    get_session_secret()
    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
    _SERVER_REF = server
    print(f"====================================================")
    print(f" CoGY Live Dashboard Server พร้อมทำงานแล้ว!")
    print(f" เปิดในเครื่องนี้ : http://localhost:{port}")
    print(f" เปิดในวง LAN   : http://{lan_ip}:{port}")
    print(f" ระบบป้องกัน    : Passcode Gate + HMAC Session + Rate Limit")
    if cfg.get("enable_public_tunnel", True):
        print(f" กำลังเปิด Cloudflare Tunnel (HTTPS)...")
        start_cloudflare_tunnel(port)
    else:
        print(f"====================================================")
    start_feishu_live_card_loop()
    start_auto_job_loop()
    server.serve_forever()


if __name__ == "__main__":
    main()
