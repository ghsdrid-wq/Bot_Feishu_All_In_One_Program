"""Engine — ตัวทำงานหลักของโปรแกรมแบบไม่มีหน้าจอ (ไม่มี Tk)

ตรรกะงานทั้งหมด (รอบรายงานรายชั่วโมง, Auto, ดึงยอด DWS/JMS, Realtime DB, ตาราง/รูป, ส่ง Feishu,
สรุปรายวัน, VIP AWB, DWS Plan Controller, JMS User Bot, webhook Feishu, controller API 6100)
ใช้เมธอดชุดเดิมของ bot_main.App — พฤติกรรมเหมือนโปรแกรมเดิมทุกอย่าง
ไฟล์นี้เขียนใหม่เฉพาะเมธอดที่เคยอ่าน/เขียนหน้าจอ ให้ใช้ config.ini + ตัวแปรสถานะแทน แล้วให้ Web UI อ่านผ่าน API

หลักที่ทำให้ใช้ของเดิมได้: เมธอดเดิมเกือบทั้งหมดเช็ก is_ui_thread() ก่อนอ่านช่องกรอก
ถ้าไม่ใช่ UI thread จะอ่านจาก config แทน — Engine ตอบ False เสมอ จึงเดินทาง config ตลอด
"""
from __future__ import annotations

import configparser
import itertools
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta

import bot_main as legacy
import daily_summary
import vip_awb
from core import jms_policy

class _OsNoExplorer:
    """os ของ bot_main แต่ไม่เปิดหน้าต่าง Explorer (ปุ่มสร้างตัวอย่างของโปรแกรมเดิมเปิดโฟลเดอร์ผลลัพธ์บนจอ
    ซึ่งบนเว็บที่สั่งจากเครื่องอื่นจะไปเด้งบนจอเครื่องรันแทน) — หน้าเว็บมีลิงก์ดาวน์โหลดไฟล์แทน"""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    @staticmethod
    def startfile(*_args, **_kwargs):
        return None


import os as _os  # noqa: E402

legacy.os = _OsNoExplorer(_os)

PIPELINE_STEPS = legacy.PIPELINE_STEPS
REPORT_MODES = legacy.REPORT_MODES
REPORT_MODE_KEYS = legacy.REPORT_MODE_KEYS


class LogChannel:
    """บรรทัด log ล่าสุดของช่องหนึ่ง (main / controller / jms) ให้หน้าเว็บดึงต่อจากเลขที่เคยเห็น"""

    def __init__(self, size: int = 1500):
        self.lines: deque[dict] = deque(maxlen=size)
        self.seq = itertools.count(1)
        self.lock = threading.Lock()

    def add(self, text: str, level: str = "INFO") -> None:
        text = str(text).rstrip("\n")
        with self.lock:
            self.lines.append({"id": next(self.seq), "time": datetime.now().strftime("%H:%M:%S"),
                               "level": level, "text": text})

    def since(self, after: int) -> list[dict]:
        with self.lock:
            return [ln for ln in self.lines if ln["id"] > after]

    def clear(self) -> None:
        with self.lock:
            self.lines.clear()


class _Base:
    """เมธอดทั้งหมดของ bot_main.App (ฟังก์ชันชุดเดียวกัน ไม่ได้คัดลอกโค้ด)"""


for _name, _fn in legacy.App.__dict__.items():
    if _name.startswith("__") and _name != "__init__":
        continue
    if _name == "__init__":
        continue
    setattr(_Base, _name, _fn)


class Engine(_Base):
    def __init__(self, autostart: bool = True):
        self.ui_thread_id = None
        self.logs = {"main": LogChannel(), "controller": LogChannel(), "jms": LogChannel()}
        self.status_text, self.status_color = "Idle", "#a3be8c"
        self.pipeline_state: dict[str, str] = {k: "ready" for k, *_ in PIPELINE_STEPS}
        self.progress_value = 0
        self.dashboard_web_status = ""
        self.daily_summary_status = ("", "#aeb8cc")
        self.vip_status_text = ("", "#aeb8cc")
        self.controller_rows: list[dict] = []
        self.controller_rows_at: str | None = None
        self.timers_lock = threading.Lock()

        # ---- เหมือน App.__init__ ส่วนที่ไม่เกี่ยวกับหน้าจอ
        self.config = configparser.RawConfigParser()
        self.config.read(legacy.CONFIG_FILE, encoding="utf-8")
        self.scheduler_run_minute, self.scheduler_start_hour, self.scheduler_end_hour = 5, 15, 12
        self.auto_run_settings = None
        self.active_run_settings = None
        self.ensure_config()
        legacy_blocked = (self.config.get("JMS_USER", "blocked_keywords", fallback="").split(",")
                          if "JMS_USER" in self.config else [])
        self.jms_policy_path = jms_policy.default_policy_path()
        jms_policy.ensure_policy(self.jms_policy_path, legacy_blocked)

        import queue
        self.task_queue = queue.Queue()
        self.worker_running = True
        self.running = False
        self.current_run_source = None
        self.run_generation = 0
        self.active_run_generation = None
        self.run_state_lock = threading.Lock()
        self.scheduler_running = False
        self.last_run_minute = None
        self.mode = None
        self.last_activity = time.time()
        self.workbook_cards = []
        self.runtime_locked = False
        self.stop_requested = False
        self.next_run = None
        self.controller_clients = []
        self.dynamic_plans = []
        self.bot_running = False
        self.jms_running = False
        self.processed_events = deque(maxlen=1000)
        self.event_lock = threading.Lock()
        self.log_flush_lock = threading.Lock()
        self.watchdog_warn_seconds = 1800
        self.watchdog_timeout_seconds = 0
        self.watchdog_warning_interval_seconds = 1800
        self.watchdog_last_warning_at = 0
        self.system_alert_last_sent = {}
        self.bot_thread = None
        self.bot_server = None
        self.ds_busy = False
        self.vip_busy = False
        self.raw_time_source = "manual"
        self._dates_user_selected = False
        self._manual_dates = None
        self.load_controller_clients()
        self.load_dynamic_plans()
        self.attach_vip_awb_log()
        self.write_log(f"Engine started (Web UI) — v{legacy.APP_VERSION}", level="START")

        threading.Thread(target=self.worker_loop, daemon=True).start()
        threading.Thread(target=self.watchdog, daemon=True).start()
        if autostart:
            self.start_controller_api()
            self.after(30000, self.daily_summary_tick)
            self.after(1500, self.dashboard_autostart_web)
            self.after(2000, self.bots_autostart)
            # จำสถานะ Auto ([WEBUI] auto_on) — เปิดโปรแกรมใหม่แล้ว Auto เปิดต่อเอง (โปรแกรมเดิมต้องมีคนกด)
            if legacy.as_bool(self.config.get("WEBUI", "auto_on", fallback="false"), False):
                self.after(3000, lambda: self.start_scheduler(remember=False))

    # ================================================================ แทน Tk
    def is_ui_thread(self):
        return False

    def run_on_ui_thread(self, callback, *args, **kwargs):
        try:
            return callback(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 — เหมือน Tk.after ที่ไม่โยน error กลับ
            self.logs["main"].add(f"internal callback error: {e}", "ERROR")
            return None

    def after(self, ms, func=None, *args):
        if func is None:
            time.sleep(ms / 1000)
            return None
        timer = threading.Timer(max(0, ms) / 1000, lambda: self.run_on_ui_thread(func, *args))
        timer.daemon = True
        timer.start()
        return timer

    def after_cancel(self, _job):
        try:
            _job.cancel()
        except Exception:
            pass

    def queue_log_line(self, key, payload):
        if isinstance(payload, tuple):
            line, tag = payload
            # "[12:00:00] ✅ ข้อความ" -> ตัด timestamp เดิม หน้าเว็บมีเวลาของตัวเอง
            text = line.split("] ", 1)[1] if line.startswith("[") and "] " in line else line
            self.logs[key].add(text, tag or "INFO")
        else:
            text = str(payload)
            if text.startswith("[") and "] " in text and text[1:5].isdigit():
                text = text.split("] ", 1)[1]
            level = "ERROR" if "[ERROR]" in text.upper() else "INFO"
            self.logs[key].add(text, level)

    def flush_log_buffer(self, key):
        return None

    # ---- สถานะที่หน้าเว็บอ่าน
    def set_status(self, text, color="#a3be8c", bg="#3b4a3e"):
        self.status_text, self.status_color = text, color

    def set_progress(self, val):
        self.progress_value = max(0, min(100, int(val)))

    def set_pipeline_state(self, key=None, state="ready"):
        if key in REPORT_MODE_KEYS:
            active = self.__dict__.get("active_report_mode")
            if state == "skip" and active is not None and active != key:
                return
            key = "report"
        for k in ([key] if key else list(self.pipeline_state)):
            self.pipeline_state[k] = state

    def set_next_run_display(self, next_run=None, clear=False):
        self.next_run = None if clear else (next_run if next_run is not None else self.get_next_scheduler_run_time())

    def set_runtime_lock_visuals(self, active):
        self.runtime_locked = active
        if not active and not self.running and not self.scheduler_running:
            self.set_status("Idle", "#a3be8c")

    def set_ui_running(self, active):
        self.runtime_locked = active
        if not active:
            self.set_pipeline_state(None, "ready")
            self.set_progress(0)

    def schedule_pipeline_reset(self, run_generation=None):
        def reset(g=run_generation):
            if not self.is_run_generation_active(g):
                return
            self.set_pipeline_state()
            self.set_progress(0)
            if self.vip_busy:
                self.set_pipeline_state("vip_awb", "run")
        self.after(1500, reset)

    def set_daily_summary_status(self, text, color="#aeb8cc"):
        self.daily_summary_status = (text, color)

    def refresh_daily_summary_last_sent(self):
        return None

    def save_daily_summary_fields(self):
        return None  # หน้าเว็บเขียน [DAILY_SUMMARY] ลง config ตรง ๆ

    def set_vip_awb_status(self, text, color="#aeb8cc"):
        self.vip_status_text = (text, color)

    def collect_vip_awb_settings(self):
        return None

    def dashboard_set_web_status(self, text, color="#aeb8cc"):
        self.dashboard_web_status = text

    def dashboard_open_web(self):
        if not getattr(self, "dash_web_started", False):
            self.dashboard_start_web(auto=True)

    def update_bot_ui(self):
        return None

    def update_jms_ui(self):
        return None

    def render_controller_status(self, rows):
        self.controller_rows = rows
        self.controller_rows_at = datetime.now().strftime("%H:%M:%S")

    def render_nav_menu(self):
        return None

    def reload_workbook_cards(self):
        return None  # หน้าเว็บอ่านรายการไฟล์ Excel จาก config เอง

    def refresh_status(self):
        threading.Thread(target=self.refresh_status_thread, daemon=True).start()

    def update_clock(self):
        return None

    # ================================================================ config
    def save_config(self):
        legacy.save_config(self.config)
        try:
            from metrics import core as metrics_core
            metrics_core.clear_config_cache()
        except Exception:
            pass
        self.refresh_scheduler_snapshot()
        self.load_controller_clients()
        if self.__dict__.get("scheduler_running") and self.auto_run_settings:
            # ค่าที่แก้ระหว่าง Auto เปิด (เช่น ติ๊กขั้นตอน) มีผลรอบถัดไป เหมือนโปรแกรมเดิม
            self.auto_run_settings = self.build_run_settings_snapshot(self.auto_run_settings.get("out", ""))

    def save_all(self):
        self.save_config()

    def reload_config(self):
        """อ่าน config.ini ใหม่ (หลังหน้าเว็บเขียนลงไป)"""
        cp = configparser.RawConfigParser()
        cp.read(legacy.CONFIG_FILE, encoding="utf-8")
        self.config = cp
        self.refresh_scheduler_snapshot()
        self.load_controller_clients()

    def output_dir(self) -> str:
        return legacy.clean_input_value(self.config.get("PATH", "output_dir", fallback=""))

    def selected_steps(self):
        dws = self.config["DWS_JMS"] if "DWS_JMS" in self.config else {}
        steps = {}
        for key, _title, _icon, default_on in PIPELINE_STEPS:
            raw = dws.get(f"step_{key}")
            steps[key] = legacy.as_bool(raw, default_on) if raw not in (None, "") else default_on
        steps.update(legacy.report_mode_steps(steps.get("report", True), self.report_mode()))
        return steps

    # ================================================================ วันที่ของรอบ
    def set_dates(self, start: date, end: date) -> None:
        """เลือกช่วงวันที่เอง (ดึงย้อนหลัง) — ใช้ได้เฉพาะตอนโปรแกรมเปิดอยู่ เหมือนโปรแกรมเดิม"""
        if start > end:
            start, end = end, start
        self._manual_dates = (start, end)
        self._dates_user_selected = True

    def reset_dates(self) -> None:
        self._manual_dates = None
        self._dates_user_selected = False

    def current_dates(self) -> tuple[date, date]:
        if self._dates_user_selected and self._manual_dates:
            return self._manual_dates
        return self.shift_date_range()

    def get_time_range(self):
        active = getattr(self, "active_run_settings", None) or {}
        if active.get("time_range"):
            start, end = active["time_range"]
        else:
            start, end = self.get_scheduler_time_range()
        return self._export_datetime_string(start), self._export_datetime_string(end)

    # ================================================================ คำสั่งจากหน้าเว็บ
    def validate_ready(self):
        selection = self.validate_run_selection()
        if not selection:
            return None
        run_export, run_chat = selection
        out = self.output_dir()
        keys = [x.strip() for x in self.config["WORKBOOKS"].get("items", "").split(",") if x.strip()]
        if run_export and not self.validate_dws_jms_ready():
            return None
        if run_chat:
            if not out:
                self.write_log("ยังไม่ได้ตั้งโฟลเดอร์ Output (หน้าตั้งค่า)", level="WARN")
                return None
            if not keys and self.selected_steps().get("excel"):
                self.write_log("กรุณาเพิ่มไฟล์ Excel อย่างน้อย 1 ไฟล์ในหน้าไฟล์ Excel", level="WARN")
                return None
        return out if run_chat else self.get_raw_export_folder()

    def prepare_run_settings(self):
        out = self.validate_ready()
        if not out:
            return None
        self.save_config()
        return self.build_run_settings_snapshot(out)

    def start_scheduler(self, remember: bool = True):
        if remember:
            self._remember_auto(True)
        return _Base.start_scheduler(self)

    def stop_scheduler(self):
        self._remember_auto(False)
        self.scheduler_running = False
        self.set_next_run_display(clear=True)
        if not self.running:
            self.cancel_pending_runs()
            self.stop_requested = False
            self.set_ui_running(False)
        else:
            self.mode = "manual"
        self.write_log("Auto scheduler stopped", level="SUCCESS")
        self.log_scheduler_event("Auto stopped by user (Web UI)")
        self.set_status("Processing" if self.running else "Idle", "#ebcb8b" if self.running else "#a3be8c")

    def _remember_auto(self, on: bool) -> None:
        if "WEBUI" not in self.config:
            self.config["WEBUI"] = {}
        self.config["WEBUI"]["auto_on"] = "true" if on else "false"
        legacy.save_config(self.config)

    def stop_process(self):
        if not self.running:
            self.cancel_pending_runs()
            self.stop_requested = False
            return
        self.cancel_pending_runs()
        self.running = False
        self.stop_requested = True
        self.current_run_source = None
        if not self.scheduler_running:
            self.set_ui_running(False)
        self.write_log("Current process stopped by user", level="WARN")
        if self.scheduler_running:
            self.set_next_run_display(self.get_next_scheduler_run_time())
            self.set_status("Auto Waiting", "#88c0d0")
        else:
            self.set_next_run_display(clear=True)
            self.set_status("Stopped", "#d3868e")

    def start_daily_summary(self, start: datetime, end: datetime, send: bool) -> str | None:
        if self.running or self.ds_busy:
            return "มีงานอื่นกำลังรันอยู่ รอให้เสร็จก่อน"
        if end <= start:
            return "ช่วงเวลาไม่ถูกต้อง: เวลาจบต้องหลังเวลาเริ่ม"
        settings = self.daily_summary_settings(self.output_dir())
        self.run_daily_summary_in_background(settings, start.strftime("%Y-%m-%d %H:%M:%S"),
                                             end.strftime("%Y-%m-%d %H:%M:%S"), send, scope=True)
        return None

    def start_jms_bot(self):
        self.start_jms_placeholder(auto=True)

    def snapshot(self) -> dict:
        """สถานะรวมให้หน้าเว็บ (เรียกบ่อย ต้องเร็ว)"""
        start_d, end_d = self.current_dates()
        self.refresh_scheduler_snapshot()
        return {
            "version": legacy.APP_VERSION,
            "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": self.status_text,
            "status_color": self.status_color,
            "running": self.running,
            "mode": self.mode,
            "scheduler_running": self.scheduler_running,
            "next_run": self.next_run.strftime("%Y-%m-%d %H:%M") if isinstance(self.next_run, datetime) else None,
            "progress": self.progress_value,
            "pipeline": self.pipeline_state,
            "dates": {"start": start_d.isoformat(), "end": end_d.isoformat(), "manual": self._dates_user_selected},
            "time": {"run_minute": self.scheduler_run_minute, "start_hour": self.scheduler_start_hour,
                     "end_hour": self.scheduler_end_hour},
            "bot_running": self.bot_running,
            "jms_running": self.jms_running,
            "ds_busy": self.ds_busy,
            "ds_status": self.daily_summary_status[0],
            "vip_busy": self.vip_busy,
            "vip_status": self.vip_status_text[0],
            "dashboard_web": self.dashboard_web_status,
            "jms_token_set": bool(self.get_jms_auth_token()),
            "feishu_ready": bool(self.config.get("FEISHU", "APP_ID", fallback="").strip()
                                 and self.config.get("FEISHU", "APP_SECRET", fallback="").strip()),
        }
