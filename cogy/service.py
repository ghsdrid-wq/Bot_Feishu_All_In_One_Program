# -*- coding: utf-8 -*-
"""CoGY Service Adapter สำหรับรวมเข้ากับ Web UI (FastAPI) ของ Bot_Feishu_All_In_One_Program"""
from __future__ import annotations

import json
import socket
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import dashboard_server
from .cogy_client import CogyClient
from .feishu_dispatcher import FeishuDispatcher, load_sent_history
from .report_builder import build_excel_report, build_table_image, compute_summary


def _is_port_open(port: int) -> bool:
    s = socket.socket()
    try:
        s.settimeout(0.4)
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


class CogyService:
    def __init__(self, autostart: bool = True):
        self._busy = False
        self._lock = threading.Lock()
        self._live_snapshot: dict[str, Any] = {}
        self._server_thread: threading.Thread | None = None
        self._poller_started = False
        if autostart:
            self.start_poller()
            try:
                if dashboard_server.load_config().get("auto_start_server", True):
                    threading.Timer(1.2, self.start_server).start()
            except Exception:
                pass

    def start_poller(self) -> None:
        if self._poller_started:
            return
        self._poller_started = True

        def _loop():
            while True:
                try:
                    snap = dashboard_server.build_live_payload()
                    with self._lock:
                        self._live_snapshot = snap
                except Exception:
                    pass
                time.sleep(3)

        threading.Thread(target=_loop, daemon=True).start()

    def start_server(self) -> dict[str, Any]:
        cfg = dashboard_server.load_config()
        port = int(cfg.get("dashboard_port", 8505))
        if _is_port_open(port):
            return {"ok": True, "already_running": True, "port": port}
        if self._server_thread and self._server_thread.is_alive():
            return {"ok": True, "already_running": True, "port": port}

        def _run():
            try:
                dashboard_server.main()
            except Exception as ex:
                dashboard_server.log_app_event("SYSTEM", "err", f"CoGY Live Server หยุดทำงาน: {ex}")

        self._server_thread = threading.Thread(target=_run, daemon=True)
        self._server_thread.start()
        dashboard_server.log_app_event("SYSTEM", "info", f"กำลังเปิด CoGY Live Dashboard (พอร์ต {port}) + Auto Job Engine...")
        return {"ok": True, "port": port}

    def get_state(self, log_category: str = "ALL") -> dict[str, Any]:
        cfg = dashboard_server.load_config()
        port = int(cfg.get("dashboard_port", 8505))
        lan_ip = dashboard_server.get_lan_ip()
        now = datetime.now()
        shift_a = int(cfg.get("shift_a_start", 12))
        default_start = now.replace(hour=shift_a, minute=0, second=0, microsecond=0)
        if now.hour < shift_a:
            default_start -= timedelta(days=1)
        default_end = now.replace(minute=59, second=59, microsecond=0)

        with self._lock:
            live = dict(self._live_snapshot) if self._live_snapshot else {}

        return {
            "busy": self._busy,
            "server_running": _is_port_open(port),
            "port": port,
            "lan_url": f"http://{lan_ip}:{port}",
            "dashboard_url": cfg.get("dashboard_url", f"http://{lan_ip}:{port}"),
            "default_start": default_start.strftime("%Y-%m-%d %H:%M:%S"),
            "default_end": default_end.strftime("%Y-%m-%d %H:%M:%S"),
            "job_status": dashboard_server.get_job_status(),
            "app_logs": list(reversed(dashboard_server.get_app_events_tail(limit=150, category=log_category))),
            "live": live,
            "settings": {
                "cogy_base_url": cfg.get("cogy_base_url", "http://10.30.36.12/api"),
                "branch_name": cfg.get("branch_name", "KKN"),
                "dashboard_passcode": cfg.get("dashboard_passcode", "kkn2026"),
                "shift_a_start": shift_a,
                "shift_b_start": int(cfg.get("shift_b_start", 0)),
                "start_min_total_delta": int(cfg.get("start_min_total_delta", 50)),
                "start_min_per_machine": int(cfg.get("start_min_per_machine", 30)),
                "idle_finish_minutes": int(cfg.get("idle_finish_minutes", 180)),
                "eod_summary_hour": int(cfg.get("eod_summary_hour", 8)),
                "auto_start_server": bool(cfg.get("auto_start_server", True)),
                "auto_recall_previous_hour": bool(cfg.get("auto_recall_previous_hour", True)),
                "enable_public_tunnel": bool(cfg.get("enable_public_tunnel", True)),
                "feishu_test_mode": bool(cfg.get("feishu_test_mode", False)),
                "feishu_test_chat_id": str(cfg.get("feishu_test_chat_id", "")),
                "feishu_chat_id": str(cfg.get("feishu_chat_id", "")),
                "feishu_rooms": list(cfg.get("feishu_rooms") or []),
                "enable_feishu_live_card": bool(cfg.get("enable_feishu_live_card", True)),
                "enable_feishu_hourly_report": bool(cfg.get("enable_feishu_hourly_report", False)),
            },
        }

    def send_report(self, mode: str = "auto", start_str: str = "", end_str: str = "") -> dict[str, Any]:
        if self._busy:
            return {"ok": False, "error": "กำลังส่งรายงานอยู่ กรุณารอสักครู่"}
        self._busy = True

        def _worker():
            try:
                cfg = dashboard_server.load_config()
                now = datetime.now()
                shift_a = int(cfg.get("shift_a_start", 12))
                shift_b = int(cfg.get("shift_b_start", 0))
                if mode == "manual" and start_str and end_str:
                    s_dt = datetime.strptime(start_str.strip(), "%Y-%m-%d %H:%M:%S")
                    e_dt = datetime.strptime(end_str.strip(), "%Y-%m-%d %H:%M:%S")
                    mode_label = "กำหนดช่วงเวลาเอง (Manual)"
                else:
                    s_dt = now.replace(hour=shift_a, minute=0, second=0, microsecond=0)
                    if now.hour < shift_a:
                        s_dt -= timedelta(days=1)
                    e_dt = now.replace(minute=59, second=59, microsecond=0)
                    mode_label = "อัปเดตรายงาน CoGY"

                dashboard_server.log_app_event("SYSTEM", "info", f"ดึงข้อมูล CoGY ({s_dt:%d/%m %H:%M} - {e_dt:%d/%m %H:%M})...")
                client = CogyClient(
                    base_url=cfg["cogy_base_url"],
                    username=cfg["cogy_username"],
                    password=cfg["cogy_password"],
                    wcs_device_code=cfg.get("wcs_device_code", "101"),
                    dws_device_code=cfg.get("dws_device_code", "201"),
                    wcs_metric=cfg.get("wcs_metric", "scanBarcodeQuantity"),
                )
                data = client.fetch_hourly_matrices(s_dt, e_dt, use_cache=True)
                summary = compute_summary(data, shift_a_start=shift_a, shift_b_start=shift_b)
                excel_path = build_excel_report(data, summary, shift_a_start=shift_a, shift_b_start=shift_b)
                table_img = build_table_image(data, summary)

                target_chat_ids, is_test = dashboard_server.get_target_feishu_chat_ids(cfg)
                final_mode = f"[TEST MODE] {mode_label}" if is_test else mode_label
                recalled = 0
                for cid in target_chat_ids:
                    bot = FeishuDispatcher(cfg["feishu_app_id"], cfg["feishu_app_secret"], cid)
                    res = bot.send_hourly_report(
                        branch_name=cfg.get("branch_name", "KKN"),
                        data=data,
                        summary=summary,
                        chart_path=None,
                        table_image_path=table_img,
                        excel_path=excel_path if cfg.get("send_excel_to_feishu", True) else None,
                        mode_label=final_mode,
                        auto_recall_prev=bool(cfg.get("auto_recall_previous_hour", True)),
                        dashboard_url=cfg.get("dashboard_url", ""),
                    )
                    recalled += len(res.get("recalled_prev") or [])
                dashboard_server.log_app_event("FEISHU", "ok", f"ส่งรายงาน CoGY [{len(target_chat_ids)} ห้อง] สำเร็จ (Recall {recalled})")
            except Exception as ex:
                dashboard_server.log_app_event("FEISHU", "err", f"ส่งรายงาน CoGY ไม่สำเร็จ: {ex}")
            finally:
                self._busy = False

        threading.Thread(target=_worker, daemon=True).start()
        return {"ok": True}

    def recall(self, scope: str = "last") -> dict[str, Any]:
        if self._busy:
            return {"ok": False, "error": "กำลังทำงานอื่นอยู่"}
        self._busy = True

        def _worker():
            try:
                cfg = dashboard_server.load_config()
                target_chat_ids, _ = dashboard_server.get_target_feishu_chat_ids(cfg)
                cid = target_chat_ids[0] if target_chat_ids else cfg.get("feishu_chat_id", "")
                bot = FeishuDispatcher(cfg["feishu_app_id"], cfg["feishu_app_secret"], cid)
                if scope == "last":
                    n = sum(1 for _, ok, _ in bot.recall_latest_batch() if ok)
                else:
                    n = sum(1 for it in load_sent_history() if not it.get("recalled") and it.get("message_id") and bot.recall_message(it["message_id"])[0])
                dashboard_server.log_app_event("FEISHU", "ok", f"เรียกคืนข้อความ Feishu ({scope}) สำเร็จ: {n} รายการ")
            except Exception as ex:
                dashboard_server.log_app_event("FEISHU", "err", f"เรียกคืนข้อความไม่สำเร็จ: {ex}")
            finally:
                self._busy = False

        threading.Thread(target=_worker, daemon=True).start()
        return {"ok": True}

    def set_modes(self, payload: dict[str, Any]) -> dict[str, Any]:
        cfg = dashboard_server.load_config()
        if "enable_feishu_live_card" in payload:
            cfg["enable_feishu_live_card"] = bool(payload["enable_feishu_live_card"])
        if "enable_feishu_hourly_report" in payload:
            cfg["enable_feishu_hourly_report"] = bool(payload["enable_feishu_hourly_report"])
        if "feishu_test_mode" in payload:
            if payload["feishu_test_mode"] and not str(cfg.get("feishu_test_chat_id", "")).strip():
                return {"ok": False, "error": "กรุณากรอก Feishu Test Chat ID ก่อนเปิดโหมดทดสอบ"}
            cfg["feishu_test_mode"] = bool(payload["feishu_test_mode"])
        dashboard_server.CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        dashboard_server.log_app_event("FEISHU", "ok", "อัปเดตโหมดส่ง Feishu เรียบร้อย")
        return {"ok": True}

    def toggle_room(self, chat_id: str, enabled: bool) -> dict[str, Any]:
        cfg = dashboard_server.load_config()
        rooms = list(cfg.get("feishu_rooms") or [])
        for r in rooms:
            if r.get("chat_id") == chat_id:
                r["enabled"] = bool(enabled)
        cfg["feishu_rooms"] = rooms
        dashboard_server.CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True}

    def add_room(self, chat_id: str, name: str = "", enabled: bool = True) -> dict[str, Any]:
        cid = str(chat_id or "").strip()
        if not cid:
            return {"ok": False, "error": "กรุณากรอก Chat ID"}
        cfg = dashboard_server.load_config()
        rooms = list(cfg.get("feishu_rooms") or [])
        for r in rooms:
            if r.get("chat_id") == cid:
                r["enabled"] = bool(enabled)
                if name:
                    r["name"] = name
                break
        else:
            rooms.append({"chat_id": cid, "name": name or cid, "enabled": bool(enabled)})
        cfg["feishu_rooms"] = rooms
        dashboard_server.CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True}

    def remove_room(self, chat_id: str) -> dict[str, Any]:
        cfg = dashboard_server.load_config()
        cfg["feishu_rooms"] = [r for r in (cfg.get("feishu_rooms") or []) if r.get("chat_id") != chat_id]
        dashboard_server.CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True}

    def list_chats(self) -> list[dict[str, Any]]:
        cfg = dashboard_server.load_config()
        bot = FeishuDispatcher(cfg["feishu_app_id"], cfg["feishu_app_secret"], cfg.get("feishu_chat_id", ""))
        return bot.list_chats()

    def lookup_chat(self, chat_id: str) -> dict[str, Any]:
        cfg = dashboard_server.load_config()
        bot = FeishuDispatcher(cfg["feishu_app_id"], cfg["feishu_app_secret"], cfg.get("feishu_chat_id", ""))
        return bot.get_chat_info(chat_id)

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        cfg = dashboard_server.load_config()
        for k in ("cogy_base_url", "branch_name", "dashboard_passcode", "feishu_test_chat_id", "feishu_chat_id"):
            if k in payload:
                cfg[k] = str(payload[k] or "").strip()
        for k in ("shift_a_start", "shift_b_start", "start_min_total_delta", "start_min_per_machine", "idle_finish_minutes", "eod_summary_hour"):
            if k in payload and payload[k] != "":
                cfg[k] = int(payload[k])
        for k in ("auto_start_server", "auto_recall_previous_hour", "enable_public_tunnel", "feishu_test_mode", "enable_feishu_live_card", "enable_feishu_hourly_report"):
            if k in payload:
                cfg[k] = bool(payload[k])
        dashboard_server.CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        dashboard_server.log_app_event("SYSTEM", "ok", "บันทึกการตั้งค่า CoGY เรียบร้อยแล้ว")
        return {"ok": True}
