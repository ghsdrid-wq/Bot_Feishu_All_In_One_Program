import os
import re
import sys
import json
import mimetypes
import time
import queue
import threading
import configparser
import ctypes
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor
from collections import deque


import customtkinter as ctk
from tkinter import filedialog, messagebox
from tkcalendar import DateEntry

import requests
import pymysql
import openpyxl
from flask import Flask, request, jsonify
from waitress import serve

from controller.controller_api import register_controller, start_api
from core.jms_api import search_user, reset_app_password, reset_jms_password, enable_user
from core.logger import write_log

from Createphoto import (
    run_create,
    migrate_old_export_config,
    save_config,
    resource_path,
    safe_key,
    unique_key,
)
import Botmessage
from Botmessage import run_send

# ==========================================================
# Nord design tokens (Fluent Soft style)
# ==========================================================
NORD = {
    # Polar Night (surfaces)
    "bg": "#2e3440",        # page background (nord0)
    "sidebar": "#272c36",   # darker than page
    "surface": "#3b4252",   # card (nord1)
    "surface_2": "#434c5e", # elevated (nord2)
    "surface_3": "#4c566a", # more elevated (nord3)
    "inner": "#323847",     # inner panel
    "log_bg": "#252b36",
    # Snow Storm (text)
    "text": "#eceff4",      # primary (nord6)
    "text_2": "#d8dee9",    # (nord4)
    "text_muted": "#aeb8cc",
    "text_dim": "#7b8496",
    "text_disabled": "#5b6474",
    # Frost (accent/blue)
    "frost": "#88c0d0",     # primary accent (nord8)
    "frost_2": "#8fbcbb",   # (nord7)
    "blue": "#5e81ac",      # (nord10)
    "blue_hover": "#4c6e93",
    # Aurora (status)
    "green": "#a3be8c", "green_hover": "#8ca876", "green_bg": "#3b4a3e",
    "red": "#bf616a", "red_hover": "#a54f58", "red_bg": "#4a3438", "red_text": "#d3868e",
    "yellow": "#ebcb8b", "yellow_bg": "#4a4433",
    "purple": "#b48ead", "purple_hover": "#9e7a98", "purple_bg": "#4a3c52",
    "orange": "#d08770", "orange_hover": "#b87560",
}


def readable_on(hex_fill):
    """เลือกสีตัวอักษรที่อ่านออกบนพื้น hex (เข้มบนพื้นสว่าง / สว่างบนพื้นเข้ม)"""
    try:
        h = str(hex_fill).lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        return NORD["bg"] if lum > 150 else NORD["text"]
    except Exception:
        return NORD["text"]


def apply_nord_theme():
    """Override customtkinter default widget colors ให้เป็นโทน Nord
    (สำหรับ widget ที่ไม่ได้ระบุ fg_color เอง เช่น OptionMenu/CheckBox/Entry/ProgressBar)"""
    try:
        theme = ctk.ThemeManager.theme
    except Exception:
        return

    def dual(v):
        return [v, v]

    overrides = {
        "CTkOptionMenu": {
            "fg_color": dual(NORD["blue"]),
            "button_color": dual(NORD["surface_2"]),
            "button_hover_color": dual(NORD["surface_3"]),
            "text_color": dual(NORD["text"]),
            "corner_radius": 10,
        },
        "CTkCheckBox": {
            "fg_color": dual(NORD["blue"]),
            "hover_color": dual(NORD["frost"]),
            "border_color": dual(NORD["surface_3"]),
            "checkmark_color": dual(NORD["text"]),
            "text_color": dual(NORD["text"]),
            "corner_radius": 6,
        },
        "CTkEntry": {
            "fg_color": dual(NORD["surface"]),
            "border_color": dual(NORD["surface_2"]),
            "text_color": dual(NORD["text"]),
            "placeholder_text_color": dual(NORD["text_dim"]),
            "corner_radius": 10,
        },
        "CTkProgressBar": {
            "fg_color": dual(NORD["surface_2"]),
            "progress_color": dual(NORD["frost"]),
            "corner_radius": 8,
        },
        "CTkScrollbar": {
            "button_color": dual(NORD["surface_3"]),
            "button_hover_color": dual(NORD["text_dim"]),
        },
        "CTkButton": {
            "fg_color": dual(NORD["blue"]),
            "hover_color": dual(NORD["blue_hover"]),
            "text_color": dual(NORD["text"]),
            "corner_radius": 12,
        },
    }
    for widget, props in overrides.items():
        if widget not in theme:
            continue
        for prop, value in props.items():
            try:
                theme[widget][prop] = value
            except Exception:
                pass


APP_TITLE = "Auto Report Feishu Enterprise Console v13.0"
CONFIG_FILE = resource_path("config.ini")
SINGLE_INSTANCE_MUTEX_NAME = "Local\\AutoReportFeishuEnterpriseConsole"
SINGLE_INSTANCE_MUTEX_HANDLE = None
SYSTEM_ALERT_CHAT_ID = "oc_3b94544c4b8d3fa5d9dc98bd500830aa"

# นาทีที่ตรวจ token เชิงรุกในแต่ละชั่วโมง (แก้ได้ที่นี่)
TOKEN_HEALTHCHECK_MINUTES = (20, 40)

DEFAULT_FEISHU = {
    "APP_ID": "",
    "APP_SECRET": "",
    "CHAT_ID": "",
    "VERIFY_TOKEN": "mytoken",
    "BOT_PORT": "7000",
    "NGROK_URL": "",
    "JMS_TOKEN": "",
    "BOT_NAME": "BOT_JMSKKN",
}

DEFAULT_PATH = {
    "output_dir": "",
}

DEFAULT_TIME = {
    "run_minute": "5",
    "start_hour": "15",
    "end_hour": "12",
}

DEFAULT_UI = {
    "nav_order": "home,workbooks,data_export,dws_plan,jms_user,settings",
}

DEFAULT_DWS_JMS = {
    "db_host": "10.30.32.10",
    "db_port": "3306",
    "db_user": "root",
    "db_password": "root",
    "db_name": "dwsdb_thailand",
    "db_table": "tab_assembly_dws_sorting_log",
    "db_sheet": "分拣日志",
    "jms_token": "",
    "name_dws": "DWS9-11.xlsx",
    "name_auto": "DWSXAUTOPDA.xlsx",
    "name_dwspda": "DWSPDA.xlsx",
    "name_realtime_db": "RealtimeDB.xlsx",
    "start_date": "",
    "end_date": "",
    "start_hour": "13:00",
    "end_hour": "23:00",
    "raw_path": "",
    "enabled": "true",
    "send_dws_file": "false",
    "send_auto_file": "false",
    "send_dwspda_file": "false",
    "send_realtime_file": "false",
}


DEFAULT_CONTROLLER_CLIENTS = {
    "DWS1": ("10.30.32.32", "4000"),
    "DWS2": ("10.30.32.33", "4000"),
    "DWS3": ("10.30.32.34", "4000"),
    "DWS4": ("10.30.32.35", "4000"),
    "DWS5": ("10.30.32.36", "4000"),
    "DWS6": ("10.30.32.37", "4000"),
    "DWS7": ("10.30.32.38", "4000"),
    "DWS8": ("10.30.32.39", "4000"),
    "DWS9-11": ("10.30.32.10", "4001"),
}


bot_app = Flask(__name__)
controller_instance = None
STAFF_PATTERN = re.compile(r"(?<![0-9A-Z])(?:THPT\d{8}|\d{6}T\d{6}|\d{5,9}|(?=[0-9A-Z]{8,20}(?![0-9A-Z]))(?=[0-9A-Z]*\d)(?=[0-9A-Z]*[A-Z])[0-9A-Z]{8,20})(?![0-9A-Z])")


def get_controller_feishu_value(key: str, default: str = "") -> str:
    app = controller_instance
    if app is None:
        return default
    return app.get_feishu_config_value(key, default)


def get_tenant_access_token():
    try:
        app_id = get_controller_feishu_value("APP_ID").strip()
        app_secret = get_controller_feishu_value("APP_SECRET").strip()
        response = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10,
        )
        data = response.json()
        return data.get("tenant_access_token")
    except Exception as e:
        print(e)
        return None


def reply_feishu_message(message_id, text):
    token = get_tenant_access_token()
    if not token:
        return False
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"content": json.dumps({"text": text}), "msg_type": "text"}
    response = requests.post(url, headers=headers, json=payload, timeout=10)
    return response.status_code == 200


def send_feishu_chat_message(chat_id, text):
    token = get_tenant_access_token()
    if not token or not chat_id:
        return False
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"receive_id": chat_id, "content": json.dumps({"text": text}), "msg_type": "text"}
    response = requests.post(url, headers=headers, json=payload, timeout=10)
    return response.status_code == 200


def send_system_alert(text):
    return send_feishu_chat_message(SYSTEM_ALERT_CHAT_ID, text)


def extract_staff_numbers(text: str, bot_name: str = "") -> List[str]:
    """Extract staff/user codes from Feishu text while ignoring bot mentions."""
    bot_key = (bot_name or "").strip().upper().lstrip("@")
    found = []
    for token in STAFF_PATTERN.findall((text or "").upper()):
        if token.startswith("BOT"):
            continue
        if bot_key and token == bot_key:
            continue
        if token not in found:
            found.append(token)
    return found


def strip_bot_response_noise(text: str) -> str:
    cleaned_lines = []
    for raw_line in str(text or "").replace("\\n", "\n").splitlines():
        line = raw_line.strip()
        lower_line = line.lower()
        if re.match(r"^(name|app password|jms password|status)\s*:", lower_line):
            continue
        if "ดำเนินการเสร็จเรียบร้อย" in line:
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def split_feishu_command_blocks(text: str, bot_name: str = "") -> List[str]:
    command_keywords = [
        "รีapp", "รี app", "รี่app", "รี่ app", "รี้app", "รี้ app", "รี๊app", "รี๊ app",
        "รีแอพ", "รี แอพ", "รี่แอพ", "รี่ แอพ", "รี้แอพ", "รี้ แอพ",
        "รีรหัสapp", "รีรหัส app",
        "รีรหัสแอพ", "รีรหัส แอพ", "รีแอป", "รี แอป", "รีรหัสแอป",
        "รีรหัส แอป", "รีเซ็ตapp", "reset app", "reset password app",
        "รีแอพให้หน่อย", "รีรหัสแอพให้หน่อย", "รีรหัสpda",
        "รีรหัสpdaให้หน่อย", "รีรหัส pda ให้หน่อย", "รีjms", "รีรหัสjms",
        "รีรหัส jms", "รี jms", "รีเซ็ตjms", "reset jms", "reset password jms",
        "รี jms ให้หน่อย", "รีรหัส jms ให้หน่อย", "เปิดยูส", "เปิด user",
        "enable", "เปิดใช้งาน", "ปลดล็อค", "ปลดล้อค", "unlock", "ระงับ",
        "โดนระงับ", "เข้าไม่ได้", "ใช้งานไม่ได้", "ปลด user", "เปิดรหัส",
        "เปิดไอดี", "dwsa", "กะบ่าย", "แพลนบ่าย", "เปลี่ยนกะบ่าย",
        "เปลี่ยนแพลนบ่าย", "dwsb", "กะดึก", "แพลนดึก", "เปลี่ยนกะดึก",
        "เปลี่ยนแพลนดึก",
    ]
    text = strip_bot_response_noise(text)
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return [str(text or "")]

    command_lines = []
    current_command = []
    current_has_keyword = False
    current_has_user = False

    for line in lines:
        lower_line = line.lower()
        has_keyword = bool(
            detect_jms_intent(line)
            or detect_plan_intent(line)
            or any(keyword in lower_line for keyword in command_keywords)
        )
        has_user = bool(extract_staff_numbers(line, bot_name))

        if has_keyword:
            if current_command and current_has_keyword and current_has_user:
                command_lines.append("\n".join(current_command))
                current_command = [line]
                current_has_user = has_user
            else:
                current_command.append(line)
                current_has_user = current_has_user or has_user
            current_has_keyword = True
            continue

        if has_user:
            current_command.append(line)
            current_has_user = True

    if current_command:
        command_lines.append("\n".join(current_command))

    return command_lines or [str(text or "")]


def acquire_single_instance_lock() -> bool:
    global SINGLE_INSTANCE_MUTEX_HANDLE
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX_NAME)
    if not handle:
        return True
    SINGLE_INSTANCE_MUTEX_HANDLE = handle
    return kernel32.GetLastError() != 183


def feishu_bot_mentioned(message: dict, text: str, bot_name: str) -> bool:
    if message.get("chat_type") == "p2p":
        return True

    bot_key = (bot_name or "BOT_JMSKKN").strip().lower().lstrip("@")
    text_key = (text or "").lower().replace("@", "")
    if bot_key and bot_key in text_key:
        return True

    for mention in message.get("mentions", []) or []:
        for key in ("name", "key", "id", "open_id", "union_id"):
            value = str(mention.get(key, "")).strip().lower().replace("@", "")
            if bot_key and bot_key in value:
                return True
    return False


def normalize_message_text(text):
    text = str(text or "").lower()
    text = text.replace("\\n", "\n")
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def contains_any(text, keywords):
    return any(keyword in text for keyword in keywords)


def detect_jms_intent(text):
    normalized_text = normalize_message_text(strip_bot_response_noise(text))
    reset_words = [
        "รี",
        "รี่",
        "รี้",
        "รี๊",
        "รีเซ็ต",
        "reset",
        "password",
        "รหัส",
        "เข้าไม่ได้",
        "ล็อค",
        "ล๊อค",
        "locked",
    ]
    app_words = [
        "app",
        "แอพ",
        "แอป",
        "pda",
        "application",
        ]
    jms_words = [
        "jms"
        ]
    enable_words = [
        "เปิดยูส",
        "เปิด user",
        "enable",
        "เปิดใช้งาน",
        "เปิดบัญชี",
        "บัญชี",
        "ปลดล็อค",
        "ปลดล้อค",
        "ปลดล็อก",
        "unlock",
        "ระงับ",
        "โดนระงับ",
        "เข้าไม่ได้",
        "ใช้งานไม่ได้",
        "ปลด user",
        "เปิดรหัส",
        "เปิดไอดี",
        "รหัสปิด",
        "ล็อค",
        "ล๊อค",
        "locked",
    ]
    has_reset = contains_any(normalized_text, reset_words)
    has_app = contains_any(normalized_text, app_words)
    has_jms = contains_any(normalized_text, jms_words)
    has_enable = contains_any(normalized_text, enable_words)
    if has_reset and has_app and not has_jms:
        return "APP"
    if has_reset and has_jms and not has_app:
        return "JMS"
    if has_reset and has_app and has_jms:
        app_pos = min([normalized_text.find(k) for k in app_words if k in normalized_text] or [999999])
        jms_pos = min([normalized_text.find(k) for k in jms_words if k in normalized_text] or [999999])
        reset_pos = min([normalized_text.find(k) for k in reset_words if k in normalized_text] or [0])
        return "JMS" if abs(jms_pos - reset_pos) <= abs(app_pos - reset_pos) else "APP"
    if has_enable:
        return "ENABLE"
    return None


def detect_plan_intent(text):
    normalized_text = normalize_message_text(text)
    has_plan_action = contains_any(
        normalized_text,
        ["เปลี่ยน", "สลับ", "แพลน", "กะ", "plan"],
    )
    if not has_plan_action:
        return None
    if contains_any(normalized_text, ["บ่าย", "dwsa", "dws a"]):
        return "DWSA"
    if contains_any(normalized_text, ["ดึก", "dwsb", "dws b"]):
        return "DWSB"
    return None


@bot_app.route("/status")
def bot_status():
    app = controller_instance
    running = bool(getattr(app, "bot_running", False)) if app is not None else False
    return jsonify({
        "success": True,
        "status": "online" if running else "offline",
        "message": "Feishu Bot Online" if running else "Feishu Bot Offline",
    })


@bot_app.route("/switch_plan", methods=["POST"])
def bot_switch_plan():
    data = request.get_json(silent=True) or {}
    plan = data.get("plan", "").strip()
    if not plan:
        return jsonify({"success": False, "message": "Plan Empty"})
    if controller_instance:
        controller_instance.switch_plan(plan)
    return jsonify({"success": True, "message": f"Switching to {plan}"})


@bot_app.route("/feishu_event", methods=["POST"])
def feishu_event():
    app = controller_instance
    if app is None:
        return jsonify({"success": False, "message": "controller_not_ready"})

    data = request.get_json(silent=True) or {}
    if "challenge" in data:
        return jsonify({"challenge": data["challenge"]})
    if not getattr(app, "bot_running", False):
        app.notify_it_alert(
            "bot_dws_plan_offline",
            "ต้องเปิด BOT DWS PLAN",
            "Feishu ส่ง event เข้ามา แต่ BOT DWS PLAN ยังไม่ได้ START BOT"
        )
        return jsonify({"success": True, "message": "bot_offline"})

    event_id = data.get("header", {}).get("event_id")
    if event_id:
        event_lock = app.__dict__.get("event_lock")
        if event_lock is not None:
            with event_lock:
                if event_id in app.processed_events:
                    app.jms_log(f"[SKIP DUPLICATE] {event_id}")
                    return jsonify({"success": True, "message": "duplicate_skip"})
                app.processed_events.append(event_id)
        else:
            if event_id in app.processed_events:
                app.jms_log(f"[SKIP DUPLICATE] {event_id}")
                return jsonify({"success": True, "message": "duplicate_skip"})
            app.processed_events.append(event_id)

    event = data.get("event", {})
    message = event.get("message", {})
    chat_id = message.get("chat_id")
    message_id = message.get("message_id")
    parent_id = message.get("parent_id")
    root_id = message.get("root_id")
    content_raw = message.get("content", "{}")

    try:
        content_json = json.loads(content_raw)
        text = content_json.get("text", "").strip().replace("\\n", "\n")
        if message.get("chat_type") != "p2p":
            bot_name = get_controller_feishu_value("BOT_NAME", "BOT_JMSKKN").strip().lower()
            if not feishu_bot_mentioned(message, text, bot_name):
                app.jms_log(f"[SKIP NO MENTION] text={text[:80]}")
                return jsonify({"status": "skip_no_mention"})
    except Exception:
        return jsonify({"success": False})

    app.jms_log(f"[FEISHU] {text}")

    def send_help():
        reply_feishu_message(
            message_id,
            "คำสั่งไม่ถูกต้อง ตรวจสอบใหม่อีกครั้ง\n\n"
            "รีเซ็ตรหัส:\n"
            "@BOT รีรหัส app 999004T000XX\n"
            "@BOT รีรหัส pda 999004T000XX\n"
            "@BOT รีรหัส jms 999004T000XX\n\n"
            "รหัสล็อค:\n"
            "@BOT ปลดล็อค 999004T000XX\n\n"
            "เปลี่ยนแพลน:\n"
            "@BOT เปลี่ยนแพลนบ่าย\n"
            "@BOT เปลี่ยนแพลนดึก",
        )

    command_lines = split_feishu_command_blocks(text, get_controller_feishu_value("BOT_NAME", "BOT_JMSKKN"))

    processed_any = False
    for command_text in command_lines:
        merged_text = " ".join(command_text.splitlines())
        command_lower = merged_text.lower()
        normalized_command_lower = re.sub(r"\s+", " ", command_lower).strip()
        try:
            handled = app.handle_jms_command(command_text, chat_id, message_id, parent_id, root_id)
        except Exception as e:
            print("JMS ERROR:", e)
            app.jms_log(f"[ERROR] {e}")
            return jsonify({"success": False, "error": str(e)})
        if handled:
            processed_any = True
            continue

        target_plan = detect_plan_intent(normalized_command_lower)
        if re.search(r"(เปลี่ยน|สลับ).*(บ่าย|dwsa)", normalized_command_lower):
            target_plan = "DWSA"
        elif re.search(r"(เปลี่ยน|สลับ).*(ดึก|dwsb)", normalized_command_lower):
            target_plan = "DWSB"

        if target_plan:
            app.switch_plan(target_plan)
            reply_feishu_message(
                message_id,
                f"PLAN : {target_plan}\nดำเนินการเปลี่ยนแพลนเสร็จเรียบร้อย\nปิดโปรแกรมแล้วเข้าใหม่อีกครั้ง",
            )
            app.add_log(f"[FEISHU] SWITCH -> {target_plan}")
            processed_any = True
            continue

        if "/status" in command_lower:
            reply_feishu_message(message_id, "🟢 Controller Online")
            processed_any = True

    if processed_any:
        return jsonify({"success": True, "message": "handled"})
    send_help()
    return jsonify({"success": True, "message": "ignored"})


def as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    value = str(value).strip().lower()
    if value == "":
        return default
    return value in {"1", "true", "yes", "on", "y"}


def clean_export_name(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", text.strip().upper())
    return text.strip("_") or "EXPORT"


def next_numeric_name(existing_names: List[str]) -> str:
    numbers = []
    for name in existing_names:
        try:
            numbers.append(int(str(name).strip()))
        except ValueError:
            pass
    return str((max(numbers) if numbers else 0) + 1)


def clean_input_value(value: str, collapse_internal_spaces: bool = False) -> str:
    """Trim copied text safely before saving/using tokens, chat IDs, paths and filenames."""
    text = str(value or "").strip()
    if collapse_internal_spaces:
        text = re.sub(r"\s+", "", text)
    return text


def entry_value(entry, collapse_internal_spaces: bool = False) -> str:
    if entry is None:
        return ""
    return clean_input_value(entry.get(), collapse_internal_spaces=collapse_internal_spaces)


class SheetRow(ctk.CTkFrame):
    def __init__(self, master, app, workbook_key: str, export_name: str):
        super().__init__(master, fg_color="#3b4252", corner_radius=12)
        self.app = app
        self.workbook_key = workbook_key
        self.export_name = export_name
        self.entries: Dict[str, ctk.CTkEntry] = {}
        self.parent_enabled = True

        section = f"EXPORT:{export_name}"
        if section not in self.app.config:
            self.app.config[section] = {}
        sec = self.app.config[section]

        # User-facing layout only. Workbook mapping is kept internally in config.
        for i in range(10):
            self.grid_columnconfigure(i, weight=0)
        self.grid_columnconfigure(2, weight=1)
        self.grid_columnconfigure(3, weight=1)
        self.grid_columnconfigure(4, weight=1)

        self.enabled_var = ctk.BooleanVar(value=as_bool(sec.get("enabled", "true"), True))
        self.send_var = ctk.BooleanVar(value=as_bool(sec.get("send_enabled", "true"), True))
        self.delete_var = ctk.BooleanVar(value=as_bool(sec.get("delete_by_start", "false"), False))

        self.use_chk = ctk.CTkCheckBox(self, text="Use", variable=self.enabled_var, command=self.on_enabled_changed, width=56)
        self.use_chk.grid(row=0, column=0, padx=(10, 4), pady=8)

        self.no_box = ctk.CTkLabel(
            self,
            text=str(export_name),
            width=64,
            fg_color="#434c5e",
            text_color="#e5e9f0",
            corner_radius=8,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.no_box.grid(row=0, column=1, padx=4, pady=8, sticky="ew")

        self._entry("sheet", sec.get("sheet", ""), 2, "Sheet")
        self._entry("range", sec.get("range", "A1:Z50"), 3, "Range")
        self._entry("file", sec.get("file", f"export_{export_name}.png"), 4, "File.png")

        self.delete_chk = ctk.CTkCheckBox(self, text="Delete", variable=self.delete_var, command=self.save, width=74)
        self.delete_chk.grid(row=0, column=5, padx=4, pady=8)

        self.send_chk = ctk.CTkCheckBox(self, text="Send", variable=self.send_var, command=self.save, width=64)
        self.send_chk.grid(row=0, column=6, padx=4, pady=8)

        self.disabled_badge = ctk.CTkLabel(
            self,
            text="",
            width=82,
            height=26,
            corner_radius=12,
            fg_color="transparent",
            text_color="#aeb8cc",
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self.disabled_badge.grid(row=0, column=7, padx=4, pady=8)

        self.delete_btn = ctk.CTkButton(
            self,
            text="✕",
            width=34,
            fg_color="#a54f58",
            hover_color="#bf616a",
            command=self.delete,
        )
        self.delete_btn.grid(row=0, column=8, padx=(4, 10), pady=8)

        self.apply_visual_state()

    def _entry(self, key: str, value: str, col: int, placeholder: str, width: int = 110):
        ent = ctk.CTkEntry(self, placeholder_text=placeholder, width=width)
        ent.insert(0, value)
        ent.grid(row=0, column=col, padx=4, pady=8, sticky="ew")
        ent.bind("<KeyRelease>", lambda _e: self.app.save_config_debounced())
        ent.bind("<FocusOut>", lambda _e: self.save())
        self.entries[key] = ent

    def on_enabled_changed(self):
        self.save()
        self.apply_visual_state(parent_enabled=self.parent_enabled)

    def save(self):
        section = f"EXPORT:{self.export_name}"
        if section not in self.app.config:
            self.app.config[section] = {}
        sec = self.app.config[section]
        sec["enabled"] = str(self.enabled_var.get()).lower()
        sec["send_enabled"] = str(self.send_var.get()).lower()
        sec["workbook"] = self.workbook_key  # hidden mapping; do not show in UI
        sec["sheet"] = self.entries["sheet"].get().strip()
        sec["range"] = self.entries["range"].get().strip()
        sec["file"] = self.entries["file"].get().strip()
        sec["delete_by_start"] = str(self.delete_var.get()).lower()
        self.app.save_config()
        self.apply_visual_state(parent_enabled=self.parent_enabled)

    def apply_visual_state(self, parent_enabled: bool = True):
        """Visually and functionally disable this row when Use is off, workbook is off, or app is running."""
        self.parent_enabled = parent_enabled
        row_enabled = bool(self.enabled_var.get())
        runtime_locked = bool(getattr(self.app, "runtime_locked", False))
        active = row_enabled and parent_enabled and not runtime_locked

        if active:
            row_bg = "#3b4252"
            entry_bg = "#434c5e"
            entry_text = "#eceff4"
            muted_text = "#d8dee9"
            no_bg = "#434c5e"
            no_text = "#e5e9f0"
            badge_text = ""
            badge_bg = "transparent"
        else:
            row_bg = "#2e3440"
            entry_bg = "#3b4252"
            entry_text = "#7b8496"
            muted_text = "#7b8496"
            no_bg = "#4c566a"
            no_text = "#aeb8cc"
            if runtime_locked:
                badge_text = "LOCKED"
                badge_bg = "#434c5e"
            else:
                badge_text = "DISABLED"
                badge_bg = "#43353a" if parent_enabled else "#434c5e"

        self.configure(fg_color=row_bg)
        self.no_box.configure(fg_color=no_bg, text_color=no_text)
        self.disabled_badge.configure(text=badge_text, fg_color=badge_bg, text_color="#d3868e" if parent_enabled else "#aeb8cc")

        # If workbook is disabled or app is running, even the row Use checkbox should look locked.
        self.use_chk.configure(state="normal" if (parent_enabled and not runtime_locked) else "disabled", text_color=muted_text)

        for ent in self.entries.values():
            ent.configure(
                state="normal" if active else "disabled",
                fg_color=entry_bg,
                text_color=entry_text,
                placeholder_text_color="#5e6779" if not active else "#7b8496",
            )

        for widget in [self.delete_chk, self.send_chk]:
            widget.configure(state="normal" if active else "disabled", text_color=muted_text)

        # Keep delete button available when merely disabled, but lock it while running.
        self.delete_btn.configure(
            state="disabled" if runtime_locked else "normal",
            fg_color="#a54f58" if active else "#43353a",
            hover_color="#bf616a" if active else "#43353a",
            text_color="#ecd9db" if active else "#aeb8cc",
        )

    def delete(self):
        if messagebox.askyesno("Delete sheet", f"ลบรายการเลข {self.export_name} ใช่ไหม?"):
            self.app.delete_export(self.export_name)

class WorkbookCard(ctk.CTkFrame):
    def __init__(self, master, app, workbook_key: str):
        super().__init__(master, fg_color="#323847", corner_radius=18)
        self.app = app
        self.workbook_key = workbook_key
        self.sheet_rows: List[SheetRow] = []

        sec = self.app.config[f"WORKBOOK:{workbook_key}"]
        self.enabled_var = ctk.BooleanVar(value=as_bool(sec.get("enabled", "true"), True))
        self.send_excel_var = ctk.BooleanVar(value=as_bool(sec.get("send_excel", "false"), False))
        display = sec.get("display_name", workbook_key)
        path = sec.get("path", "")

        self.grid_columnconfigure(0, weight=1)

        self.header = ctk.CTkFrame(self, fg_color="#3b4252", corner_radius=14)
        self.header.grid(row=0, column=0, padx=12, pady=(12, 6), sticky="ew")
        self.header.grid_columnconfigure(2, weight=1)

        self.use_chk = ctk.CTkCheckBox(self.header, text="Use", variable=self.enabled_var, command=self.save_workbook, width=58)
        self.use_chk.grid(row=0, column=0, padx=(12, 6), pady=10)

        self.icon_label = ctk.CTkLabel(self.header, text="📘", font=ctk.CTkFont(size=22))
        self.icon_label.grid(row=0, column=1, padx=4, pady=10)

        self.title_label = ctk.CTkLabel(self.header, text=display, font=ctk.CTkFont(size=16, weight="bold"), text_color="#eceff4")
        self.title_label.grid(row=0, column=2, padx=4, pady=(8, 0), sticky="w")

        self.path_editing = False
        self.path_entry = None
        self.path_label = ctk.CTkLabel(
            self.header,
            text=path,
            text_color="#aeb8cc",
            anchor="w",
            cursor="hand2",
        )
        self.path_label.grid(row=1, column=2, padx=4, pady=(0, 8), sticky="ew")
        self.path_label.bind("<Double-Button-1>", lambda _e: self.start_edit_path())
        self.path_label.bind("<Enter>", lambda _e: self.path_label.configure(text_color="#88c0d0"))
        self.path_label.bind("<Leave>", lambda _e: self.path_label.configure(text_color="#aeb8cc" if not getattr(self.app, "runtime_locked", False) and self.enabled_var.get() else "#5e6779"))

        self.send_excel_chk = ctk.CTkCheckBox(
            self.header,
            text="Send Excel",
            variable=self.send_excel_var,
            command=self.save_workbook,
            width=104,
        )
        self.send_excel_chk.grid(row=0, column=3, rowspan=2, padx=(8, 4), pady=10)

        self.workbook_badge = ctk.CTkLabel(
            self.header,
            text="ENABLED",
            width=86,
            height=28,
            corner_radius=14,
            fg_color="#3b4a3e",
            text_color="#a3be8c",
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self.workbook_badge.grid(row=0, column=4, rowspan=2, padx=(4, 4), pady=10)

        self.add_sheet_btn = ctk.CTkButton(
            self.header,
            text="+ Add Sheet",
            width=116,
            fg_color="#5e81ac",
            hover_color="#4c6e93",
            command=self.add_sheet,
        )
        self.add_sheet_btn.grid(row=0, column=5, rowspan=2, padx=8, pady=10)

        self.remove_btn = ctk.CTkButton(
            self.header,
            text="Remove",
            width=86,
            fg_color="#a54f58",
            hover_color="#bf616a",
            command=self.delete_workbook,
        )
        self.remove_btn.grid(row=0, column=6, rowspan=2, padx=(0, 12), pady=10)

        labels = ctk.CTkFrame(self, fg_color="transparent")
        labels.grid(row=1, column=0, padx=16, pady=(4, 0), sticky="ew")
        for i, (txt, width) in enumerate([("", 56), ("No.", 64), ("Sheet", 120), ("Range", 110), ("Output File", 120), ("", 74), ("", 64), ("Status", 82)]):
            labels.grid_columnconfigure(i, weight=1 if i in [2, 3, 4] else 0)
            ctk.CTkLabel(labels, text=txt, text_color="#aeb8cc", width=width, anchor="w").grid(row=0, column=i, padx=4, sticky="ew")

        self.rows_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.rows_frame.grid(row=2, column=0, padx=12, pady=(0, 12), sticky="ew")
        self.rows_frame.grid_columnconfigure(0, weight=1)
        self.reload_sheets()
        self.apply_visual_state()

    def save_workbook(self):
        sec = self.app.config[f"WORKBOOK:{self.workbook_key}"]
        sec["enabled"] = str(self.enabled_var.get()).lower()
        sec["send_excel"] = str(self.send_excel_var.get()).lower()
        self.app.save_config()
        self.apply_visual_state()

    def start_edit_path(self):
        """Allow changing only the workbook path by double-clicking the path text."""
        if getattr(self.app, "runtime_locked", False) or self.path_editing:
            return

        sec = self.app.config[f"WORKBOOK:{self.workbook_key}"]
        current_path = sec.get("path", "").strip()

        self.path_editing = True
        self.path_label.grid_remove()

        edit_frame = ctk.CTkFrame(self.header, fg_color="transparent")
        edit_frame.grid(row=1, column=2, padx=4, pady=(0, 8), sticky="ew")
        edit_frame.grid_columnconfigure(0, weight=1)

        self.path_entry = ctk.CTkEntry(
            edit_frame,
            fg_color="#434c5e",
            border_color="#88c0d0",
            text_color="#eceff4",
            height=28,
        )
        self.path_entry.insert(0, current_path)
        self.path_entry.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        save_btn = ctk.CTkButton(
            edit_frame,
            text="Save",
            width=58,
            height=28,
            fg_color="#a3be8c",
            hover_color="#8ca876",
            command=lambda: self.finish_edit_path(edit_frame, save=True),
        )
        save_btn.grid(row=0, column=1, padx=(0, 4))

        browse_btn = ctk.CTkButton(
            edit_frame,
            text="Browse",
            width=72,
            height=28,
            fg_color="#4c566a",
            hover_color="#5e6779",
            command=self.browse_edit_path,
        )
        browse_btn.grid(row=0, column=2, padx=(0, 4))

        cancel_btn = ctk.CTkButton(
            edit_frame,
            text="Cancel",
            width=66,
            height=28,
            fg_color="#5e6779",
            hover_color="#7b8496",
            command=lambda: self.finish_edit_path(edit_frame, save=False),
        )
        cancel_btn.grid(row=0, column=3)

        self.path_entry.bind("<Return>", lambda _e: self.finish_edit_path(edit_frame, save=True))
        self.path_entry.bind("<Escape>", lambda _e: self.finish_edit_path(edit_frame, save=False))
        self.path_entry.focus_set()
        self.path_entry.select_range(0, "end")

    def browse_edit_path(self):
        path = filedialog.askopenfilename(
            filetypes=[("Excel files", "*.xlsx *.xlsm *.xlsb *.xls"), ("All files", "*.*")]
        )
        if path and self.path_entry is not None:
            self.path_entry.delete(0, "end")
            self.path_entry.insert(0, os.path.abspath(path))

    def finish_edit_path(self, edit_frame, save: bool):
        new_path = self.path_entry.get().strip() if (save and self.path_entry is not None) else None

        try:
            edit_frame.destroy()
        except Exception:
            pass

        self.path_entry = None
        self.path_editing = False

        if save and new_path is not None:
            sec = self.app.config[f"WORKBOOK:{self.workbook_key}"]
            old_path = sec.get("path", "").strip()
            if new_path != old_path:
                sec["path"] = os.path.abspath(new_path) if new_path else ""
                # Keep the card title synced with the selected Excel file name.
                if new_path:
                    sec["display_name"] = os.path.basename(new_path)
                    self.title_label.configure(text=os.path.basename(new_path))
                self.app.save_config()
                if hasattr(self.app, "write_log"):
                    self.app.write_log(f"Workbook path updated: {os.path.basename(new_path) if new_path else self.workbook_key}")

        text = self.app.config[f"WORKBOOK:{self.workbook_key}"].get("path", "")
        self.path_label.configure(text=text)
        self.path_label.grid(row=1, column=2, padx=4, pady=(0, 8), sticky="ew")
        self.apply_visual_state()

    def apply_visual_state(self):
        runtime_locked = bool(getattr(self.app, "runtime_locked", False))
        active = bool(self.enabled_var.get()) and not runtime_locked

        self.use_chk.configure(state="disabled" if runtime_locked else "normal")
        self.send_excel_chk.configure(
            state="disabled" if runtime_locked else "normal",
            text_color="#d8dee9" if active else "#7b8496",
        )

        if active:
            self.configure(fg_color="#323847")
            self.header.configure(fg_color="#3b4252")
            self.title_label.configure(text_color="#eceff4")
            self.path_label.configure(text_color="#aeb8cc")
            self.icon_label.configure(text="📘", text_color="#eceff4")
            self.workbook_badge.configure(text="ENABLED", fg_color="#3b4a3e", text_color="#a3be8c")
            self.add_sheet_btn.configure(state="normal", fg_color="#5e81ac", hover_color="#4c6e93", text_color="#eceff4")
            self.remove_btn.configure(state="normal", fg_color="#a54f58", hover_color="#bf616a", text_color="#ecd9db")
        else:
            self.configure(fg_color="#272c36")
            self.header.configure(fg_color="#2e3440")
            self.title_label.configure(text_color="#7b8496")
            self.path_label.configure(text_color="#5e6779")
            self.icon_label.configure(text="📕", text_color="#7b8496")
            if runtime_locked:
                self.workbook_badge.configure(text="LOCKED", fg_color="#434c5e", text_color="#d8dee9")
            else:
                self.workbook_badge.configure(text="DISABLED", fg_color="#43353a", text_color="#d3868e")
            self.add_sheet_btn.configure(state="disabled", fg_color="#434c5e", hover_color="#434c5e", text_color="#7b8496")
            self.remove_btn.configure(state="disabled" if runtime_locked else "normal", fg_color="#43353a", hover_color="#43353a", text_color="#aeb8cc")

        for row in self.sheet_rows:
            row.apply_visual_state(parent_enabled=active)

    def export_names_for_workbook(self) -> List[str]:
        names = [x.strip() for x in self.app.config["EXPORTS"].get("items", "").split(",") if x.strip()]
        return [name for name in names if self.app.config.get(f"EXPORT:{name}", "workbook", fallback="") == self.workbook_key]

    def reload_sheets(self):
        for row in self.sheet_rows:
            row.destroy()
        self.sheet_rows.clear()
        for idx, name in enumerate(self.export_names_for_workbook()):
            row = SheetRow(self.rows_frame, self.app, self.workbook_key, name)
            row.grid(row=idx, column=0, padx=4, pady=5, sticky="ew")
            self.sheet_rows.append(row)
        # Apply workbook-level disabled state after rows are rebuilt.
        if hasattr(self, "enabled_var"):
            self.apply_visual_state()

    def add_sheet(self):
        if not self.enabled_var.get() or getattr(self.app, "runtime_locked", False):
            return
        self.app.add_export_for_workbook(self.workbook_key)

    def delete_workbook(self):
        if messagebox.askyesno("Remove Excel file", f"ลบไฟล์ Excel นี้ และรายการ Sheet/Export ทั้งหมดของไฟล์นี้ใช่ไหม?"):
            self.app.delete_workbook(self.workbook_key)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        apply_nord_theme()

        self.title(APP_TITLE)

        # Fixed War Room size.
        # Reason: the dashboard needs enough vertical space for Metrics, Scheduler,
        # Battle Flow and Live Log to be visible together. 1280x820 is the tested
        # compact command-center size for 100% Windows scaling.
        self.window_width = 1280
        self.window_height = 900
        self.geometry(f"{self.window_width}x{self.window_height}")
        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = max(0, (screen_w - self.window_width) // 2)
        y = max(0, (screen_h - self.window_height) // 2)
        self.geometry(f"{self.window_width}x{self.window_height}+{x}+{y}")
        self.minsize(self.window_width, self.window_height)
        self.maxsize(self.window_width, self.window_height)
        self.resizable(False, False)
        self.ui_thread_id = threading.get_ident()

        self.config = configparser.ConfigParser()
        self.config.read(CONFIG_FILE, encoding="utf-8")
        self.scheduler_run_minute = 5
        self.scheduler_start_hour = 15
        self.scheduler_end_hour = 12
        self.auto_run_settings = None
        self.active_run_settings = None
        self.ensure_config()

        self.task_queue = queue.Queue()
        self.worker_running = True
        self.running = False
        self.current_run_source = None
        self.run_generation = 0
        self.run_state_lock = threading.Lock()
        self.scheduler_running = False
        self.last_run_minute = None
        self.mode = None
        self.last_activity = time.time()
        self._save_job = None
        self.workbook_cards: List[WorkbookCard] = []
        self.runtime_locked = False
        self.runtime_lock_banner = None
        self.nav_drag_key = None
        self.nav_drag_started = False
        self.nav_drag_start_y = 0
        self.lockable_inputs = []
        self.lockable_buttons = []
        self.button_color_map = {}
        self.stop_requested = False
        self.next_run = None
        self.controller_clients = []
        self.dynamic_plans = []
        self.bot_running = False
        self.jms_running = False
        self.processed_events = deque(maxlen=1000)
        self.event_lock = threading.Lock()
        self.log_flush_lock = threading.Lock()
        self.log_buffers = {"main": deque(), "controller": deque(), "jms": deque()}
        self.log_flush_pending = {"main": False, "controller": False, "jms": False}
        self.log_flush_delay_ms = 3000
        self.watchdog_warn_seconds = 1800
        self.watchdog_timeout_seconds = 0
        self.watchdog_warning_interval_seconds = 1800
        self.watchdog_last_warning_at = 0
        self.system_alert_last_sent = {}
        self.log_flush_batch_size = 200
        self.bot_thread = None
        self.load_controller_clients()
        self.load_dynamic_plans()

        self.build_ui()
        self.load_values_to_ui()

        threading.Thread(target=self.worker_loop, daemon=True).start()
        threading.Thread(target=self.watchdog, daemon=True).start()
        self.start_controller_api()
        self.after(1000, self.update_clock)
        self.after(2500, self.refresh_status)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def ensure_config(self):
        for section, defaults in [("PATH", DEFAULT_PATH), ("FEISHU", DEFAULT_FEISHU), ("TIME", DEFAULT_TIME), ("UI", DEFAULT_UI), ("DWS_JMS", DEFAULT_DWS_JMS)]:
            if section not in self.config:
                self.config[section] = {}
            for k, v in defaults.items():
                if not self.config[section].get(k):
                    self.config[section][k] = v
        # Clean old retired Feishu keys from previous versions.
        for old_key in (("WEB" + chr(72) + "OOK"), "SECRET"):
            if "FEISHU" in self.config and old_key in self.config["FEISHU"]:
                self.config.remove_option("FEISHU", old_key)
        for name, (ip, port) in DEFAULT_CONTROLLER_CLIENTS.items():
            if name not in self.config:
                self.config[name] = {}
            if not self.config[name].get("IP"):
                self.config[name]["IP"] = ip
            if not self.config[name].get("PORT"):
                self.config[name]["PORT"] = port
        if "WORKBOOKS" not in self.config:
            self.config["WORKBOOKS"] = {"items": ""}
        if "EXPORTS" not in self.config:
            self.config["EXPORTS"] = {"items": ""}
        migrate_old_export_config(self.config)
        self.normalize_export_names_to_numbers()
        self.save_config()

    def normalize_export_names_to_numbers(self):
        """Make export names user-friendly numeric IDs: 1, 2, 3...
        This only renames config sections. Workbook mapping, sheet, range, file, delete/send flags stay unchanged.
        """
        if "EXPORTS" not in self.config:
            self.config["EXPORTS"] = {"items": ""}
            return

        old_names = [x.strip() for x in self.config["EXPORTS"].get("items", "").split(",") if x.strip()]
        if not old_names:
            return

        # Already clean numeric sequence: keep as-is.
        if old_names == [str(i) for i in range(1, len(old_names) + 1)]:
            return

        snapshots = []
        for old_name in old_names:
            section = f"EXPORT:{old_name}"
            if section not in self.config:
                continue
            snapshots.append(dict(self.config[section]))
            self.config.remove_section(section)

        new_names = []
        for idx, values in enumerate(snapshots, start=1):
            new_name = str(idx)
            new_names.append(new_name)
            section = f"EXPORT:{new_name}"
            self.config[section] = values
            # Keep existing output filename if it exists; otherwise create a clear default.
            if not self.config[section].get("file"):
                self.config[section]["file"] = f"export_{new_name}.png"

        self.config["EXPORTS"]["items"] = ",".join(new_names)

    def save_config(self):
        if hasattr(self, "out_entry"):
            self.config["PATH"]["output_dir"] = entry_value(self.out_entry)
            self.config["TIME"]["run_minute"] = clean_input_value(self.minute_var.get()) or "5"
            self.config["TIME"]["start_hour"] = clean_input_value(self.start_hour_var.get()).replace(":00", "") or "15"
            self.config["TIME"]["end_hour"] = clean_input_value(self.end_hour_var.get()).replace(":00", "") or "12"
            self.config["FEISHU"]["APP_ID"] = entry_value(self.app_id_entry, collapse_internal_spaces=True)
            self.config["FEISHU"]["APP_SECRET"] = entry_value(self.app_secret_entry, collapse_internal_spaces=True)
            if hasattr(self, "chat_id_entry"):
                self.config["FEISHU"]["CHAT_ID"] = entry_value(self.chat_id_entry, collapse_internal_spaces=True)
            legacy_jms_token = ""
            if "DWS_JMS" in self.config:
                legacy_jms_token = clean_input_value(self.config["DWS_JMS"].get("jms_token", ""), collapse_internal_spaces=True)
            for attr, key in [
                ("bot_port_entry", "BOT_PORT"),
                ("bot_name_entry", "BOT_NAME"),
                ("verify_token_entry", "VERIFY_TOKEN"),
                ("ngrok_url_entry", "NGROK_URL"),
                ("auth_token_entry", "JMS_TOKEN"),
            ]:
                if hasattr(self, attr):
                    self.config["FEISHU"][key] = entry_value(
                        getattr(self, attr),
                        collapse_internal_spaces=("TOKEN" in key or key in {"BOT_PORT"}),
                    )
            if not self.config["FEISHU"].get("JMS_TOKEN") and legacy_jms_token:
                self.config["FEISHU"]["JMS_TOKEN"] = legacy_jms_token
            self.config["FEISHU"]["AUTH_TOKEN"] = self.config["FEISHU"].get("JMS_TOKEN", "")
            if hasattr(self, "controller_client_rows"):
                for row in self.controller_client_rows:
                    section = row["name"]
                    if section not in self.config:
                        self.config[section] = {}
                    self.config[section]["IP"] = clean_input_value(row["ip_entry"].get())
                    self.config[section]["PORT"] = clean_input_value(row["port_entry"].get(), collapse_internal_spaces=True)
            for old_key in (("WEB" + chr(72) + "OOK"), "SECRET"):
                self.config.remove_option("FEISHU", old_key)
            if "DWS_JMS" not in self.config:
                self.config["DWS_JMS"] = {}
            if hasattr(self, "raw_path_entry"):
                self.config["DWS_JMS"]["raw_path"] = entry_value(self.raw_path_entry)
            if hasattr(self, "bot_export_var"):
                self.config["DWS_JMS"]["enabled"] = str(self.bot_export_var.get()).lower()
            elif hasattr(self, "dws_enable_var"):
                self.config["DWS_JMS"]["enabled"] = str(self.dws_enable_var.get()).lower()
            if hasattr(self, "bot_chat_var"):
                self.config["DWS_JMS"]["bot_chat_enabled"] = str(self.bot_chat_var.get()).lower()
            for _attr, _key in [
                ("db_host_entry", "db_host"),
                ("db_port_entry", "db_port"),
                ("db_user_entry", "db_user"),
                ("db_password_entry", "db_password"),
                ("db_name_entry", "db_name"),
            ]:
                if hasattr(self, _attr):
                    self.config["DWS_JMS"][_key] = entry_value(getattr(self, _attr), collapse_internal_spaces=True)
            self.config["DWS_JMS"]["jms_token"] = self.get_jms_auth_token()
            for _attr, _key, _default in [
                ("name_dws", "name_dws", "DWS9-11.xlsx"),
                ("name_auto", "name_auto", "DWSXAUTOPDA.xlsx"),
                ("name_dwspda", "name_dwspda", "DWSPDA.xlsx"),
                ("name_realtime_db", "name_realtime_db", "RealtimeDB.xlsx"),
            ]:
                if hasattr(self, _attr):
                    self.config["DWS_JMS"][_key] = entry_value(getattr(self, _attr)) or _default
                elif not self.config["DWS_JMS"].get(_key):
                    self.config["DWS_JMS"][_key] = _default
            for _attr, _key in [
                ("send_dws_file_var", "send_dws_file"),
                ("send_auto_file_var", "send_auto_file"),
                ("send_dwspda_file_var", "send_dwspda_file"),
                ("send_realtime_file_var", "send_realtime_file"),
            ]:
                if hasattr(self, _attr):
                    self.config["DWS_JMS"][_key] = str(getattr(self, _attr).get()).lower()
            self.config["DWS_JMS"]["start_date"] = self.start_date.get()
            self.config["DWS_JMS"]["end_date"] = self.end_date.get()
            self.config["DWS_JMS"]["start_hour"] = self.start_hour.get()
            self.config["DWS_JMS"]["end_hour"] = self.end_hour.get()
        save_config(self.config)
        self.refresh_scheduler_snapshot()
        if hasattr(self, "controller_client_rows"):
            self.load_controller_clients()
        self.sync_auto_run_settings_after_save()

    def save_config_debounced(self):
        if self._save_job:
            self.after_cancel(self._save_job)
        self._save_job = self.after(450, self.save_all)

    def save_all(self):
        for card in self.workbook_cards:
            card.save_workbook()
            for row in card.sheet_rows:
                row.save()
        self.save_config()

    def parse_hour_value(self, value, default: int) -> int:
        try:
            text = str(value if value is not None else default).strip()
            if ":" in text:
                text = text.split(":", 1)[0]
            return max(0, min(23, int(text)))
        except Exception:
            return default

    def parse_minute_value(self, value, default: int = 5) -> int:
        try:
            return max(0, min(59, int(str(value if value is not None else default).strip())))
        except Exception:
            return default

    def refresh_scheduler_snapshot(self):
        time_config = self.config["TIME"] if "TIME" in self.config else {}
        minute_raw = time_config.get("run_minute", "5")
        start_raw = time_config.get("start_hour", "15")
        end_raw = time_config.get("end_hour", "12")
        if self.is_ui_thread():
            if "minute_var" in self.__dict__:
                minute_raw = self.minute_var.get()
            if "start_hour_var" in self.__dict__:
                start_raw = self.start_hour_var.get()
            if "end_hour_var" in self.__dict__:
                end_raw = self.end_hour_var.get()
        self.scheduler_run_minute = self.parse_minute_value(minute_raw, 5)
        self.scheduler_start_hour = self.parse_hour_value(start_raw, 15)
        self.scheduler_end_hour = self.parse_hour_value(end_raw, 12)
        return self.scheduler_run_minute, self.scheduler_start_hour, self.scheduler_end_hour

    def get_next_scheduler_run_time(self, now: Optional[datetime] = None) -> datetime:
        self.refresh_scheduler_snapshot()
        now = now or datetime.now()
        minute = self.scheduler_run_minute
        now_key = now.strftime("%Y-%m-%d %H:%M")
        if now.minute < minute:
            return now.replace(minute=minute, second=0, microsecond=0)
        if now.minute == minute and self.last_run_minute != now_key:
            return now.replace(second=0, microsecond=0)
        next_hour = now + timedelta(hours=1)
        return next_hour.replace(minute=minute, second=0, microsecond=0)

    def format_next_scheduler_run(self, next_run: Optional[datetime] = None) -> str:
        next_run = next_run or self.get_next_scheduler_run_time()
        return next_run.strftime("%Y-%m-%d %H:%M")

    def set_next_run_display(self, next_run: Optional[datetime] = None, clear: bool = False):
        if not self.is_ui_thread():
            self.run_on_ui_thread(self.set_next_run_display, next_run, clear)
            return
        if clear:
            self.next_run = None
            text = "Next auto run: -"
        else:
            self.next_run = next_run or self.get_next_scheduler_run_time()
            text = f"Next auto run: {self.format_next_scheduler_run(self.next_run)}"
        label = getattr(self, "next_run_label", None)
        if label is not None:
            label.configure(text=text)

    def get_bool_var_value(self, attr: str, default: bool = False) -> bool:
        var = self.__dict__.get(attr)
        if var is None or not hasattr(var, "get"):
            return default
        return bool(var.get())

    def build_run_settings_snapshot(self, out):
        self.refresh_scheduler_snapshot()
        dws_config = self.config["DWS_JMS"] if "DWS_JMS" in self.config else {}
        feishu = self.config["FEISHU"] if "FEISHU" in self.config else {}
        path_cfg = self.config["PATH"] if "PATH" in self.config else {}

        if self.is_ui_thread() and "auth_token_entry" in self.__dict__:
            jms_token = entry_value(self.auth_token_entry, collapse_internal_spaces=True)
        else:
            jms_token = clean_input_value(feishu.get("JMS_TOKEN", ""), collapse_internal_spaces=True)
        if not jms_token:
            jms_token = clean_input_value(feishu.get("AUTH_TOKEN", ""), collapse_internal_spaces=True)
        if not jms_token:
            jms_token = clean_input_value(dws_config.get("jms_token", ""), collapse_internal_spaces=True)

        if self.is_ui_thread() and "raw_path_entry" in self.__dict__ and entry_value(self.raw_path_entry):
            raw_folder = entry_value(self.raw_path_entry)
        else:
            raw_folder = clean_input_value(dws_config.get("raw_path", "")) or clean_input_value(path_cfg.get("output_dir", ""))

        if self.is_ui_thread() and "chat_id_entry" in self.__dict__:
            chat_id = entry_value(self.chat_id_entry, collapse_internal_spaces=True)
        else:
            chat_id = clean_input_value(feishu.get("CHAT_ID", ""), collapse_internal_spaces=True)

        app_id = entry_value(self.app_id_entry, collapse_internal_spaces=True) if "app_id_entry" in self.__dict__ else ""
        app_secret = entry_value(self.app_secret_entry, collapse_internal_spaces=True) if "app_secret_entry" in self.__dict__ else ""
        send_dws_var = self.__dict__.get("send_dws_file_var")
        send_auto_var = self.__dict__.get("send_auto_file_var")
        send_dwspda_var = self.__dict__.get("send_dwspda_file_var")
        send_realtime_var = self.__dict__.get("send_realtime_file_var")

        def snapshot_send_flag(var, key: str) -> bool:
            if var is not None and hasattr(var, "get"):
                return bool(var.get())
            return as_bool(dws_config.get(key, "false"), False)

        def snapshot_filename(key: str, default: str) -> str:
            return clean_input_value(dws_config.get(key, "")) or default

        return {
            "out": out,
            "run_export": self.get_bool_var_value("bot_export_var", True),
            "run_chat": self.get_bool_var_value("bot_chat_var", True),
            "raw_folder": raw_folder,
            "db_config": self.get_dws_db_config(),
            "dws_names": {
                "name_dws": snapshot_filename("name_dws", "DWS9-11.xlsx"),
                "name_auto": snapshot_filename("name_auto", "DWSXAUTOPDA.xlsx"),
                "name_dwspda": snapshot_filename("name_dwspda", "DWSPDA.xlsx"),
                "name_realtime_db": snapshot_filename("name_realtime_db", "RealtimeDB.xlsx"),
            },
            "dws_send_files": {
                "send_dws_file": snapshot_send_flag(send_dws_var, "send_dws_file"),
                "send_auto_file": snapshot_send_flag(send_auto_var, "send_auto_file"),
                "send_dwspda_file": snapshot_send_flag(send_dwspda_var, "send_dwspda_file"),
                "send_realtime_file": snapshot_send_flag(send_realtime_var, "send_realtime_file"),
            },
            "jms_token": jms_token,
            "chat_id": chat_id,
            "app_id": app_id,
            "app_secret": app_secret,
        }

    def sync_auto_run_settings_after_save(self):
        if not getattr(self, "scheduler_running", False):
            return
        if not self.is_ui_thread():
            self.run_on_ui_thread(self.sync_auto_run_settings_after_save)
            return
        run_chat = self.get_bool_var_value("bot_chat_var", True)
        out = entry_value(self.out_entry) if run_chat else self.get_raw_export_folder()
        self.auto_run_settings = self.build_run_settings_snapshot(out)

    def prepare_run_settings(self):
        if not self.is_ui_thread():
            self.write_log("Run preparation must happen on UI thread", level="ERROR")
            return None
        out = self.validate_ready()
        if not out:
            return None
        self.save_all()
        return self.build_run_settings_snapshot(out)

    def is_ui_thread(self):
        return threading.get_ident() == getattr(self, "ui_thread_id", None)

    def run_on_ui_thread(self, callback, *args, **kwargs):
        if self.is_ui_thread():
            return callback(*args, **kwargs)
        try:
            self.after(0, lambda: callback(*args, **kwargs))
        except Exception:
            pass
        return None

    def build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self, width=230, corner_radius=0, fg_color="#272c36")
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(9, weight=1)

        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, padx=20, pady=(26, 22), sticky="ew")
        ctk.CTkLabel(brand, text="Auto Report", font=ctk.CTkFont(size=26, weight="bold"), text_color="#eceff4").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(brand, text="Enterprise Console", text_color="#aeb8cc", font=ctk.CTkFont(size=13)).grid(row=1, column=0, pady=(4, 0), sticky="w")

        self.nav_parent = ctk.CTkFrame(sidebar, fg_color="transparent")
        self.nav_parent.grid(row=2, column=0, padx=18, pady=0, sticky="ew")
        self.nav_parent.grid_columnconfigure(0, weight=1)
        self.nav_buttons = {}
        self.nav_rows = {}
        self.nav_items = {
            "home": ("⌂  BOT REPORT", "nav_home"),
            "workbooks": ("▣  ไฟล์ Excel", "nav_workbooks"),
            "data_export": ("⇩  DATA EXPORT", "nav_data_export"),
            "dws_plan": ("▦  BOT DWS PLAN", "nav_dws_plan"),
            "jms_user": ("👤  BOT JMS USER", "nav_jms_user"),
            "settings": ("⚙  ตั้งค่า", "nav_settings"),
        }
        self.render_nav_menu()

        self.side_hint = ctk.CTkLabel(sidebar, text="ลำดับงาน: DWS → Excel → Feishu", text_color="#7b8496", wraplength=180, justify="left")
        self.side_hint.grid(row=9, column=0, padx=20, pady=16, sticky="sw")
        self.status_pill = ctk.CTkLabel(sidebar, text="Idle", fg_color="#3b4a3e", text_color="#a3be8c", corner_radius=18, height=36, font=ctk.CTkFont(size=13, weight="bold"))
        self.status_pill.grid(row=10, column=0, padx=18, pady=(8, 20), sticky="ew")

        self.content = ctk.CTkFrame(self, fg_color="#2e3440", corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.pipeline_widgets = {}
        self.pages = {
            "home": self.build_home_page(self.content),
            "workbooks": self.build_workbooks_page(self.content),
            "data_export": self.build_data_export_page(self.content),
            "dws_plan": self.build_dws_plan_page(self.content),
            "jms_user": self.build_jms_user_page(self.content),
            "settings": self.build_settings_page(self.content),
        }
        self.show_page("home")

    def header(self, parent, title, subtitle):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            frame,
            text=title,
            font=ctk.CTkFont(size=30, weight="bold"),
            text_color="#eceff4",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            frame,
            text=subtitle,
            text_color="#aeb8cc",
            font=ctk.CTkFont(size=13),
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        return frame

    def make_card(self, parent, fg="#323847", radius=22):
        return ctk.CTkFrame(parent, fg_color=fg, corner_radius=radius)

    def make_stage(self, parent, col, key, title, icon):
        stage = ctk.CTkFrame(parent, fg_color="#323847", corner_radius=18, border_width=1, border_color="#434c5e", height=108)
        stage.grid(row=0, column=col, padx=8, pady=8, sticky="ew")
        stage.grid_propagate(False)
        stage.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(stage, text=icon, font=ctk.CTkFont(size=22)).grid(row=0, column=0, padx=12, pady=(10, 0))
        ctk.CTkLabel(stage, text=title, text_color="#d8dee9", font=ctk.CTkFont(size=13, weight="bold")).grid(row=1, column=0, padx=12, pady=(0, 2))
        status = ctk.CTkLabel(stage, text="READY", text_color="#aeb8cc", font=ctk.CTkFont(size=11, weight="bold"))
        status.grid(row=2, column=0, padx=12, pady=(0, 8))
        self.pipeline_widgets[key] = {"frame": stage, "status": status}

    def set_pipeline_state(self, key=None, state="ready"):
        if not self.is_ui_thread():
            self.run_on_ui_thread(self.set_pipeline_state, key, state)
            return
        palette = {
            "ready": ("#323847", "#434c5e", "READY", "#aeb8cc"),
            "run": ("#3b4252", "#88c0d0", "RUNNING", "#88c0d0"),
            "ok": ("#3b4a3e", "#a3be8c", "DONE", "#a3be8c"),
            "error": ("#4a3438", "#bf616a", "ERROR", "#d3868e"),
            "skip": ("#434c5e", "#5e6779", "SKIP", "#d8dee9"),
        }
        targets = [key] if key else list(getattr(self, "pipeline_widgets", {}).keys())
        for k in targets:
            w = self.pipeline_widgets.get(k)
            if not w:
                continue
            fg, border, text, color = palette.get(state, palette["ready"])
            w["frame"].configure(fg_color=fg, border_color=border)
            w["status"].configure(text=text, text_color=color)

    def get_scheduler_time_range(self):
        self.refresh_scheduler_snapshot()
        start_hour = self.scheduler_start_hour
        end_hour = self.scheduler_end_hour

        if self.is_ui_thread() and "start_date" in self.__dict__ and "end_date" in self.__dict__:
            try:
                start_dt = datetime.combine(self.start_date.get_date(), datetime.min.time()).replace(hour=start_hour)
                end_dt = datetime.combine(self.end_date.get_date(), datetime.min.time()).replace(hour=end_hour)
                if start_dt > end_dt:
                    start_dt, end_dt = end_dt, start_dt
                return start_dt, end_dt
            except Exception:
                pass

        dws = self.config["DWS_JMS"] if "DWS_JMS" in self.config else {}
        try:
            start_date = datetime.strptime(dws.get("start_date", ""), "%Y-%m-%d").date()
            end_date = datetime.strptime(dws.get("end_date", ""), "%Y-%m-%d").date()
            start_dt = datetime.combine(start_date, datetime.min.time()).replace(hour=start_hour)
            end_dt = datetime.combine(end_date, datetime.min.time()).replace(hour=end_hour)
            if start_dt > end_dt:
                start_dt, end_dt = end_dt, start_dt
            return start_dt, end_dt
        except Exception:
            pass

        now = datetime.now()
        business_date = (now - timedelta(days=1)).date() if now.hour < start_hour else now.date()
        start_dt = datetime.combine(business_date, datetime.min.time()).replace(hour=start_hour)
        end_date = business_date + timedelta(days=1) if start_hour >= end_hour else business_date
        end_dt = datetime.combine(end_date, datetime.min.time()).replace(hour=end_hour)
        return start_dt, end_dt

    def build_home_page(self, master):
        page = ctk.CTkFrame(master, fg_color="#2e3440")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(5, weight=1)
        self.header(page, "BOT REPORT", "รับงาน / ตั้งเวลา / ดูสถานะการทำงานทั้งหมด").grid(row=0, column=0, padx=24, pady=(16, 6), sticky="ew")

        command = self.make_card(page, "#3b4252", 22)
        command.grid(row=1, column=0, padx=24, pady=6, sticky="ew")
        for i in range(10):
            command.grid_columnconfigure(i, weight=1)
        hours = [f"{i:02}:00" for i in range(24)]
        minutes = [str(i) for i in range(60)]
        self.minute_var = ctk.StringVar(value="5")
        self.start_hour_var = ctk.StringVar(value="15:00")
        self.end_hour_var = ctk.StringVar(value="12:00")

        ctk.CTkLabel(command, text="Scheduler", text_color="#eceff4", font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=0, columnspan=2, padx=16, pady=(16, 2), sticky="w")
        ctk.CTkLabel(command, text="Run minute", text_color="#aeb8cc").grid(row=1, column=0, padx=(16, 4), pady=10, sticky="w")
        self.minute_menu = ctk.CTkOptionMenu(command, values=minutes, variable=self.minute_var, width=82, command=lambda _: self.save_config())
        self.minute_menu.grid(row=1, column=1, padx=4, pady=10, sticky="w")
        ctk.CTkLabel(command, text="Start", text_color="#aeb8cc").grid(row=1, column=2, padx=(16, 4), pady=10, sticky="e")
        self.start_date = DateEntry(command, width=18, date_pattern="yyyy-mm-dd", state="readonly", font=("Segoe UI", 12))
        self.start_date.grid(row=1, column=3, padx=4, pady=10, sticky="ew")
        self.start_menu = ctk.CTkOptionMenu(command, values=hours, variable=self.start_hour_var, width=92, command=lambda _: self.save_config())
        self.start_menu.grid(row=1, column=4, padx=4, pady=10, sticky="w")
        ctk.CTkLabel(command, text="→", text_color="#88c0d0", width=24, anchor="center", font=ctk.CTkFont(size=18, weight="bold")).grid(row=1, column=5, padx=4, pady=10)
        ctk.CTkLabel(command, text="End", text_color="#aeb8cc").grid(row=1, column=6, padx=(8, 4), pady=10, sticky="e")
        self.end_date = DateEntry(command, width=18, date_pattern="yyyy-mm-dd", state="readonly", font=("Segoe UI", 12))
        self.end_date.grid(row=1, column=7, padx=4, pady=10, sticky="ew")
        self.end_menu = ctk.CTkOptionMenu(command, values=hours, variable=self.end_hour_var, width=92, command=lambda _: self.save_config())
        self.end_menu.grid(row=1, column=8, padx=(4, 16), pady=10, sticky="w")
        self.start_date.bind("<<DateEntrySelected>>", lambda _e: self.save_config(), "+")
        self.end_date.bind("<<DateEntrySelected>>", lambda _e: self.save_config(), "+")
        self.start_hour = self.start_menu
        self.end_hour = self.end_menu
        self.btn_start = ctk.CTkButton(command, text="▣ Start Auto", height=40, fg_color="#5e81ac", hover_color="#4c6e93", command=self.start_scheduler)
        self.btn_run = ctk.CTkButton(command, text="⚡ Run Now", height=40, fg_color="#a3be8c", hover_color="#8ca876", text_color="#2e3440", command=self.run_once)
        self.btn_stop_auto = ctk.CTkButton(command, text="⛔ Stop Auto", height=40, fg_color="#bf616a", hover_color="#a54f58", command=self.stop_scheduler)
        self.btn_stop_run = ctk.CTkButton(command, text="⛔ Stop Run", height=40, fg_color="#bf616a", hover_color="#a54f58", command=self.stop_process)

        ctk.CTkLabel(command, text="เลือกงานที่จะรัน", text_color="#eceff4", font=ctk.CTkFont(size=17, weight="bold")).grid(row=2, column=0, columnspan=2, padx=16, pady=(8, 2), sticky="w")
        self.bot_export_var = ctk.BooleanVar(value=True)
        self.bot_chat_var = ctk.BooleanVar(value=True)
        # Backward-compatible alias: old code used dws_enable_var to decide raw export.
        self.dws_enable_var = self.bot_export_var
        self.chk_bot_export = ctk.CTkCheckBox(command, text="Bot Export / Raw DWS-JMS", variable=self.bot_export_var, command=self.save_config)
        self.chk_bot_export.grid(row=3, column=0, columnspan=3, padx=(16, 4), pady=(6, 16), sticky="w")
        self.chk_bot_chat = ctk.CTkCheckBox(command, text="Bot Chat / Excel Image + Feishu", variable=self.bot_chat_var, command=self.save_config)
        self.chk_bot_chat.grid(row=3, column=3, columnspan=3, padx=(16, 4), pady=(6, 16), sticky="w")
        self.btn_start.grid(row=3, column=7, padx=8, pady=(6, 16), sticky="ew")
        self.btn_run.grid(row=3, column=8, padx=(8, 16), pady=(6, 16), sticky="ew")

        pipeline = self.make_card(page, "#2e3440", 22)
        pipeline.grid(row=2, column=0, padx=24, pady=6, sticky="ew")
        for i in range(6):
            pipeline.grid_columnconfigure(i, weight=1)
        ctk.CTkLabel(pipeline, text="ลำดับการทำงาน", text_color="#eceff4", font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=0, columnspan=6, padx=16, pady=(14, 0), sticky="w")
        stages = [("dws", "DWS", "📥"), ("jms_auto", "JMS AUTO", "📦"), ("jms_pda", "JMS PDA", "📲"), ("realtime", "Realtime DB", "🧭"), ("excel", "Excel Image", "🖼"), ("feishu", "Feishu", "🚀")]
        stage_wrap = ctk.CTkFrame(pipeline, fg_color="transparent")
        stage_wrap.grid(row=1, column=0, columnspan=6, padx=8, pady=(4, 10), sticky="ew")
        for i in range(6):
            stage_wrap.grid_columnconfigure(i, weight=1)
        for col, (key, title, icon) in enumerate(stages):
            self.make_stage(stage_wrap, col, key, title, icon)

        self.progress = ctk.CTkProgressBar(page, height=14, progress_color="#88c0d0")
        self.progress.grid(row=3, column=0, padx=24, pady=(6, 6), sticky="ew")
        self.progress.set(0)

        self.next_run_label = ctk.CTkLabel(page, text="Next auto run: -", text_color="#88c0d0", anchor="w")
        self.next_run_label.grid(row=4, column=0, padx=24, pady=(0, 6), sticky="ew")

        log_card = self.make_card(page, "#2e3440", 22)
        log_card.grid(row=5, column=0, padx=24, pady=(6, 18), sticky="nsew")
        log_card.grid_rowconfigure(1, weight=1)
        log_card.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(log_card, fg_color="transparent")
        top.grid(row=0, column=0, padx=16, pady=(14, 6), sticky="ew")
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="Live Log", font=ctk.CTkFont(size=16, weight="bold"), text_color="#e5e9f0").grid(row=0, column=0, sticky="w")
        ctk.CTkButton(top, text="Clear", width=70, fg_color="#4c566a", hover_color="#5e6779", command=lambda: self.log_box.delete("1.0", "end")).grid(row=0, column=1, sticky="e")
        self.log_box = ctk.CTkTextbox(log_card, fg_color="#252b36", text_color="#a3be8c", font=("Consolas", 12), corner_radius=12, height=150)
        self.log_box.grid(row=1, column=0, padx=16, pady=(0, 16), sticky="nsew")
        try:
            self.log_box.tag_config("INFO", foreground="#88c0d0")
            self.log_box.tag_config("START", foreground="#8fbcbb")
            self.log_box.tag_config("SUCCESS", foreground="#a3be8c")
            self.log_box.tag_config("WARN", foreground="#ebcb8b")
            self.log_box.tag_config("ERROR", foreground="#d3868e")
        except Exception:
            pass
        return page

    def build_workbooks_page(self, master):
        page = ctk.CTkFrame(master, fg_color="#2e3440")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)
        self.header(page, "ไฟล์ Excel", "เลือกไฟล์ Excel ได้หลายไฟล์ แล้วเพิ่มรายการ Sheet/Export ได้ง่าย ๆ").grid(row=0, column=0, padx=24, pady=(22, 12), sticky="ew")

        tools = ctk.CTkFrame(page, fg_color="#3b4252", corner_radius=16)
        tools.grid(row=1, column=0, padx=24, pady=8, sticky="ew")
        tools.grid_columnconfigure(1, weight=1)
        self.btn_add_excel = ctk.CTkButton(tools, text="＋ เพิ่มไฟล์ Excel", height=38, width=150, fg_color="#5e81ac", hover_color="#4c6e93", command=self.add_workbook_files)
        self.btn_add_excel.grid(row=0, column=0, padx=14, pady=14, sticky="w")
        ctk.CTkLabel(tools, text="เลือกได้หลายไฟล์ในครั้งเดียว | กด + Add Sheet ใต้ไฟล์นั้นเพื่อเพิ่มรายการ Export", text_color="#aeb8cc").grid(row=0, column=1, padx=12, pady=14, sticky="w")
        self.btn_save_workspace = ctk.CTkButton(tools, text="💾 บันทึก", width=92, fg_color="#4c566a", hover_color="#5e6779", command=self.save_all)
        self.btn_save_workspace.grid(row=0, column=2, padx=14, pady=14)

        self.workbook_scroll = ctk.CTkScrollableFrame(page, fg_color="#323847", corner_radius=18)
        self.workbook_scroll.grid(row=2, column=0, padx=24, pady=(8, 24), sticky="nsew")
        self.workbook_scroll.grid_columnconfigure(0, weight=1)
        return page

    def bind_clean_entry(self, ent, collapse_internal_spaces: bool = False, digits_only: bool = False):
        """Clean risky input at UI level and before saving.

        - Token / App Secret / Chat ID: spaces are blocked immediately.
        - Download size: only digits are accepted.
        - Normal text/path fields: trim only when focus leaves or config is saved.
        """
        def sanitize(value: str) -> str:
            text = clean_input_value(value, collapse_internal_spaces=collapse_internal_spaces)
            if digits_only:
                text = re.sub(r"\D+", "", text)
            return text

        def normalize(_event=None):
            cleaned = sanitize(ent.get())
            if ent.get() != cleaned:
                cursor = ent.index("insert")
                ent.delete(0, "end")
                ent.insert(0, cleaned)
                try:
                    ent.icursor(min(cursor, len(cleaned)))
                except Exception:
                    pass
            self.save_config_debounced()

        def on_key_press(event):
            if digits_only:
                allowed = {"BackSpace", "Delete", "Left", "Right", "Home", "End", "Tab", "Return", "Escape"}
                if event.keysym in allowed or (event.state & 0x4 and event.keysym.lower() in {"a", "c", "v", "x"}):
                    return None
                if not event.char.isdigit():
                    return "break"
            if collapse_internal_spaces and event.char and event.char.isspace():
                return "break"
            return None

        ent.bind("<KeyPress>", on_key_press, "+")
        ent.bind("<FocusOut>", normalize, "+")
        ent.bind("<Control-v>", lambda _e: self.after(80, normalize), "+")
        ent.bind("<KeyRelease>", lambda _e: self.after(1, normalize) if (collapse_internal_spaces or digits_only) else self.save_config_debounced(), "+")
        return ent

    def setting_row_wide(self, parent, row, label):
        ctk.CTkLabel(parent, text=label, text_color="#d8dee9", width=150, anchor="w").grid(row=row, column=0, padx=(18, 10), pady=10, sticky="w")
        ent = ctk.CTkEntry(parent, fg_color="#434c5e", border_color="#5e6779")
        ent.grid(row=row, column=1, columnspan=3, padx=(8, 16), pady=10, sticky="ew")
        self.bind_clean_entry(ent, collapse_internal_spaces=("token" in label.lower() or "chat" in label.lower() or "secret" in label.lower() or "app id" in label.lower()), digits_only=("จำนวนรายการ" in label or "download size" in label.lower()))
        self.lockable_inputs.append(ent)
        return ent

    def build_data_export_page(self, master):
        page = ctk.CTkScrollableFrame(master, fg_color="#2e3440", corner_radius=0)
        page.grid_columnconfigure(0, weight=1)
        self.header(page, "DATA EXPORT", "ทดสอบการดึงข้อมูล DWS/JMS แยกตามขั้นตอนโดยใช้ช่วงเวลา Start/End จากหน้า BOT REPORT").grid(row=0, column=0, padx=24, pady=(18, 8), sticky="ew")

        tests = self.make_card(page, "#3b4252", 22)
        tests.grid(row=1, column=0, padx=24, pady=8, sticky="ew")
        tests.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(tests, text="Manual Export Test", text_color="#eceff4", font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=0, padx=16, pady=(16, 2), sticky="w")
        ctk.CTkLabel(tests, text="Run minute เป็นตัวตั้งเวลาทำงานอัตโนมัติ ส่วน Start/End ใช้เป็นช่วงเวลาข้อมูลสำหรับ DWS/JMS", text_color="#aeb8cc").grid(row=1, column=0, padx=16, pady=(0, 8), sticky="w")

        grid = ctk.CTkFrame(tests, fg_color="transparent")
        grid.grid(row=2, column=0, padx=8, pady=6, sticky="ew")
        for i in range(5):
            grid.grid_columnconfigure(i, weight=1)
        cards = [
            ("Full Export", "DWS + JMS Auto + JMS PDA + Realtime", "full", "#b48ead", "#9e7a98"),
            ("DWS", "Download DWS9-11", "dws", "#5e81ac", "#4c6e93"),
            ("JMS Auto", "Export auto scan", "jms_auto", "#8fbcbb", "#7aa5a4"),
            ("JMS PDA", "Export PDA scan", "jms_pda", "#a3be8c", "#8ca876"),
            ("Realtime DB", "Export realtime track", "realtime", "#d08770", "#b87560"),
        ]
        for idx, (title, desc, key, color, hover) in enumerate(cards):
            card = ctk.CTkFrame(grid, fg_color="#323847", corner_radius=18, border_width=1, border_color="#434c5e")
            card.grid(row=0, column=idx, padx=6, pady=6, sticky="nsew")
            card.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(card, text=title, text_color="#eceff4", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, padx=10, pady=(10, 2), sticky="w")
            ctk.CTkLabel(card, text=desc, text_color="#aeb8cc", font=ctk.CTkFont(size=11), wraplength=165).grid(row=1, column=0, padx=10, pady=(0, 8), sticky="w")
            btn = ctk.CTkButton(card, text="RUN", height=32, fg_color=color, hover_color=hover, text_color=readable_on(color), command=lambda mode=key: self.run_dws_jms_task(mode))
            btn.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="ew")
            self.lockable_buttons.append(btn)
            self.button_color_map[btn] = (color, hover)

        btn_stop = ctk.CTkButton(tests, text="STOP DWS/JMS", height=40, fg_color="#bf616a", hover_color="#a54f58", command=self.stop_process)
        btn_stop.grid(row=3, column=0, padx=16, pady=(4, 16), sticky="ew")
        self.button_color_map[btn_stop] = ("#bf616a", "#a54f58")
        return page

    def build_dws_plan_page(self, master):
        page = ctk.CTkFrame(master, fg_color="#2e3440")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        self.header(
            page,
            "BOT DWS PLAN",
            "ควบคุมการเปลี่ยนแพลนและตรวจสถานะเครื่อง DWS จาก bot_main.py",
        ).grid(row=0, column=0, padx=24, pady=(22, 12), sticky="ew")
        body = ctk.CTkFrame(page, fg_color="#2e3440")
        body.grid(row=1, column=0, padx=14, pady=(0, 14), sticky="nsew")
        self.build_controller_dws_tab(body)
        return page

    def build_jms_user_page(self, master):
        page = ctk.CTkFrame(master, fg_color="#2e3440")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        self.header(
            page,
            "BOT JMS USER",
            "เปิด/ปิดการประมวลผลคำสั่งรีรหัสและปลดล็อค user จาก Feishu",
        ).grid(row=0, column=0, padx=24, pady=(22, 12), sticky="ew")
        body = ctk.CTkFrame(page, fg_color="#2e3440")
        body.grid(row=1, column=0, padx=14, pady=(0, 14), sticky="nsew")
        self.build_controller_jms_tab(body)
        return page

    def build_controller_dws_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        control = self.make_card(tab, "#3b4252", 18)
        control.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        control.grid_columnconfigure(4, weight=1)
        self.controller_plan_entry = ctk.CTkEntry(control, placeholder_text="DWSA / DWSB", width=180)
        self.controller_plan_entry.grid(row=0, column=0, padx=(16, 8), pady=14, sticky="w")
        ctk.CTkButton(control, text="Change", width=96, fg_color="#4c566a", hover_color="#5e6779", command=self.change_plan_from_entry).grid(row=0, column=1, padx=6, pady=14)
        ctk.CTkButton(control, text="Refresh Status", width=130, fg_color="#4c566a", hover_color="#5e6779", command=self.refresh_status).grid(row=0, column=2, padx=6, pady=14)
        self.btn_start_bot = ctk.CTkButton(control, text="START BOT", width=120, fg_color="#a3be8c", hover_color="#8ca876", text_color="#2e3440", command=self.start_feishu_bot)
        self.btn_stop_bot = ctk.CTkButton(control, text="STOP BOT", width=120, fg_color="#bf616a", hover_color="#a54f58", command=self.stop_feishu_bot)
        self.btn_start_bot.grid(row=0, column=3, padx=6, pady=14)
        self.bot_status_label = ctk.CTkLabel(control, text="Bot : OFFLINE", text_color="#d3868e", font=ctk.CTkFont(size=13, weight="bold"))
        self.bot_status_label.grid(row=0, column=4, padx=14, pady=14, sticky="w")

        table_card = self.make_card(tab, "#2e3440", 18)
        table_card.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")
        table_card.grid_columnconfigure(0, weight=1)
        table_card.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(table_card, text="DWS Client Status", text_color="#eceff4", font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, padx=14, pady=(12, 6), sticky="w")
        self.controller_status_frame = ctk.CTkScrollableFrame(table_card, fg_color="#252b36", corner_radius=12)
        self.controller_status_frame.grid(row=1, column=0, padx=14, pady=(0, 14), sticky="nsew")
        for col in range(6):
            self.controller_status_frame.grid_columnconfigure(col, weight=1 if col in {1, 5} else 0)

        log_card = self.make_card(tab, "#2e3440", 18)
        log_card.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="nsew")
        log_card.grid_columnconfigure(0, weight=1)
        log_card.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(log_card, fg_color="transparent")
        top.grid(row=0, column=0, padx=14, pady=(12, 6), sticky="ew")
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="Controller Logs", text_color="#eceff4", font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(top, text="Clear", width=70, fg_color="#4c566a", hover_color="#5e6779", command=lambda: self.controller_log_box.delete("1.0", "end")).grid(row=0, column=1, sticky="e")
        self.controller_log_box = ctk.CTkTextbox(log_card, fg_color="#252b36", text_color="#a3be8c", font=("Consolas", 11), corner_radius=12)
        self.controller_log_box.grid(row=1, column=0, padx=14, pady=(0, 14), sticky="nsew")
        self.update_bot_ui()

    def build_controller_jms_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        control = self.make_card(tab, "#3b4252", 18)
        control.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        self.btn_start_jms = ctk.CTkButton(control, text="START BOT", width=120, fg_color="#a3be8c", hover_color="#8ca876", text_color="#2e3440", command=self.start_jms_placeholder)
        self.btn_stop_jms = ctk.CTkButton(control, text="STOP BOT", width=120, fg_color="#bf616a", hover_color="#a54f58", command=self.stop_jms_placeholder)
        self.btn_start_jms.grid(row=0, column=0, padx=(16, 8), pady=14)
        self.jms_status_label = ctk.CTkLabel(control, text="Bot : OFFLINE", text_color="#d3868e", font=ctk.CTkFont(size=13, weight="bold"))
        self.jms_status_label.grid(row=0, column=1, padx=14, pady=14, sticky="w")
        ctk.CTkLabel(control, text="เมื่อเปิด JMS Bot แล้ว คำสั่งรีรหัส/ปลดล็อคจาก Feishu จะถูกประมวลผล", text_color="#aeb8cc").grid(row=0, column=2, padx=14, pady=14, sticky="w")

        log_card = self.make_card(tab, "#2e3440", 18)
        log_card.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")
        log_card.grid_columnconfigure(0, weight=1)
        log_card.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(log_card, fg_color="transparent")
        top.grid(row=0, column=0, padx=14, pady=(12, 6), sticky="ew")
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="JMS Logs", text_color="#eceff4", font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(top, text="Clear", width=70, fg_color="#4c566a", hover_color="#5e6779", command=lambda: self.jms_log_text.delete("1.0", "end")).grid(row=0, column=1, sticky="e")
        self.jms_log_text = ctk.CTkTextbox(log_card, fg_color="#252b36", text_color="#a3be8c", font=("Consolas", 11), corner_radius=12)
        self.jms_log_text.grid(row=1, column=0, padx=14, pady=(0, 14), sticky="nsew")
        self.jms_log("[SYSTEM] JMS TAB READY")
        self.update_jms_ui()

    def render_controller_client_settings(self):
        if not hasattr(self, "controller_client_frame"):
            return
        for widget in self.controller_client_frame.winfo_children():
            widget.destroy()
        self.controller_client_rows.clear()
        headers = ["PC Name", "IP Address", "Port"]
        for col, title in enumerate(headers):
            ctk.CTkLabel(self.controller_client_frame, text=title, text_color="#aeb8cc", font=ctk.CTkFont(weight="bold")).grid(row=0, column=col, padx=8, pady=(8, 4), sticky="w")
        for idx, client in enumerate(self.controller_clients, start=1):
            ctk.CTkLabel(self.controller_client_frame, text=client["name"], text_color="#d8dee9").grid(row=idx, column=0, padx=8, pady=5, sticky="w")
            ip_entry = ctk.CTkEntry(self.controller_client_frame, width=180)
            ip_entry.insert(0, client["ip"])
            ip_entry.grid(row=idx, column=1, padx=8, pady=5, sticky="ew")
            port_entry = ctk.CTkEntry(self.controller_client_frame, width=90)
            port_entry.insert(0, str(client["port"]))
            port_entry.grid(row=idx, column=2, padx=8, pady=5, sticky="w")
            self.bind_clean_entry(ip_entry)
            self.bind_clean_entry(port_entry, collapse_internal_spaces=True, digits_only=True)
            self.controller_client_rows.append({"name": client["name"], "ip_entry": ip_entry, "port_entry": port_entry})
        self.controller_client_frame.grid_columnconfigure(1, weight=1)

    def build_settings_page(self, master):
        page = ctk.CTkScrollableFrame(master, fg_color="#2e3440", corner_radius=0)
        page.grid_columnconfigure(0, weight=1)
        self.header(page, "ตั้งค่า", "รวม config ทั้งหมดไว้ที่นี่: Feishu, DWS/JMS Export, ชื่อไฟล์, DWS PLAN และ JMS USER").grid(row=0, column=0, padx=24, pady=(18, 8), sticky="ew")

        actions = ctk.CTkFrame(page, fg_color="transparent")
        actions.grid(row=1, column=0, padx=24, pady=(0, 8), sticky="ew")
        actions.grid_columnconfigure(0, weight=1)
        self.btn_save_settings = ctk.CTkButton(actions, text="💾 บันทึกการตั้งค่าทั้งหมด", height=40, fg_color="#4c566a", hover_color="#5e6779", command=self.save_all)
        self.btn_save_settings.grid(row=0, column=1, sticky="e")

        path = self.make_card(page, "#3b4252", 22)
        path.grid(row=2, column=0, padx=24, pady=8, sticky="ew")
        path.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(path, text="ตำแหน่งโฟลเดอร์", text_color="#eceff4", font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=0, padx=16, pady=(16, 0), sticky="w")
        self.out_entry = self.path_row(path, 1, "ตำแหน่งเก็บรูป", self.browse_folder)
        self.raw_path_entry = self.path_row(path, 2, "ตำแหน่งเก็บ Excel", self.browse_folder)

        export = self.make_card(page, "#3b4252", 22)
        export.grid(row=3, column=0, padx=24, pady=8, sticky="ew")
        for i in range(4):
            export.grid_columnconfigure(i, weight=1 if i in (1, 3) else 0)
        ctk.CTkLabel(export, text="ตั้งค่า DWS/JMS Export", text_color="#eceff4", font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=0, columnspan=4, padx=18, pady=(16, 4), sticky="w")
        ctk.CTkLabel(export, text="ตั้งค่า JMS_TOKEN และฐานข้อมูล DWS (MySQL)", text_color="#aeb8cc").grid(row=1, column=0, columnspan=4, padx=18, pady=(0, 4), sticky="w")
        self.auth_token_entry = self.setting_row_wide(export, 2, "JMS_TOKEN")
        self.db_host_entry = self.setting_row_wide(export, 3, "DB Host")
        self.db_port_entry = self.setting_row_wide(export, 4, "DB Port")
        self.db_user_entry = self.setting_row_wide(export, 5, "DB User")
        self.db_password_entry = self.setting_row_wide(export, 6, "DB Password")
        self.db_name_entry = self.setting_row_wide(export, 7, "DB Name")

        feishu = self.make_card(page, "#3b4252", 22)
        feishu.grid(row=4, column=0, padx=24, pady=8, sticky="ew")
        feishu.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(feishu, text="ตั้งค่า Bot Feishu", text_color="#eceff4", font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=0, padx=16, pady=(16, 0), sticky="w")
        ctk.CTkLabel(feishu, text="App ID / App Secret ใช้ร่วมกันทั้งส่งรูป, Feishu webhook, DWS PLAN และ JMS USER", text_color="#aeb8cc").grid(row=1, column=0, columnspan=2, padx=16, pady=(4, 6), sticky="w")
        self.app_id_entry = self.setting_row(feishu, 2, "App ID")
        self.app_secret_entry = self.setting_row(feishu, 3, "App Secret")
        self.chat_id_entry = self.setting_row(feishu, 4, "Chat ID")
        self.bot_name_entry = self.setting_row(feishu, 5, "BOT_NAME")
        self.bot_port_entry = self.setting_row(feishu, 6, "BOT_PORT")
        self.verify_token_entry = self.setting_row(feishu, 7, "VERIFY_TOKEN")
        self.ngrok_url_entry = self.setting_row(feishu, 8, "NGROK_URL")


        clients = self.make_card(page, "#3b4252", 22)
        clients.grid(row=5, column=0, padx=24, pady=(8, 24), sticky="ew")
        clients.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(clients, text="ตั้งค่า DWS PLAN Clients", text_color="#eceff4", font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=0, padx=16, pady=(16, 4), sticky="w")
        ctk.CTkLabel(clients, text="IP/Port สำหรับหน้า DWS PLAN และ Controller API", text_color="#aeb8cc").grid(row=1, column=0, padx=16, pady=(0, 6), sticky="w")
        self.controller_client_frame = ctk.CTkFrame(clients, fg_color="#252b36", corner_radius=12)
        self.controller_client_frame.grid(row=2, column=0, padx=16, pady=(0, 16), sticky="ew")
        self.controller_client_rows = []
        self.render_controller_client_settings()
        return page

    def path_row(self, parent, row, label, browse_func):
        ctk.CTkLabel(parent, text=label, text_color="#d8dee9", width=130, anchor="w").grid(row=row, column=0, padx=(16, 8), pady=10, sticky="w")
        ent = ctk.CTkEntry(parent)
        ent.grid(row=row, column=1, padx=8, pady=10, sticky="ew")
        self.bind_clean_entry(ent)
        btn = ctk.CTkButton(parent, text="Browse", width=92, fg_color="#4c566a", hover_color="#5e6779", command=lambda: self.set_path(ent, browse_func()))
        btn.grid(row=row, column=2, padx=(8, 16), pady=10)
        self.lockable_inputs.append(ent)
        self.lockable_buttons.append(btn)
        return ent

    def setting_row(self, parent, row, label):
        ctk.CTkLabel(parent, text=label, text_color="#d8dee9", width=130, anchor="w").grid(row=row, column=0, padx=(16, 8), pady=10, sticky="w")
        ent = ctk.CTkEntry(parent)
        ent.grid(row=row, column=1, padx=(8, 16), pady=10, sticky="ew")
        self.bind_clean_entry(ent, collapse_internal_spaces=("token" in label.lower() or "chat" in label.lower() or "secret" in label.lower() or "app id" in label.lower()), digits_only=("จำนวนรายการ" in label or "download size" in label.lower()))
        self.lockable_inputs.append(ent)
        return ent

    def get_nav_order(self):
        default_order = list(self.nav_items.keys())
        raw = self.config.get("UI", "nav_order", fallback=",".join(default_order))
        order = [x.strip() for x in raw.split(",") if x.strip() in self.nav_items]
        for key in default_order:
            if key not in order:
                order.append(key)
        return order

    def save_nav_order(self, order):
        if "UI" not in self.config:
            self.config["UI"] = {}
        self.config["UI"]["nav_order"] = ",".join(order)
        save_config(self.config)

    def render_nav_menu(self):
        if not hasattr(self, "nav_parent"):
            return
        for child in self.nav_parent.winfo_children():
            child.destroy()
        self.nav_buttons = {}
        self.nav_rows = {}
        order = self.get_nav_order()
        for idx, key in enumerate(order):
            text, attr = self.nav_items[key]
            row = ctk.CTkFrame(self.nav_parent, fg_color="transparent")
            row.grid(row=idx, column=0, pady=4, sticky="ew")
            row.grid_columnconfigure(0, weight=1)
            btn = ctk.CTkButton(row, text=text, height=44, anchor="w", fg_color="#434c5e")
            btn.grid(row=0, column=0, sticky="ew")
            self.nav_buttons[key] = btn
            self.nav_rows[key] = row
            setattr(self, attr, btn)
            for widget in (row, btn):
                widget.bind("<ButtonPress-1>", lambda event, k=key: self.start_nav_drag(k, event))
                widget.bind("<B1-Motion>", lambda event, k=key: self.drag_nav_item(k, event))
                widget.bind("<ButtonRelease-1>", lambda event, k=key: self.end_nav_drag(k, event))
        if hasattr(self, "current_page"):
            self.highlight_nav(self.current_page)

    def can_open_page_during_runtime(self, key: str) -> bool:
        if not (self.running or self.scheduler_running or self.runtime_locked):
            return True
        return key in {"home", "dws_plan", "jms_user"}

    def layout_nav_rows(self):
        for idx, key in enumerate(self.get_nav_order()):
            row = getattr(self, "nav_rows", {}).get(key)
            if row is not None:
                row.grid(row=idx, column=0, pady=4, sticky="ew")
        self.highlight_nav(getattr(self, "current_page", "home"))

    def start_nav_drag(self, key, event):
        if not self.can_open_page_during_runtime(key):
            return
        self.nav_drag_key = key
        self.nav_drag_started = False
        self.nav_drag_start_y = event.y_root

    def drag_nav_item(self, key, event):
        if self.nav_drag_key != key or self.running or self.scheduler_running or self.runtime_locked:
            return
        if abs(event.y_root - self.nav_drag_start_y) < 8:
            return
        self.nav_drag_started = True
        btn = self.nav_buttons.get(key)
        if btn is not None:
            btn.configure(fg_color="#4c566a")

        order = self.get_nav_order()
        if key not in order:
            return
        current_idx = order.index(key)
        target_idx = current_idx
        for idx, item_key in enumerate(order):
            row = self.nav_rows.get(item_key)
            if row is None:
                continue
            row_top = row.winfo_rooty()
            row_mid = row_top + (row.winfo_height() / 2)
            if event.y_root < row_mid:
                target_idx = idx
                break
        else:
            target_idx = len(order) - 1

        if target_idx != current_idx:
            item = order.pop(current_idx)
            order.insert(target_idx, item)
            self.save_nav_order(order)
            self.layout_nav_rows()
            self.nav_drag_key = key
            self.nav_drag_started = True

    def end_nav_drag(self, key, event):
        if self.nav_drag_key != key:
            return
        dragged = self.nav_drag_started
        self.nav_drag_key = None
        self.nav_drag_started = False
        if dragged:
            self.highlight_nav(getattr(self, "current_page", "home"))
            return "break"
        if self.can_open_page_during_runtime(key):
            self.show_page(key)

    def highlight_nav(self, name):
        for btn in getattr(self, "nav_buttons", {}).values():
            btn.configure(fg_color="#434c5e")
        btn = getattr(self, "nav_buttons", {}).get(name)
        if btn is not None:
            btn.configure(fg_color="#5e81ac")

    def show_page(self, name):
        previous_page = getattr(self, "current_page", None)
        if previous_page == "settings" and name != "settings":
            self.save_all()
        for p in self.pages.values():
            p.grid_remove()
        self.pages[name].grid(row=0, column=0, sticky="nsew")
        self.current_page = name
        self.highlight_nav(name)

    def load_values_to_ui(self):
        self.out_entry.insert(0, self.config["PATH"].get("output_dir", ""))
        self.minute_var.set(self.config["TIME"].get("run_minute", "5"))
        self.start_hour_var.set(f'{int(self.config["TIME"].get("start_hour", "15")):02}:00')
        self.end_hour_var.set(f'{int(self.config["TIME"].get("end_hour", "12")):02}:00')
        self.app_id_entry.insert(0, self.config["FEISHU"].get("APP_ID", ""))
        self.app_secret_entry.insert(0, self.config["FEISHU"].get("APP_SECRET", ""))
        if hasattr(self, "chat_id_entry"):
            self.chat_id_entry.insert(0, self.config["FEISHU"].get("CHAT_ID", ""))
        for attr, key in [
            ("bot_port_entry", "BOT_PORT"),
            ("bot_name_entry", "BOT_NAME"),
            ("verify_token_entry", "VERIFY_TOKEN"),
            ("ngrok_url_entry", "NGROK_URL"),
            ("auth_token_entry", "JMS_TOKEN"),
        ]:
            if hasattr(self, attr):
                value = self.config["FEISHU"].get(key, DEFAULT_FEISHU.get(key, ""))
                if key == "JMS_TOKEN" and not value:
                    value = self.config["DWS_JMS"].get("jms_token", "") if "DWS_JMS" in self.config else ""
                getattr(self, attr).insert(0, value)
        dws = self.config["DWS_JMS"] if "DWS_JMS" in self.config else {}
        if hasattr(self, "raw_path_entry"):
            self.raw_path_entry.insert(0, dws.get("raw_path", "") or self.config["PATH"].get("output_dir", ""))
        if hasattr(self, "bot_export_var"):
            self.bot_export_var.set(as_bool(dws.get("enabled", "true"), True))
        if hasattr(self, "bot_chat_var"):
            self.bot_chat_var.set(as_bool(dws.get("bot_chat_enabled", "true"), True))
        for _attr, _key, _default in [
            ("db_host_entry", "db_host", "10.30.32.10"),
            ("db_port_entry", "db_port", "3306"),
            ("db_user_entry", "db_user", "root"),
            ("db_password_entry", "db_password", "root"),
            ("db_name_entry", "db_name", "dwsdb_thailand"),
        ]:
            if hasattr(self, _attr):
                getattr(self, _attr).insert(0, dws.get(_key, _default))
        for _attr, _key, _default in [
            ("name_dws", "name_dws", "DWS9-11.xlsx"),
            ("name_auto", "name_auto", "DWSXAUTOPDA.xlsx"),
            ("name_dwspda", "name_dwspda", "DWSPDA.xlsx"),
            ("name_realtime_db", "name_realtime_db", "RealtimeDB.xlsx"),
        ]:
            if hasattr(self, _attr):
                getattr(self, _attr).insert(0, dws.get(_key, _default))
        for _attr, _key in [
            ("send_dws_file_var", "send_dws_file"),
            ("send_auto_file_var", "send_auto_file"),
            ("send_dwspda_file_var", "send_dwspda_file"),
            ("send_realtime_file_var", "send_realtime_file"),
        ]:
            if hasattr(self, _attr):
                getattr(self, _attr).set(as_bool(dws.get(_key, "false"), False))
        # ตอนเปิดแอปทุกครั้ง: วันเริ่ม = วันปัจจุบัน, วันจบ = +1 วันเสมอ
        # (ไม่ใช้ค่าที่เซฟไว้ กันวันที่เก่าค้างเมื่อเปิดคนละวัน)
        today = datetime.now().date()
        try:
            self.start_date.set_date(today)
            self.end_date.set_date(today + timedelta(days=1))
        except Exception:
            pass
        self.start_hour.set(dws.get("start_hour", "13:00"))
        self.end_hour.set(dws.get("end_hour", "23:00"))
        self.reload_workbook_cards()

    def reload_workbook_cards(self):
        for card in self.workbook_cards:
            card.destroy()
        self.workbook_cards.clear()
        keys = [x.strip() for x in self.config["WORKBOOKS"].get("items", "").split(",") if x.strip()]
        for idx, key in enumerate(keys):
            if f"WORKBOOK:{key}" not in self.config:
                continue
            card = WorkbookCard(self.workbook_scroll, self, key)
            card.grid(row=idx, column=0, padx=12, pady=10, sticky="ew")
            self.workbook_cards.append(card)
    def add_workbook_files(self):
        paths = filedialog.askopenfilenames(filetypes=[("Excel files", "*.xlsx *.xlsm *.xlsb *.xls"), ("All files", "*.*")])
        if not paths:
            return
        keys = [x.strip() for x in self.config["WORKBOOKS"].get("items", "").split(",") if x.strip()]
        existing_paths = {
            self.config[f"WORKBOOK:{k}"].get("path", "").strip().lower()
            for k in keys if f"WORKBOOK:{k}" in self.config
        }
        added = 0
        for path in paths:
            norm = os.path.abspath(path).lower()
            if norm in existing_paths:
                continue
            key = unique_key(keys, safe_key(path))
            keys.append(key)
            self.config[f"WORKBOOK:{key}"] = {
                "path": os.path.abspath(path),
                "display_name": os.path.basename(path),
                "enabled": "true",
                "send_excel": "false",
            }
            added += 1
        self.config["WORKBOOKS"]["items"] = ",".join(keys)
        self.save_config()
        self.reload_workbook_cards()
        self.write_log(f"Added workbook(s): {added}")

    def add_export_for_workbook(self, workbook_key: str):
        names = [x.strip() for x in self.config["EXPORTS"].get("items", "").split(",") if x.strip()]
        name = next_numeric_name(names)
        names.append(name)
        self.config["EXPORTS"]["items"] = ",".join(names)
        self.config[f"EXPORT:{name}"] = {
            "enabled": "true",
            "send_enabled": "true",
            "workbook": workbook_key,
            "sheet": "",
            "range": "A1:Z50",
            "file": f"export_{name}.png",
            "delete_by_start": "false",
        }
        self.save_config()
        self.reload_workbook_cards()

    def delete_export(self, name: str):
        names = [x.strip() for x in self.config["EXPORTS"].get("items", "").split(",") if x.strip() and x.strip() != name]
        self.config["EXPORTS"]["items"] = ",".join(names)
        section = f"EXPORT:{name}"
        if section in self.config:
            self.config.remove_section(section)
        self.save_config()
        self.reload_workbook_cards()

    def delete_workbook(self, workbook_key: str):
        keys = [x.strip() for x in self.config["WORKBOOKS"].get("items", "").split(",") if x.strip() and x.strip() != workbook_key]
        self.config["WORKBOOKS"]["items"] = ",".join(keys)
        section = f"WORKBOOK:{workbook_key}"
        if section in self.config:
            self.config.remove_section(section)

        export_names = [x.strip() for x in self.config["EXPORTS"].get("items", "").split(",") if x.strip()]
        keep = []
        for name in export_names:
            sec = f"EXPORT:{name}"
            if sec in self.config and self.config[sec].get("workbook") == workbook_key:
                self.config.remove_section(sec)
            else:
                keep.append(name)
        self.config["EXPORTS"]["items"] = ",".join(keep)
        self.save_config()
        self.reload_workbook_cards()

    @staticmethod
    def browse_folder():
        return filedialog.askdirectory()

    def set_path(self, entry, value):
        if value:
            entry.delete(0, "end")
            entry.insert(0, value)
            self.save_config()

    def mark_activity(self):
        self.last_activity = time.time()

    def write_log(self, msg, level: Optional[str] = None, touch_activity: bool = True):
        if touch_activity:
            self.mark_activity()
        stamp = datetime.now().strftime("%H:%M:%S")
        icon, text, tag = self.format_log_line(str(msg), level=level)
        line = f"[{stamp}] {icon} {text}\n"
        self.queue_log_line("main", (line, tag))

    def queue_log_line(self, key, payload):
        buffers = getattr(self, "log_buffers", None)
        pending = getattr(self, "log_flush_pending", None)
        lock = getattr(self, "log_flush_lock", None)
        if buffers is None or pending is None or lock is None:
            return
        with lock:
            buffers[key].append(payload)
            if pending[key]:
                return
            pending[key] = True
        try:
            self.after(getattr(self, "log_flush_delay_ms", 3000), lambda k=key: self.flush_log_buffer(k))
        except Exception:
            with lock:
                pending[key] = False

    def flush_log_buffer(self, key):
        if not self.is_ui_thread():
            self.run_on_ui_thread(self.flush_log_buffer, key)
            return
        buffers = getattr(self, "log_buffers", None)
        pending = getattr(self, "log_flush_pending", None)
        lock = getattr(self, "log_flush_lock", None)
        if buffers is None or pending is None or lock is None:
            return
        with lock:
            items = []
            source = buffers[key]
            for _ in range(min(len(source), getattr(self, "log_flush_batch_size", 200))):
                items.append(source.popleft())
            has_more = bool(source)
            pending[key] = has_more
        if not items:
            return
        if key == "main":
            box = getattr(self, "log_box", None)
            if box is not None:
                for line, tag in items:
                    try:
                        box.insert("end", line, tag)
                    except Exception:
                        box.insert("end", line)
                self.trim_log_box(box)
                box.see("end")
        else:
            box_name = "controller_log_box" if key == "controller" else "jms_log_text"
            box = getattr(self, box_name, None)
            if box is not None:
                box.insert("end", "".join(items))
                self.trim_log_box(box)
                box.see("end")
        if has_more:
            self.after(getattr(self, "log_flush_delay_ms", 3000), lambda k=key: self.flush_log_buffer(k))

    def trim_log_box(self, box, max_lines: int = 100):
        """จำกัดจำนวนบรรทัดใน log box — เกิน max_lines ตัดแถวบนสุด (เก่าสุด) ออก
        กันหน่วยความจำบวมเมื่อเปิดโปรแกรมทิ้งไว้หลายวัน"""
        try:
            line_count = int(box.index("end-1c").split(".")[0])
            if line_count > max_lines:
                box.delete("1.0", f"{line_count - max_lines + 1}.0")
        except Exception:
            pass

    def format_log_line(self, raw: str, level: Optional[str] = None):
        text = (raw or "").strip()

        # Remove legacy duplicate icons and technical prefixes from old log calls.
        text = re.sub(r"^[\s⚡✓✔✅✕✖❌●•▲⚠️]+", "", text).strip()

        upper = text.upper()
        lower = text.lower()

        # Default classification.
        tag = "INFO"
        icon = "ℹ️"

        if level:
            lv = str(level).upper()
            if lv in {"SUCCESS", "OK", "DONE"}:
                tag, icon = "SUCCESS", "✅"
            elif lv in {"ERROR", "FAIL", "FAILED"}:
                tag, icon = "ERROR", "❌"
            elif lv in {"WARN", "WARNING"}:
                tag, icon = "WARN", "⚠️"
            elif lv in {"START", "RUN"}:
                tag, icon = "START", "🚀"

        # Common error/warning/success detection.
        if any(k in upper for k in ["ERROR", "FAILED", "FAIL", "EXCEPTION"]):
            tag, icon = "ERROR", "❌"
        elif any(k in upper for k in ["WARN", "WARNING", "RETRY", "SKIP"]):
            tag, icon = "WARN", "⚠️"
        elif any(k in upper for k in ["DONE", "COMPLETED", "SUCCESS", "[OK]", " OK"]) or "saved" in lower:
            tag, icon = "SUCCESS", "✅"
        elif any(k in upper for k in ["START", "RUN ", "TRIGGER", "OPEN", "EXPORT", "UPLOAD", "SEND"]):
            tag, icon = "START", "🚀"

        # Normalize main pipeline messages.
        replacements = [
            (r"^Start processing\.\.\.$", "Pipeline started"),
            (r"^Done ✅$", "Pipeline completed successfully"),
            (r"^Sending images\.\.\.$", "Feishu delivery started"),
            (r"^Auto scheduler started$", "Auto scheduler started"),
            (r"^Auto scheduler stopped$", "Auto scheduler stopped"),
            (r"^Process stopped$", "Current process stopped by user"),
            (r"^Stopped$", "All running tasks stopped"),
            (r"^Trigger auto run at (.+)$", r"Scheduled run triggered at \1"),
            (r"^Skip auto run (.+) \(busy with (.+)\)$", r"Scheduled run skipped at \1 — busy with \2"),
            (r"^Bot Export (\d+)/(\d+): (.+)$", r"Raw export step \1/\2 — \3"),
            (r"^Run step (\d+)/(\d+): (.+)$", r"Manual export step \1/\2 — \3"),
            (r"^Start DWS/JMS mode: (.+) \| (.+) → (.+)$", r"Manual DWS/JMS started — mode=\1 | \2 → \3"),
            (r"^DWS/JMS Done ✅$", "DWS/JMS manual export completed"),
        ]
        for pat, repl in replacements:
            new_text = re.sub(pat, repl, text)
            if new_text != text:
                text = new_text
                break

        # Normalize legacy [TAG] messages.
        m = re.match(r"^\[([^\]]+)\]\s*(.*)$", text)
        if m:
            source, body = m.group(1).strip(), m.group(2).strip()
            body_upper = body.upper()

            if body_upper.startswith("REQUESTING EXPORT FILE"):
                text = f"{source} export request sent"
                tag, icon = "START", "🚀"
            elif body_upper.startswith("STATUS:"):
                text = f"{source} response {body.split(':', 1)[-1].strip()}"
                tag, icon = "INFO", "🌐"
            elif body_upper.startswith("SIZE:"):
                try:
                    mb = int(body.split(':', 1)[-1].strip()) / 1024 / 1024
                    text = f"{source} response size {mb:.2f} MB"
                except Exception:
                    text = f"{source} {body.lower()}"
                tag, icon = "INFO", "📦"
            elif "FILE SAVED" in body_upper:
                filename = body.split("→")[-1].strip() if "→" in body else body
                text = f"{filename} saved successfully"
                tag, icon = "SUCCESS", "📥"
            elif "DWS FINISHED" in body_upper:
                text = "DWS export completed"
                tag, icon = "SUCCESS", "✅"
            elif body_upper.startswith("PAGE") and "LOADED" in body_upper:
                text = f"{source} {body.lower()}"
                tag, icon = "INFO", "📄"
            elif "CREATING" in body_upper:
                text = f"{source} generating output file"
                tag, icon = "START", "🧾"
            elif "DOWNLOADED" in body_upper:
                text = f"{source} {body}"
                tag, icon = "SUCCESS", "📥"
            else:
                text = f"{source} — {body}" if body else source

        # Normalize Createphoto / Botmessage messages.
        m = re.match(r"^\[OPEN\]\s*(.+)$", text)
        if m:
            text, tag, icon = f"Workbook opened — {m.group(1)}", "START", "📘"

        m = re.match(r"^\[DATE SET\]\s*A2\s*=\s*(.+)$", text)
        if m:
            text, tag, icon = f"Business date set — {m.group(1)}", "INFO", "📅"

        m = re.match(r"^\[DELETE\]\s*(.+)\s+(\d+)\s+cols$", text)
        if m:
            text, tag, icon = f"{m.group(1)} adjusted — removed {m.group(2)} column(s)", "INFO", "🧹"

        m = re.match(r"^\[SKIP DELETE\]\s*(.+)$", text)
        if m:
            text, tag, icon = f"{m.group(1)} column adjustment skipped", "INFO", "↪️"

        m = re.match(r"^\[EXPORT\]\s*(.+)$", text)
        if m:
            text, tag, icon = f"Image export started — {m.group(1)}", "START", "🖼"

        m = re.match(r"^\[OK\]\s*(.+)$", text)
        if m:
            text, tag, icon = f"Image created — {os.path.basename(m.group(1))}", "SUCCESS", "✅"

        m = re.match(r"^TOKEN OK$", text, re.I)
        if m:
            text, tag, icon = "Feishu access token ready", "SUCCESS", "🔐"

        m = re.match(r"^Uploading:\s*(.+)$", text)
        if m:
            text, tag, icon = f"Uploading image — {os.path.basename(m.group(1))}", "START", "📡"

        m = re.match(r"^Sending:\s*(.+)$", text)
        if m:
            text, tag, icon = f"Sending image — {os.path.basename(m.group(1))}", "START", "🚀"

        m = re.match(r"^Missing file:\s*(.+)$", text)
        if m:
            text, tag, icon = f"Image not found — {os.path.basename(m.group(1))}", "WARN", "⚠️"

        m = re.match(r"^Excel attachment delivery started — (.+)$", text)
        if m:
            text, tag, icon = f"Excel attachment delivery started — {m.group(1)}", "START", "📎"

        m = re.match(r"^Uploading Excel file — (.+)$", text)
        if m:
            text, tag, icon = f"Uploading Excel file — {m.group(1)}", "START", "📤"

        m = re.match(r"^Sending Excel file — (.+)$", text)
        if m:
            text, tag, icon = f"Sending Excel file — {m.group(1)}", "START", "📨"

        m = re.match(r"^Excel file sent — (.+)$", text)
        if m:
            text, tag, icon = f"Excel file sent — {m.group(1)}", "SUCCESS", "📎"

        m = re.match(r"^Excel attachment not found — (.+)$", text)
        if m:
            text, tag, icon = f"Excel attachment not found — {m.group(1)}", "WARN", "⚠️"

        # Final cleanup: remove accidental double spaces.
        text = re.sub(r"\s+", " ", text).strip()
        return icon, text, tag

    def set_progress(self, val):
        if not self.is_ui_thread():
            self.run_on_ui_thread(self.set_progress, val)
            return
        self.progress.set(max(0, min(100, val)) / 100)

    def set_status(self, text, color="#a3be8c", bg="#3b4a3e"):
        if not self.is_ui_thread():
            self.run_on_ui_thread(self.set_status, text, color, bg)
            return
        self.status_pill.configure(text=text, text_color=color, fg_color=bg)

    def update_clock(self):
        self.after(1000, self.update_clock)

    def set_runtime_lock_visuals(self, active: bool):
        """Lock all configuration controls during Run Now / Auto processing and make the disabled state visible."""
        if not self.is_ui_thread():
            self.run_on_ui_thread(self.set_runtime_lock_visuals, active)
            return
        self.runtime_locked = active

        for card in getattr(self, "workbook_cards", []):
            try:
                card.apply_visual_state()
            except Exception:
                pass

        input_state = "disabled" if active else "normal"
        for ent in getattr(self, "lockable_inputs", []):
            try:
                ent.configure(
                    state=input_state,
                    fg_color="#323847" if active else "#434c5e",
                    text_color="#7b8496" if active else "#eceff4",
                )
            except Exception:
                pass

        for date_widget in [getattr(self, "start_date", None), getattr(self, "end_date", None)]:
            if date_widget is None:
                continue
            try:
                date_widget.configure(state="disabled" if active else "readonly")
            except Exception:
                pass

        for btn in [
            getattr(self, "btn_add_excel", None),
            getattr(self, "btn_save_workspace", None),
            getattr(self, "btn_save_settings", None),
            *getattr(self, "lockable_buttons", []),
        ]:
            if btn is None:
                continue
            try:
                original = getattr(self, "button_color_map", {}).get(btn)
                normal_fg, normal_hover = original if original else ("#4c566a", "#5e6779")
                btn.configure(
                    state="disabled" if active else "normal",
                    fg_color="#434c5e" if active else normal_fg,
                    hover_color="#434c5e" if active else normal_hover,
                    text_color="#7b8496" if active else "#eceff4",
                )
            except Exception:
                pass

        if active:
            self.set_status("UI Locked", "#d8dee9", "#434c5e")
        elif not self.running and not self.scheduler_running:
            self.set_status("Idle", "#a3be8c", "#3b4a3e")

    def set_ui_running(self, active: bool):
        if not self.is_ui_thread():
            self.run_on_ui_thread(self.set_ui_running, active)
            return
        self.set_runtime_lock_visuals(active)
        state = "disabled" if active else "normal"
        for w in [
            getattr(self, "minute_menu", None), getattr(self, "start_menu", None), getattr(self, "end_menu", None),
            getattr(self, "chk_bot_export", None), getattr(self, "chk_bot_chat", None),
        ]:
            if w is None:
                continue
            try:
                w.configure(state=state)
            except Exception:
                pass

        for key, btn in getattr(self, "nav_buttons", {}).items():
            try:
                nav_state = "normal" if active and key in {"home", "dws_plan", "jms_user"} else state
                btn.configure(state=nav_state)
            except Exception:
                pass
        if not active:
            self.render_nav_menu()

        if active and self.mode == "manual":
            try:
                self.btn_run.grid_remove()
                self.btn_stop_run.grid(row=3, column=8, padx=(8, 16), pady=(6, 16), sticky="ew")
            except Exception:
                pass
        else:
            try:
                self.btn_stop_run.grid_remove()
                self.btn_run.grid(row=3, column=8, padx=(8, 16), pady=(6, 16), sticky="ew")
            except Exception:
                pass

        if self.scheduler_running:
            try:
                self.btn_start.grid_remove()
                self.btn_stop_auto.grid(row=3, column=7, padx=8, pady=(6, 16), sticky="ew")
            except Exception:
                pass
        else:
            try:
                self.btn_stop_auto.grid_remove()
                self.btn_start.grid(row=3, column=7, padx=8, pady=(6, 16), sticky="ew")
            except Exception:
                pass

        if not active:
            self.set_pipeline_state(None, "ready")

    def worker_loop(self):
        while self.worker_running:
            task = self.task_queue.get()
            try:
                if task:
                    task()
            except Exception as e:
                self.write_log(f"Worker error: {e}")
            finally:
                self.task_queue.task_done()

    def watchdog(self):
        while self.worker_running:
            time.sleep(10)
            self.check_watchdog_once()

    def check_watchdog_once(self, now=None):
        if not self.running:
            return
        now = time.time() if now is None else now
        idle_seconds = now - self.last_activity
        if idle_seconds > self.watchdog_warn_seconds and now - self.watchdog_last_warning_at > self.watchdog_warning_interval_seconds:
            self.watchdog_last_warning_at = now
            self.write_log(
                f"Watchdog warning: no pipeline activity for {int(idle_seconds)}s, continuing current run",
                level="WARN",
                touch_activity=False,
            )
        if self.watchdog_timeout_seconds and idle_seconds > self.watchdog_timeout_seconds:
            self.write_log(
                f"Watchdog timeout: no pipeline activity for {int(idle_seconds)}s, stopping current process only",
                level="WARN",
                touch_activity=False,
            )
            self.run_on_ui_thread(self.stop_process)

    def is_busy(self):
        return self.running

    def validate_run_selection(self):
        run_export = self.get_bool_var_value("bot_export_var", True)
        run_chat = self.get_bool_var_value("bot_chat_var", True)
        if not run_export and not run_chat:
            self.write_log("Please select Bot Export, Bot Chat, or both")
            return None
        return run_export, run_chat

    def validate_ready(self):
        selection = self.validate_run_selection()
        if not selection:
            return None
        run_export, run_chat = selection
        out = entry_value(self.out_entry)
        workbook_keys = [x.strip() for x in self.config["WORKBOOKS"].get("items", "").split(",") if x.strip()]
        if run_export and not self.validate_dws_jms_ready():
            return None
        if run_chat:
            if not out:
                self.write_log("Please select Output Picture folder")
                return None
            if not workbook_keys:
                self.write_log("กรุณาเพิ่มไฟล์ Excel อย่างน้อย 1 ไฟล์ในหน้าไฟล์ Excel")
                return None
        return out if run_chat else self.get_raw_export_folder()

    def log(self, msg, tag="SYS", level="INFO"):
        # Compatibility wrapper for old module-style log calls.
        # write_log() handles icon, level color, and wording normalization.
        self.write_log(f"[{tag}] {msg}", level=level)

    def get_raw_export_folder(self):
        active = getattr(self, "active_run_settings", None) or {}
        if active.get("raw_folder"):
            return active["raw_folder"]
        if not self.is_ui_thread():
            dws = self.config["DWS_JMS"] if "DWS_JMS" in self.config else {}
            path_cfg = self.config["PATH"] if "PATH" in self.config else {}
            return clean_input_value(dws.get("raw_path", "")) or clean_input_value(path_cfg.get("output_dir", ""))
        raw = self.__dict__.get("raw_path_entry")
        if raw is not None and entry_value(raw):
            return entry_value(raw)
        return entry_value(self.__dict__.get("out_entry"))

    def get_dws_export_filename(self, key: str, default: str) -> str:
        active_raw = self.__dict__.get("active_run_settings")
        active: Dict[str, Any] = active_raw if isinstance(active_raw, dict) else {}
        names_raw = active.get("dws_names")
        active_names: Dict[str, str] = {
            str(name_key): str(name_value)
            for name_key, name_value in names_raw.items()
        } if isinstance(names_raw, dict) else {}
        active_value = active_names.get(key, "")
        if active_value:
            return clean_input_value(active_value) or default
        dws = self.config["DWS_JMS"] if "DWS_JMS" in self.config else {}
        filename = clean_input_value(dws.get(key, ""))
        return filename or default

    def get_dws_db_config(self) -> dict:
        """อ่านค่าเชื่อมต่อ MySQL ของ DWS9-11
        ลำดับความสำคัญ: active_run_settings (worker thread) -> ช่องกรอกใน UI -> config.ini -> ค่า default"""
        active = getattr(self, "active_run_settings", None) or {}
        active_db = active.get("db_config")
        if isinstance(active_db, dict) and active_db:
            return active_db

        dws = self.config["DWS_JMS"] if "DWS_JMS" in self.config else {}
        ui_map = {
            "db_host": "db_host_entry",
            "db_port": "db_port_entry",
            "db_user": "db_user_entry",
            "db_password": "db_password_entry",
            "db_name": "db_name_entry",
        }

        def _val(key, default):
            if self.is_ui_thread() and key in ui_map and ui_map[key] in self.__dict__:
                ui_val = entry_value(getattr(self, ui_map[key]), collapse_internal_spaces=True)
                if ui_val:
                    return ui_val
            raw = clean_input_value(dws.get(key, "")) if dws else ""
            return raw or default

        port_raw = _val("db_port", "3306")
        try:
            port = int(str(port_raw).strip())
        except (TypeError, ValueError):
            port = 3306
        return {
            "host": _val("db_host", "10.30.32.10"),
            "port": port,
            "user": _val("db_user", "root"),
            "password": _val("db_password", "root"),
            "database": _val("db_name", "dwsdb_thailand"),
            "table": _val("db_table", "tab_assembly_dws_sorting_log"),
            "sheet": _val("db_sheet", "分拣日志"),
        }

    def get_jms_auth_token(self) -> str:
        active = getattr(self, "active_run_settings", None) or {}
        if active.get("jms_token"):
            return clean_input_value(active["jms_token"], collapse_internal_spaces=True)
        token = ""
        if self.is_ui_thread() and "auth_token_entry" in self.__dict__:
            token = entry_value(self.auth_token_entry, collapse_internal_spaces=True)
        if not token and "FEISHU" in self.config:
            token = clean_input_value(self.config["FEISHU"].get("JMS_TOKEN", ""), collapse_internal_spaces=True)
        if not token and "FEISHU" in self.config:
            token = clean_input_value(self.config["FEISHU"].get("AUTH_TOKEN", ""), collapse_internal_spaces=True)
        if not token and "DWS_JMS" in self.config:
            token = clean_input_value(self.config["DWS_JMS"].get("jms_token", ""), collapse_internal_spaces=True)
        return token

    def notify_it_alert(self, key: str, title: str, detail: str, cooldown_seconds: int = 900):
        now = time.time()
        last_sent = self.system_alert_last_sent.get(key, 0)
        if now - last_sent < cooldown_seconds:
            return False
        text = (
            f"แจ้งเตือนระบบ Auto Report\n"
            f"{title}\n\n"
            f"{detail}"
        )
        ok = send_system_alert(text)
        if ok:
            self.system_alert_last_sent[key] = now
            self.write_log(f"IT alert sent: {title}")
        else:
            self.write_log(f"IT alert failed: {title}", level="ERROR")
        return ok

    def is_token_error_text(self, text: str) -> bool:
        lowered = str(text or "").lower()
        return any(x in lowered for x in (
            "token",
            "auth",
            "unauthorized",
            "forbidden",
            "login",
            "session",
            "401",
            "403",
            "หมดอายุ",
            "ไม่ถูกต้อง",
            "เข้าสู่ระบบ",
            "ไม่มีสิทธิ์",
            "请重新登录",
            "重新登录",
            "未登录",
            "登录",
            "登陆",
            "权限",
        ))

    def extract_response_message(self, data: Any) -> str:
        if not isinstance(data, dict):
            return ""
        parts = []
        for key in ("msg", "message", "error", "errMsg", "errmsg", "reason"):
            value = data.get(key)
            if value:
                parts.append(str(value))
        return " ".join(parts)

    def raise_token_error_from_response(self, response, token_name: str):
        status = getattr(response, "status_code", None)
        if status in (401, 403):
            raise Exception(f"{token_name} หมดอายุหรือไม่ถูกต้อง กรุณาอัปเดต {token_name} ที่หน้า ตั้งค่า")
        content = getattr(response, "content", b"") or b""
        content_type = str(getattr(response, "headers", {}).get("Content-Type", "")).lower()
        if "json" in content_type:
            try:
                data = response.json()
            except Exception:
                data = None
            if self.is_token_error_text(self.extract_response_message(data)):
                raise Exception(f"{token_name} หมดอายุหรือไม่ถูกต้อง กรุณาอัปเดต {token_name} ที่หน้า ตั้งค่า")
            return
        if len(content) > 2000 and "json" not in content_type and "text" not in content_type:
            return
        body = getattr(response, "text", "")
        if self.is_token_error_text(body):
            raise Exception(f"{token_name} หมดอายุหรือไม่ถูกต้อง กรุณาอัปเดต {token_name} ที่หน้า ตั้งค่า")

    def notify_export_token_error(self, error, source: str = "ตัวดึงยอด"):
        text = str(error)
        if "JMS_TOKEN" in text or self.is_token_error_text(text):
            return self.notify_it_alert(
                "jms_token_export_failed",
                "JMS_TOKEN ใช้งานไม่ได้",
                "JMS_TOKEN หมดอายุหรือไม่ถูกต้อง กรุณาอัปเดต JMS_TOKEN ที่หน้า ตั้งค่า"
            )
        return False

    # ============================================================
    # Token health-check เชิงรุก — ยิง probe เบาๆ (ไม่โหลดไฟล์เต็ม)
    # คืนสถานะ: "ok" / "invalid" / "missing" / "error"(network)
    # ============================================================

    def _probe_jms_token(self):
        token = self.get_jms_auth_token()
        if not token:
            return "missing"
        try:
            base, headers = self._jms_base_headers()
            resp = requests.post(
                f"{base}/downLoadCenter/downLoadInfoList",
                json={"current": 1, "size": 1},
                headers=headers,
                timeout=(5, 20),
            )
            if getattr(resp, "status_code", None) in (401, 403):
                return "invalid"
            try:
                data = resp.json()
            except Exception:
                data = {}
            if self.is_token_error_text(self.extract_response_message(data)):
                return "invalid"
            return "ok"
        except Exception as e:
            if self.is_token_error_text(str(e)):
                return "invalid"
            return "error"

    def _probe_dws_db(self):
        """ทดสอบเชื่อมต่อฐานข้อมูล DWS (MySQL) เชิงรุก"""
        db = self.get_dws_db_config()
        if not db.get("host") or not db.get("database"):
            return "missing"
        conn = None
        try:
            conn = pymysql.connect(
                host=db["host"], port=db["port"], user=db["user"],
                password=db["password"], database=db["database"],
                charset="utf8mb4", connect_timeout=8, read_timeout=15,
            )
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return "ok"
        except pymysql.err.OperationalError as e:
            # 1045 = access denied (user/pass ผิด), 1049 = unknown database
            code = e.args[0] if e.args else 0
            if code in (1045, 1049, 1044):
                return "invalid"
            return "error"
        except Exception:
            return "error"
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def run_token_healthcheck(self):
        """ตรวจ token เชิงรุก (เรียกจาก scheduler ตอน Auto ทำงาน)
        แจ้ง Feishu ทันทีถ้าเสีย เพื่อให้ไปแก้ทันก่อนรอบส่งยอด"""
        if not self.scheduler_running or self.running:
            return
        self.write_log("ตรวจ token เชิงรุก...", level="INFO")
        checks = (("JMS_TOKEN", self._probe_jms_token), ("DWS Database", self._probe_dws_db))
        for name, probe in checks:
            try:
                result = probe()
            except Exception:
                result = "error"
            if result == "ok":
                self.write_log(f"{name} ใช้งานได้ปกติ", level="SUCCESS")
            elif result in ("invalid", "missing"):
                detail = "หมดอายุหรือไม่ถูกต้อง" if result == "invalid" else "ยังไม่ได้ตั้งค่า"
                self.write_log(f"{name} {detail} — แจ้งเตือน IT แล้ว", level="ERROR")
                self.notify_it_alert(
                    f"token_healthcheck_{name}",
                    f"{name} ใช้งานไม่ได้ (ตรวจเชิงรุก)",
                    f"{name} {detail}\nกรุณาอัปเดต {name} ที่หน้า ตั้งค่า ก่อนถึงรอบส่งยอด",
                    cooldown_seconds=600,
                )
            else:
                # network สะดุด/ตอบไม่ชัด — ไม่แจ้งเตือนกันปลุกผิด แค่ log
                self.write_log(f"{name} ตรวจไม่ชัด (network) — ข้ามการแจ้งเตือน", level="WARN")

    def validate_dws_jms_ready(self):
        if not self.get_raw_export_folder():
            self.write_log("Please select Raw Export Folder")
            return False
        _db = self.get_dws_db_config()
        if not _db.get("host") or not _db.get("database"):
            self.write_log("Missing DWS Database config")
            self.notify_it_alert(
                "missing_dws_db",
                "ตั้งค่าฐานข้อมูล DWS ยังไม่ครบ",
                "ตัวดึงยอด DWS ไม่สามารถเริ่มงานได้ กรุณาตั้งค่า DB Host/DB Name ที่หน้า ตั้งค่า"
            )
            return False
        if not self.get_jms_auth_token():
            self.write_log("Missing JMS_TOKEN")
            self.notify_it_alert(
                "missing_jms_token_export",
                "JMS_TOKEN ยังไม่ได้ตั้งค่า",
                "ตัวดึงยอด JMS ไม่สามารถเริ่มงานได้ กรุณาอัปเดต JMS_TOKEN ที่หน้า ตั้งค่า"
            )
            return False
        return True

    def get_time_range(self):
        active = getattr(self, "active_run_settings", None) or {}
        if active.get("time_range"):
            start, end = active["time_range"]
            return start, end
        # Manual test in Data Export uses manual DateEntry. Auto / Run Now follows Bot Chat scheduler.
        if getattr(self, "raw_time_source", "manual") == "scheduler":
            start, end = self.get_scheduler_time_range()
        else:
            start_str = f"{self.start_date.get()} {self.start_hour.get()}"
            end_str = f"{self.end_date.get()} {self.end_hour.get()}"
            start = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
            end = datetime.strptime(end_str, "%Y-%m-%d %H:%M")
            if start > end:
                start, end = end, start
        return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")

    def sleep_with_stop(self, seconds):
        for _ in range(seconds):
            if self.stop_requested or not self.running:
                return False
            self.mark_activity()
            time.sleep(1)
        return True

    def begin_run_request(self):
        lock = getattr(self, "run_state_lock", None)
        if lock is None:
            self.run_generation += 1
            generation = self.run_generation
        else:
            with lock:
                self.run_generation += 1
                generation = self.run_generation
        self.stop_requested = False
        self.watchdog_last_warning_at = 0
        self.mark_activity()
        return generation

    def cancel_pending_runs(self):
        lock = getattr(self, "run_state_lock", None)
        if lock is None:
            self.run_generation += 1
            return self.run_generation
        with lock:
            self.run_generation += 1
            return self.run_generation

    def is_run_generation_active(self, run_generation):
        if run_generation is None:
            return True
        lock = getattr(self, "run_state_lock", None)
        if lock is None:
            return run_generation == self.run_generation
        with lock:
            return run_generation == self.run_generation

    def schedule_pipeline_reset(self, run_generation=None):
        self.after(
            1500,
            lambda g=run_generation: self.set_pipeline_state() if self.is_run_generation_active(g) else None,
        )

    def prepare_dws_jms_settings(self):
        if not self.is_ui_thread():
            self.write_log("DWS/JMS preparation must happen on UI thread", level="ERROR")
            return None
        if not self.validate_dws_jms_ready():
            return None
        self.save_all()
        start_str = f"{self.start_date.get()} {self.start_hour.get()}"
        end_str = f"{self.end_date.get()} {self.end_hour.get()}"
        start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(end_str, "%Y-%m-%d %H:%M")
        if start_dt > end_dt:
            start_dt, end_dt = end_dt, start_dt
        return {
            "raw_folder": self.get_raw_export_folder(),
            "db_config": self.get_dws_db_config(),
            "jms_token": self.get_jms_auth_token(),
            "time_range": (
                start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            ),
            "dws_names": {
                "name_dws": self.get_dws_export_filename("name_dws", "DWS9-11.xlsx"),
                "name_auto": self.get_dws_export_filename("name_auto", "DWSXAUTOPDA.xlsx"),
                "name_dwspda": self.get_dws_export_filename("name_dwspda", "DWSPDA.xlsx"),
                "name_realtime_db": self.get_dws_export_filename("name_realtime_db", "RealtimeDB.xlsx"),
            },
        }

    def run_dws_jms_task(self, mode="full"):
        if self.running:
            self.write_log("DWS/JMS blocked: another process is running")
            return
        settings = self.prepare_dws_jms_settings()
        if not settings:
            return
        self.mode = "manual"
        self.current_run_source = f"dws_jms:{mode}"
        run_generation = self.begin_run_request()
        self.set_ui_running(True)
        self.task_queue.put(lambda m=mode, s=settings, g=run_generation: self.run_dws_jms_process(m, s, g))

    def run_dws_jms_process(self, mode="full", run_settings=None, run_generation=None):
        if self.running:
            return
        if not self.is_run_generation_active(run_generation):
            self.write_log(f"Skipped stale DWS/JMS request: {mode}", level="WARN")
            return
        step_map = {
            "dws": [("dws", "DWS", self.run_dws)],
            "jms_auto": [("jms_auto", "JMS AUTO", self.run_jms_auto)],
            "jms_pda": [("jms_pda", "JMS PDA", self.run_jms_pda)],
            "realtime": [("realtime", "Realtime DB", self.run_realtime_db)],
            "full": [
                ("dws", "DWS", self.run_dws),
                ("jms_auto", "JMS AUTO", self.run_jms_auto),
                ("jms_pda", "JMS PDA", self.run_jms_pda),
                ("realtime", "Realtime DB", self.run_realtime_db),
            ],
        }
        steps = step_map.get(mode, [])
        key = None
        try:
            self.active_run_settings = run_settings or {}
            if run_generation is None:
                run_generation = self.begin_run_request()
            else:
                self.stop_requested = False
                self.last_activity = time.time()
            self.running = True
            self.raw_time_source = "manual"
            self.set_pipeline_state(None, "ready")
            self.set_status("DWS/JMS Running", "#d8c7d3", "#4a3c52")
            self.set_progress(5)
            start, end = self.get_time_range()
            self.write_log(f"Start DWS/JMS mode: {mode} | {start} → {end}", level="START")
            total = max(1, len(steps))
            step_keys = {k for k, _n, _f in steps}
            if "jms_auto" in step_keys and "jms_pda" in step_keys:
                self.prewarm_jms_exports(["建包扫描", "卸车扫描"])
            for idx, (key, name, func) in enumerate(steps, start=1):
                if self.stop_requested or not self.running or not self.is_run_generation_active(run_generation):
                    self.write_log("DWS/JMS stopped")
                    self.set_pipeline_state(key, "skip")
                    return
                self.set_pipeline_state(key, "run")
                self.write_log(f"Run step {idx}/{total}: {name}", level="START")
                func()
                self.set_pipeline_state(key, "ok")
                self.set_progress(int(idx / total * 100))
                if mode == "full" and idx < total:
                    if not self.sleep_with_stop(3):
                        self.write_log("DWS/JMS stopped during wait")
                        return
            self.write_log("DWS/JMS completed successfully", level="SUCCESS")
            self.set_status("Idle", "#a3be8c", "#3b4a3e")
        except Exception as e:
            self.write_log(f"DWS/JMS ERROR: {e}", level="ERROR")
            self.notify_export_token_error(e, "Manual DWS/JMS export")
            if key:
                self.set_pipeline_state(key, "error")
            self.set_status("Error", "#d3868e", "#4a3438")
        finally:
            is_current = self.is_run_generation_active(run_generation)
            self.running = False
            if is_current:
                self.stop_requested = False
            self.active_run_settings = None
            self.raw_time_source = "manual"
            if is_current:
                self.current_run_source = None
            if is_current and not self.scheduler_running:
                self.set_ui_running(False)
            self.schedule_pipeline_reset(run_generation)

    # ลำดับหัวคอลัมน์ให้ตรงกับไฟล์ export เดิม (DWS9-11.xlsx) เป๊ะทั้ง 18 คอลัมน์
    DWS_EXPORT_HEADERS = [
        "扫描时间", "到件时间", "回传时间", "条码", "dws序号",
        "格口", "请求到的格口", "PLC返回的格口", "三段码", "异常码",
        "落格用时", "方案类型", "重量(kg)", "长(cm)", "宽(cm)",
        "高(cm)", "额外数据", "任务编号",
    ]

    @staticmethod
    def _dws_fmt_dt(value):
        """datetime -> 'YYYY-MM-DD HH:MM:SS' (string); ค่าว่าง -> ''"""
        if value is None or value == "":
            return ""
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        return str(value)

    @staticmethod
    def _dws_fmt_dim(value):
        """น้ำหนัก/กว้างยาวสูง -> string ทศนิยม 3 ตำแหน่ง (เหมือน export เดิม); NULL -> ''"""
        if value is None or value == "":
            return ""
        try:
            return f"{float(value):.3f}"
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _dws_fmt_chute_num(value):
        """PLC返回的格口 (StartChuteNo) -> ตัวเลข (int) เหมือน export เดิม; ค่าว่าง -> ''"""
        if value is None or value == "":
            return ""
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _dws_fmt_int(value):
        """异常码 / 落格用时 -> ตัวเลข (int); NULL -> ''"""
        if value is None or value == "":
            return ""
        try:
            return int(value)
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _dws_fmt_str(value):
        """คอลัมน์ประเภท string; NULL -> ''"""
        if value is None:
            return ""
        return str(value)

    def _dws_row_from_record(self, r):
        """แปลง 1 แถวจาก DB (dict) -> list 18 ช่อง ตามลำดับหัวคอลัมน์ export เดิม"""
        return [
            self._dws_fmt_dt(r.get("ScanTime")),          # 扫描时间
            self._dws_fmt_dt(r.get("ArrivalTime")),       # 到件时间
            self._dws_fmt_dt(r.get("PassBackTime")),      # 回传时间
            self._dws_fmt_str(r.get("BarCode")),          # 条码
            self._dws_fmt_str(r.get("DwsNo")),            # dws序号
            self._dws_fmt_str(r.get("ChuteNo")),          # 格口 (实落格口)
            self._dws_fmt_str(r.get("ChuteNos")),         # 请求到的格口 (匹配的所有格口)
            self._dws_fmt_chute_num(r.get("StartChuteNo")),  # PLC返回的格口 (numeric)
            self._dws_fmt_str(r.get("TerminalDispatchCode")),  # 三段码
            self._dws_fmt_int(r.get("ExceptionCode")),    # 异常码
            self._dws_fmt_int(r.get("ElapsedTime")),      # 落格用时
            self._dws_fmt_str(r.get("PlanFlag")),         # 方案类型
            self._dws_fmt_dim(r.get("Weight")),           # 重量(kg)
            self._dws_fmt_dim(r.get("Length")),           # 长(cm)
            self._dws_fmt_dim(r.get("Width")),            # 宽(cm)
            self._dws_fmt_dim(r.get("Heigth")),           # 高(cm)
            self._dws_fmt_str(r.get("ExtraData")),        # 额外数据
            self._dws_fmt_str(r.get("TaskNo")),           # 任务编号
        ]

    @staticmethod
    def _autofit_worksheet(ws, min_width=8, max_width=60, padding=2, scan_rows=3000):
        """ปรับความกว้างคอลัมน์อัตโนมัติตามความยาวข้อความ (รองรับตัวอักษรจีน/ไทยกว้าง 2 เท่า)
        สแกนสูงสุด scan_rows แถวเพื่อความเร็วกับไฟล์ใหญ่"""
        from openpyxl.utils import get_column_letter
        widths = {}
        for idx, row in enumerate(ws.iter_rows()):
            if idx >= scan_rows:
                break
            for cell in row:
                if cell.value is None:
                    continue
                text = str(cell.value)
                # ตัวอักษร CJK/กว้าง นับเป็น 2 หน่วย
                length = sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)
                col = cell.column
                if length > widths.get(col, 0):
                    widths[col] = length
        for col, length in widths.items():
            ws.column_dimensions[get_column_letter(col)].width = max(min_width, min(max_width, length + padding))

    def autofit_excel_file(self, path):
        """เปิดไฟล์ Excel ที่ดาวน์โหลด/สร้างมา แล้วปรับความกว้างคอลัมน์อัตโนมัติ ก่อนบันทึกทับ"""
        try:
            wb = openpyxl.load_workbook(path)
            for ws in wb.worksheets:
                self._autofit_worksheet(ws)
            wb.save(path)
            wb.close()
        except Exception as e:
            self.write_log(
                f"ปรับความกว้างคอลัมน์ไม่สำเร็จ ({os.path.basename(path)}): {e}",
                level="WARN",
            )

    def run_dws(self):
        self.log("Export started", "DWS", "START")

        start, end = self.get_time_range()
        conn = None
        try:
            db = self.get_dws_db_config()
            table = db["table"]
            # ป้องกัน SQL injection ที่ชื่อ table (มาจาก config เท่านั้น) + ดึงตามช่วงเวลา ScanTime
            if not re.match(r"^[A-Za-z0-9_]+$", table):
                raise Exception(f"ชื่อ table ไม่ถูกต้อง: {table}")

            conn = pymysql.connect(
                host=db["host"],
                port=db["port"],
                user=db["user"],
                password=db["password"],
                database=db["database"],
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=10,
                read_timeout=120,
            )
            sql = (
                "SELECT ScanTime, ArrivalTime, PassBackTime, BarCode, DwsNo, "
                "ChuteNo, ChuteNos, StartChuteNo, TerminalDispatchCode, ExceptionCode, "
                "ElapsedTime, PlanFlag, Weight, Length, Width, Heigth, ExtraData, TaskNo "
                f"FROM {table} "
                "WHERE ScanTime BETWEEN %s AND %s "
                "ORDER BY ScanTime DESC, Id DESC"
            )
            with conn.cursor() as cur:
                cur.execute(sql, (start, end))
                records = cur.fetchall()

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = (db["sheet"] or "分拣日志")[:31]
            ws.append(self.DWS_EXPORT_HEADERS)
            for r in records:
                ws.append(self._dws_row_from_record(r))
            self._autofit_worksheet(ws)

            raw_dir = self.get_raw_export_folder()
            os.makedirs(raw_dir, exist_ok=True)
            filename = self.get_dws_export_filename("name_dws", "DWS9-11.xlsx")
            path = os.path.join(raw_dir, filename)
            wb.save(path)

            size_mb = os.path.getsize(path) / 1024 / 1024
            self.write_log(
                f"{filename} saved | {len(records):,} rows | {size_mb:.2f} MB | DB {db['host']}:{db['port']}/{db['database']}",
                level="SUCCESS",
            )
            self.log("Export completed", "DWS", "SUCCESS")

        except Exception as e:
            self.log(f"ERROR: {e}", "DWS", "ERROR")
            if isinstance(e, pymysql.MySQLError):
                self.notify_it_alert(
                    "dws_db_connect_error",
                    "DWS Database ใช้งานไม่ได้",
                    f"เชื่อมต่อฐานข้อมูล DWS9-11 ไม่สำเร็จ กรุณาตรวจสอบว่า MySQL เปิดอยู่และตั้งค่าถูกต้อง\nรายละเอียด: {e}",
                )
            self.set_status("Error", "#d3868e", "#4a3438")
            raise
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


    def _jms_base_headers(self):
        base = "https://jmsgw.jtexpress.co.th/operatingplatform"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authtoken": self.get_jms_auth_token(),
            "origin": "https://jms.jtexpress.co.th",
            "referer": "https://jms.jtexpress.co.th/",
            "routename": "scanComparisonNew",
            "lang": "TH",
            "langtype": "TH",
        }
        return base, headers

    def run_jms_auto(self):
        base, headers = self._jms_base_headers()
        start, end = self.get_time_range()
        self._export_jms(base, headers, start, end, "建包扫描", self.get_dws_export_filename("name_auto", "DWSXAUTOPDA.xlsx"))

    def run_jms_pda(self):
        base, headers = self._jms_base_headers()
        start, end = self.get_time_range()
        self._export_jms(base, headers, start, end, "卸车扫描", self.get_dws_export_filename("name_dwspda", "DWSPDA.xlsx"))

    def prewarm_jms_exports(self, scan_types):
        """Fire asyncDownExcel หลาย scanType ต่อกัน เพื่อให้เซิร์ฟเวอร์ generate
        ขนานกัน (ลดเวลารวม AUTO+PDA). เก็บ marker พร้อม run_generation ไว้ให้
        _export_jms ใช้ collect ต่อ — ถ้า prewarm ล้มก็ปล่อยให้ยิงใหม่แบบเรียง"""
        self.jms_export_markers = {}
        if self.stop_requested:
            return
        try:
            base, headers = self._jms_base_headers()
            start, end = self.get_time_range()
            generation = getattr(self, "run_generation", None)
            session = requests.Session()
            try:
                for st in scan_types:
                    if self.stop_requested:
                        return
                    marker = self._jms_fire_export(session, base, headers, start, end, st)
                    if marker is None:
                        return
                    self.jms_export_markers[st] = (marker, generation)
                    self.sleep_with_stop(2)
            finally:
                session.close()
            self.log("Prewarm: fired parallel exports (AUTO + PDA)", "JMS")
        except Exception as e:
            # prewarm ล้ม ไม่ทำให้ทั้ง run ล้ม — collect จะ fire ใหม่แบบเรียงเอง
            self.jms_export_markers = {}
            self.log(f"Prewarm skipped ({e}) — fallback sequential", "JMS", "WARN")

    def run_realtime_db(self):

        self.log("Loading Realtime DB", "REALTIME")

        token = self.get_jms_auth_token()

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authtoken": token,
            "origin": "https://jms.jtexpress.co.th",
            "referer": "https://jms.jtexpress.co.th/",
            "routename": "TrackRealTimeMonitoringDB",
            "lang": "TH",
            "langtype": "TH",
            "timezone": "GMT+0700",
            "Cache-Control": "max-age=2, must-revalidate",
        }

        session = requests.Session()

        # ==================================================
        # STEP 1 : CREATE EXPORT TASK
        # ==================================================

        export_start_time = (
            datetime.now() - timedelta(seconds=5)
        ).replace(microsecond=0)

        export_url = (
            "https://jmsgw.jtexpress.co.th"
            "/businessindicator/bigdataReport/pageExcelByTask/"
            "trail_monitor_detail_doris"
        )

        payload = {
            "countryId": "1",
            "modelName": "ควบคุมติดตามแบบเรียลไทม์DB(รายละเอียด)",

            "operateTypeCode": [
                "Arrive scan",
                "Packing scan",
                "Problematic parcel scan",
                "Return Item Registration"
            ],

            "overTimeType": [
                "Exceed 4 hours with no track",
                "Exceed 6 hours with no track",
                "Exceed 8 hours with no track",
                "Exceed 12 hours with no track",
                "Exceed 24 hours with no track",
                "Exceed 36 hours with no track",
                "Exceed 48 hours with no track"
            ],

            "scanAgentCode": "555090",
            "scanCode": "999004",
            "scanFranCode": "555090"
        }

        self.log(
            "Realtime generating output file",
            "REALTIME"
        )

        r = session.post(
            export_url,
            json=payload,
            headers=headers,
            timeout=(10, 60)
        )

        self.raise_token_error_from_response(r, "JMS_TOKEN")
        r.raise_for_status()

        export_data = r.json()
        if self.is_token_error_text(self.extract_response_message(export_data)):
            raise Exception("JMS_TOKEN หมดอายุหรือไม่ถูกต้อง กรุณาอัปเดต JMS_TOKEN ที่หน้า ตั้งค่า")

        if (
            export_data.get("code") == 0
            and "กำลังยุ่ง" in str(export_data.get("msg", ""))
        ):
            self.log(
                "Export task already exists, waiting file generation...",
                "REALTIME"
            )

        elif not export_data.get("succ", False):
            raise Exception(
                export_data.get("msg", "Export failed")
            )

        # ==================================================
        # STEP 2 : WAIT EXPORT
        # ==================================================

        if not self.sleep_with_stop(5):
            return

        # ==================================================
        # STEP 3 : QUERY FILE LIST
        # ==================================================

        list_url = (
            "https://jmsgw.jtexpress.co.th"
            "/businessindicator/bigdataReport/report/file/list"
        )

        list_payload = {
            "current": 1,
            "size": 50
        }

        download_url = None

        for _ in range(60):

            resp = session.post(
                list_url,
                json=list_payload,
                headers=headers,
                timeout=(10, 60)
            )

            self.raise_token_error_from_response(resp, "JMS_TOKEN")
            resp.raise_for_status()

            data = resp.json()
            if self.is_token_error_text(self.extract_response_message(data)):
                raise Exception("JMS_TOKEN หมดอายุหรือไม่ถูกต้อง กรุณาอัปเดต JMS_TOKEN ที่หน้า ตั้งค่า")

            rows = data.get("data", {}).get("list", [])

            if not isinstance(rows, list):
                rows = []

            rows = sorted(
                rows,
                key=lambda x: str(
                    x.get("createTime", "")
                ),
                reverse=True
            )

            for row in rows:

                if not isinstance(row, dict):
                    continue

                create_time = str(
                    row.get("createTime", "")
                )

                try:
                    row_time = datetime.strptime(
                        create_time,
                        "%Y-%m-%d %H:%M:%S"
                    )
                except Exception:
                    continue

                if row_time < export_start_time:
                    continue

                if (
                    row.get("business")
                    == "trail_monitor_detail_doris"
                    and (
                        str(row.get("status")) == "2"
                        or row.get("statusText")
                        == "เสร็จสิ้นแล้ว"
                    )
                ):

                    download_url = row.get("downUrl")

                    if not download_url:

                        download_result = row.get(
                            "downloadResult"
                        )

                        if isinstance(
                            download_result,
                            str
                        ):
                            try:
                                download_result = json.loads(
                                    download_result
                                )
                            except Exception:
                                download_result = {}

                        if isinstance(
                            download_result,
                            dict
                        ):
                            download_url = (
                                download_result.get(
                                    "data"
                                )
                            )

                    if download_url:
                        break

            if download_url:
                break

            if not self.sleep_with_stop(2):
                return

        if not download_url:
            raise Exception(
                "Cannot find completed realtime export file"
            )

        # ==================================================
        # STEP 4 : DOWNLOAD XLSX
        # ==================================================

        self.log(
            "Downloading JMS realtime file...",
            "REALTIME"
        )

        file_resp = session.get(
            download_url,
            timeout=(30, 300)
        )

        file_resp.raise_for_status()

        raw_dir = self.get_raw_export_folder()

        os.makedirs(
            raw_dir,
            exist_ok=True
        )

        save_path = os.path.join(
            raw_dir,
            self.get_dws_export_filename("name_realtime_db", "RealtimeDB.xlsx")
        )

        if len(file_resp.content) < 1000:
            raise Exception(
                "Downloaded file too small"
            )

        with open(save_path, "wb") as f:
            f.write(file_resp.content)

        self.autofit_excel_file(save_path)

        self.log(
            f"Downloaded {os.path.basename(save_path)}",
            "REALTIME"
        )

        return save_path

    def _jms_post(self, session, url, payload, headers, retries=2, timeout=(10, 60)):
        """POST ไปยัง JMS พร้อม retry เมื่อ network สะดุดชั่วคราว
        (token error เด้งทันที ไม่ retry). คืน None เมื่อผู้ใช้กด stop."""
        last_err = None
        for attempt in range(retries + 1):
            if self.stop_requested:
                return None
            try:
                resp = session.post(url, json=payload, headers=headers, timeout=timeout)
                self.raise_token_error_from_response(resp, "JMS_TOKEN")
                return resp
            except Exception as e:
                if "JMS_TOKEN" in str(e):
                    raise
                last_err = e
                if attempt < retries:
                    self.log(f"JMS request retry {attempt + 1}/{retries} — {e}", "JMS", "WARN")
                    if not self.sleep_with_stop(3):
                        return None
        raise last_err

    def _jms_fire_export(self, session, base, headers, start, end, scanType):
        """สั่งเซิร์ฟเวอร์สร้างไฟล์ (asyncDownExcel) คืนจุดอ้างอิงเวลา
        ไว้กันหยิบไฟล์เก่าจากรอบก่อน. คืน None เมื่อ stop."""
        export_start_time = (
            datetime.now() - timedelta(seconds=5)
        ).replace(microsecond=0)

        # payload ต้องตรงกับที่หน้าเว็บส่งตอนกด Export ทุก field
        # เพื่อให้เซิร์ฟเวอร์ประมวลผล "เปรียบเทียบการสแกน" (扫描对比)
        # แบบเดียวกับการดึงมือ — ยอดจึงตรงกัน 100%
        payload = {
            "current": 1,
            "size": 20,
            "startTimeStr": start,
            "endTimeStr": end,
            "scanNetworkCode": "999004",
            "scanType": scanType,
            "excelType": "downExcelAll",
            "sortName": "scanDate",
            "sortOrder": "asc",
            "billType": 0,
            "countryId": "1",
            "contrastScanType": "发件扫描",
        }

        self.log("Requesting server export (asyncDownExcel)", "JMS")

        res = self._jms_post(session, f"{base}/scanningContrast/asyncDownExcel", payload, headers)
        if res is None:
            return None

        try:
            gen_data = res.json()
        except Exception:
            gen_data = {}

        if self.is_token_error_text(self.extract_response_message(gen_data)):
            raise Exception("JMS_TOKEN หมดอายุหรือไม่ถูกต้อง กรุณาอัปเดต JMS_TOKEN ที่หน้า ตั้งค่า")

        return export_start_time

    def _jms_collect_export(self, session, base, headers, scanType, filename, export_start_time, max_rounds=90):
        """poll หา record ที่เสร็จของรอบนี้ แล้ว stream ไฟล์ลงดิสก์
        คืน True=สำเร็จ, False=ครบเพดานแต่ไฟล์ยังไม่มา, None=ผู้ใช้กด stop
        (แต่ละรอบ 10 วิ — max_rounds=90 ≈ 15 นาที)"""
        list_url = f"{base}/downLoadCenter/downLoadInfoList"
        sign_url = f"{base}/downLoadCenter/getDownloadSignedUrl"

        download_url = None

        for _ in range(max_rounds):

            if self.stop_requested:
                return None

            resp = self._jms_post(session, list_url, {"current": 1, "size": 20}, headers)
            if resp is None:
                return None

            try:
                list_data = resp.json()
            except Exception:
                list_data = {}
            if self.is_token_error_text(self.extract_response_message(list_data)):
                raise Exception("JMS_TOKEN หมดอายุหรือไม่ถูกต้อง กรุณาอัปเดต JMS_TOKEN ที่หน้า ตั้งค่า")

            records = list_data.get("data", {}).get("records", [])
            if not isinstance(records, list):
                records = []

            records = sorted(
                records,
                key=lambda x: str(x.get("downTime", "")),
                reverse=True
            )

            for r in records:

                if not isinstance(r, dict):
                    continue

                if str(r.get("finishOrNot")) != "1":
                    continue

                if not r.get("downUrl"):
                    continue

                # แยก AUTO (建包扫描) ออกจาก PDA (卸车扫描) ด้วย queryJson
                if scanType not in str(r.get("queryJson", "")):
                    continue

                try:
                    down_time = datetime.strptime(
                        str(r.get("downTime", "")),
                        "%Y-%m-%d %H:%M:%S"
                    )
                except Exception:
                    continue

                # ต้องเป็นไฟล์ของรอบนี้เท่านั้น กันหยิบไฟล์เก่า
                if down_time < export_start_time:
                    continue

                sign_res = self._jms_post(session, sign_url, r, headers)
                if sign_res is None:
                    return None

                try:
                    sign_data = sign_res.json()
                except Exception:
                    sign_data = {}

                signed = sign_data.get("data")
                if isinstance(signed, list):
                    signed = signed[0] if signed else None

                if signed:
                    download_url = signed
                    break

            if download_url:
                break

            self.log("Waiting server file generation...", "JMS")

            if not self.sleep_with_stop(10):
                return None

        if not download_url:
            return False

        # โหลดไฟล์แบบ stream ลงดิสก์ตรงๆ (ไม่โหลดทั้งไฟล์เข้า RAM)
        self.log("Downloading server-generated file", "JMS")

        raw_dir = self.get_raw_export_folder()
        os.makedirs(raw_dir, exist_ok=True)
        save_path = os.path.join(raw_dir, filename)

        written = 0
        with session.get(download_url, timeout=(30, 600), stream=True) as file_resp:
            file_resp.raise_for_status()
            content_type = str(file_resp.headers.get("Content-Type", "")).lower()
            if "html" in content_type:
                raise Exception(f"{filename}: downloaded file invalid (login/html)")
            with open(save_path, "wb") as f:
                for chunk in file_resp.iter_content(chunk_size=262144):
                    if self.stop_requested:
                        return None
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
                    self.mark_activity()

        if written < 1000:
            raise Exception(f"{filename}: downloaded file too small")

        self.autofit_excel_file(save_path)

        self.log(
            f"File saved → {filename}",
            "JMS",
            "SUCCESS"
        )
        return True

    def _export_jms(self, base, headers, start, end, scanType, filename):

        self.log(
            f"Loading JMS data → {filename}",
            "JMS"
        )

        session = requests.Session()
        try:
            # ถ้ามี prewarm สั่ง export ไว้ก่อนแล้ว (marker ของ run เดียวกัน) ใช้เลย
            markers = getattr(self, "jms_export_markers", None)
            entry = markers.pop(scanType, None) if isinstance(markers, dict) else None
            marker = None
            if isinstance(entry, tuple) and len(entry) == 2:
                m, gen = entry
                if gen == getattr(self, "run_generation", None):
                    marker = m

            if marker is not None:
                # เส้นทาง prewarm: collect ด้วยเพดานสั้นลง (~10 นาที)
                # ถ้าไฟล์ยังไม่มา = เซิร์ฟเวอร์อาจไม่รับงานที่ 2 → fallback fire ใหม่
                result = self._jms_collect_export(session, base, headers, scanType, filename, marker, max_rounds=60)
                if result is None:
                    return
                if result:
                    return
                self.log(f"Prewarm file not ready in time — fallback fresh export: {filename}", "JMS", "WARN")

            # เส้นทางปกติ / fallback: fire แล้ว collect เพดานเต็ม (~15 นาที)
            marker = self._jms_fire_export(session, base, headers, start, end, scanType)
            if marker is None:
                return
            if not self.sleep_with_stop(30):
                return
            result = self._jms_collect_export(session, base, headers, scanType, filename, marker, max_rounds=90)
            if result is False:
                raise Exception(f"{filename}: server export file not found (timeout)")
        finally:
            session.close()
        

    def get_selected_excel_files_for_feishu(self):
        paths = []
        seen = set()
        raw_dir = self.get_raw_export_folder()
        active_raw = self.__dict__.get("active_run_settings")
        active: Dict[str, Any] = active_raw if isinstance(active_raw, dict) else {}
        names_raw = active.get("dws_names")
        send_raw = active.get("dws_send_files")
        active_names: Dict[str, str] = {
            str(name_key): str(name_value)
            for name_key, name_value in names_raw.items()
        } if isinstance(names_raw, dict) else {}
        active_send: Dict[str, bool] = {
            str(flag_key): bool(flag_value)
            for flag_key, flag_value in send_raw.items()
        } if isinstance(send_raw, dict) else {}
        dws = self.config["DWS_JMS"] if "DWS_JMS" in self.config else {}
        generated = [
            ("send_dws_file_var", "send_dws_file", "name_dws", "DWS9-11.xlsx"),
            ("send_auto_file_var", "send_auto_file", "name_auto", "DWSXAUTOPDA.xlsx"),
            ("send_dwspda_file_var", "send_dwspda_file", "name_dwspda", "DWSPDA.xlsx"),
            ("send_realtime_file_var", "send_realtime_file", "name_realtime_db", "RealtimeDB.xlsx"),
        ]
        for var_attr, flag_key, name_key, default_name in generated:
            if flag_key in active_send:
                selected = bool(active_send.get(flag_key))
            elif self.is_ui_thread() and hasattr(self, var_attr):
                selected = bool(getattr(self, var_attr).get())
            else:
                selected = as_bool(dws.get(flag_key, "false"), False)
            if not selected:
                continue
            filename = clean_input_value(active_names.get(name_key, "")) or self.get_dws_export_filename(name_key, default_name)
            if not filename:
                continue
            path = os.path.join(raw_dir, filename)
            norm = os.path.normcase(os.path.abspath(path))
            if os.path.exists(path):
                paths.append(path)
                seen.add(norm)
            else:
                self.write_log(f"Excel attachment not found — {filename}", level="WARN")
        keys = [x.strip() for x in self.config["WORKBOOKS"].get("items", "").split(",") if x.strip()]
        for key in keys:
            section = f"WORKBOOK:{key}"
            if section not in self.config:
                continue
            sec = self.config[section]
            if not as_bool(sec.get("enabled", "true"), True):
                continue
            if not as_bool(sec.get("send_excel", "false"), False):
                continue
            path = clean_input_value(sec.get("path", ""))
            norm = os.path.normcase(os.path.abspath(path)) if path else ""
            if norm in seen:
                continue
            if os.path.exists(path):
                paths.append(path)
                seen.add(norm)
            else:
                display = sec.get("display_name", key)
                self.write_log(f"Excel attachment not found — {display}", level="WARN")
        return paths

    def get_tenant_access_token_for_file_send(self):
        active = getattr(self, "active_run_settings", None) or {}
        if active.get("app_id") or active.get("app_secret"):
            app_id = clean_input_value(active.get("app_id", ""), collapse_internal_spaces=True)
            app_secret = clean_input_value(active.get("app_secret", ""), collapse_internal_spaces=True)
        elif self.is_ui_thread():
            app_id = entry_value(self.app_id_entry, collapse_internal_spaces=True)
            app_secret = entry_value(self.app_secret_entry, collapse_internal_spaces=True)
        else:
            feishu = self.config["FEISHU"] if "FEISHU" in self.config else {}
            app_id = clean_input_value(feishu.get("APP_ID", ""), collapse_internal_spaces=True)
            app_secret = clean_input_value(feishu.get("APP_SECRET", ""), collapse_internal_spaces=True)
        if not app_id or not app_secret:
            raise Exception("Missing APP_ID / APP_SECRET")

        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/"
        response = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=15)
        response.raise_for_status()
        try:
            res = response.json()
        except ValueError as exc:
            raise Exception("Get tenant token returned invalid JSON") from exc
        if "tenant_access_token" not in res:
            raise Exception(f"Get tenant token failed: {res}")
        return res["tenant_access_token"]

    def upload_excel_file_to_feishu(self, token: str, file_path: str):
        url = "https://open.feishu.cn/open-apis/im/v1/files"
        filename = os.path.basename(file_path)
        mime = mimetypes.guess_type(filename)[0] or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        headers = {"Authorization": f"Bearer {token}"}
        data = {"file_type": "xls", "file_name": filename}
        with open(file_path, "rb") as f:
            files = {"file": (filename, f, mime)}
            response = requests.post(url, headers=headers, data=data, files=files, timeout=60)
        response.raise_for_status()
        try:
            res = response.json()
        except ValueError as exc:
            raise Exception("Upload file returned invalid JSON") from exc
        if res.get("code") != 0 or not res.get("data", {}).get("file_key"):
            raise Exception(f"Upload file failed: {res}")
        return res["data"]["file_key"]

    def send_feishu_file_message(self, token: str, chat_id: str, file_key: str):
        url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
        payload = {
            "receive_id": chat_id,
            "msg_type": "file",
            "content": json.dumps({"file_key": file_key}, ensure_ascii=False),
        }
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        try:
            res = response.json()
        except ValueError as exc:
            raise Exception("Send file message returned invalid JSON") from exc
        if res.get("code") != 0:
            raise Exception(f"Send file message failed: {res}")
        return res

    def send_selected_excel_files_to_feishu(self, is_running=None):
        if is_running is None:
            is_running = lambda: self.running
        files = self.get_selected_excel_files_for_feishu()
        if not files:
            return

        active = getattr(self, "active_run_settings", None) or {}
        if active.get("chat_id"):
            chat_id = clean_input_value(active["chat_id"], collapse_internal_spaces=True)
        elif self.is_ui_thread() and hasattr(self, "chat_id_entry"):
            chat_id = entry_value(self.chat_id_entry, collapse_internal_spaces=True)
        else:
            feishu = self.config["FEISHU"] if "FEISHU" in self.config else {}
            chat_id = clean_input_value(feishu.get("CHAT_ID", ""), collapse_internal_spaces=True)
        if not chat_id:
            self.write_log("Excel file sending skipped — Chat ID is empty", level="WARN")
            return

        self.write_log(f"Excel attachment delivery started — {len(files)} file(s)", level="START")
        token = self.get_tenant_access_token_for_file_send()
        for file_path in files:
            if not is_running():
                return
            filename = os.path.basename(file_path)
            self.write_log(f"Uploading Excel file — {filename}", level="START")
            file_key = self.upload_excel_file_to_feishu(token, file_path)
            if not is_running():
                return
            self.write_log(f"Sending Excel file — {filename}", level="START")
            self.send_feishu_file_message(token, chat_id, file_key)
            if not is_running():
                return
            self.write_log(f"Excel file sent — {filename}", level="SUCCESS")
            time.sleep(0.8)

    def run_process(self, run_settings=None, run_generation=None):
        if self.running:
            return
        if not self.is_run_generation_active(run_generation):
            self.write_log("Skipped stale pipeline request", level="WARN")
            return
        if run_settings is None:
            if self.is_ui_thread():
                run_settings = self.prepare_run_settings()
            else:
                self.write_log("Run blocked: missing prepared settings", level="ERROR")
                return
        if not run_settings:
            return
        out = run_settings.get("out")
        if not out:
            return
        pipeline_error_key = None
        try:
            self.active_run_settings = run_settings
            if run_generation is None:
                run_generation = self.begin_run_request()
            else:
                self.stop_requested = False
                self.last_activity = time.time()
            self.running = True
            self.set_status("Processing", "#ebcb8b", "#4a4433")
            self.set_progress(8)
            self.set_pipeline_state(None, "ready")
            self.write_log("Pipeline started", level="START")

            run_export = bool(run_settings.get("run_export", True))
            run_chat = bool(run_settings.get("run_chat", True))

            if run_export:
                if not self.validate_dws_jms_ready():
                    raise Exception("DWS/JMS config is not ready")
                self.raw_time_source = "scheduler"
                raw_steps = [
                    ("dws", "DWS", self.run_dws),
                    ("jms_auto", "JMS AUTO", self.run_jms_auto),
                    ("jms_pda", "JMS PDA", self.run_jms_pda),
                    ("realtime", "Realtime DB", self.run_realtime_db),
                ]
                if not self.stop_requested:
                    self.prewarm_jms_exports(["建包扫描", "卸车扫描"])
                for idx, (key, name, func) in enumerate(raw_steps, start=1):
                    if not self.running or self.stop_requested or not self.is_run_generation_active(run_generation):
                        return
                    pipeline_error_key = key
                    self.set_pipeline_state(key, "run")
                    self.write_log(f"Raw export step {idx}/4 — {name}", level="START")
                    func()
                    self.set_pipeline_state(key, "ok")
                    pipeline_error_key = None
                    self.set_progress(8 + idx * 9)
                self.raw_time_source = "manual"
            else:
                for key in ["dws", "jms_auto", "jms_pda", "realtime"]:
                    self.set_pipeline_state(key, "skip")

            if run_chat:
                pipeline_error_key = "excel"
                self.set_pipeline_state("excel", "run")
                run_create(out, log=self.write_log, is_running=lambda: self.running and self.is_run_generation_active(run_generation))
                if not self.is_run_generation_active(run_generation):
                    return
                self.set_pipeline_state("excel", "ok")
                pipeline_error_key = None
                self.set_progress(62)
                if not self.running or not self.is_run_generation_active(run_generation):
                    self.set_status("Stopped", "#d3868e", "#4a3438")
                    return

                self.write_log("Feishu delivery started", level="START")
                self.set_status("Sending", "#88c0d0", "#3b4252")
                pipeline_error_key = "feishu"
                self.set_pipeline_state("feishu", "run")
                self.set_progress(76)
                try:
                    Botmessage.send_ui = lambda stage: self.set_status(stage.capitalize(), "#88c0d0", "#3b4252")
                    run_send(out, log=self.write_log, is_running=lambda: self.running and self.is_run_generation_active(run_generation))
                    if self.running and self.is_run_generation_active(run_generation):
                        self.send_selected_excel_files_to_feishu(
                            is_running=lambda: self.running and self.is_run_generation_active(run_generation)
                        )
                    self.set_pipeline_state("feishu", "ok")
                    pipeline_error_key = None
                finally:
                    Botmessage.send_ui = None
            else:
                self.set_pipeline_state("excel", "skip")
                self.set_pipeline_state("feishu", "skip")

            self.set_progress(100)
            self.write_log("Pipeline completed successfully", level="SUCCESS")
            if self.scheduler_running:
                next_run = self.get_next_scheduler_run_time()
                self.set_next_run_display(next_run)
                self.set_status("Auto Waiting", "#88c0d0", "#3b4252")
                self.write_log(f"Next auto run: {self.format_next_scheduler_run(next_run)}")
            else:
                self.set_status("Idle", "#a3be8c", "#3b4a3e")
        except Exception as e:
            self.write_log(f"Pipeline failed — {e}", level="ERROR")
            self.notify_export_token_error(e, "Auto Report raw export")
            self.set_pipeline_state(pipeline_error_key or "excel", "error")
            self.set_status("Error", "#d3868e", "#4a3438")
        finally:
            is_current = self.is_run_generation_active(run_generation)
            self.running = False
            if is_current:
                self.stop_requested = False
            self.active_run_settings = None
            self.raw_time_source = "manual"
            if is_current:
                self.current_run_source = None

            if is_current and not self.scheduler_running:
                self.set_ui_running(False)

            self.schedule_pipeline_reset(run_generation)

    def run_once(self):
        if self.running:
            self.write_log("Run Now blocked: another process is running")
            return

        run_settings = self.prepare_run_settings()
        if not run_settings:
            return

        self.mode = "manual"
        self.current_run_source = "manual"
        run_generation = self.begin_run_request()

        self.set_ui_running(True)
        self.task_queue.put(lambda settings=run_settings, g=run_generation: self.run_process(settings, g))

    def start_scheduler(self):
        if self.scheduler_running:
            self.write_log("Auto scheduler already running", level="WARN")
            return
        if self.is_busy():
            self.write_log("Start Auto blocked: another process is running", level="WARN")
            return
        run_settings = self.prepare_run_settings()
        if not run_settings:
            return
        self.auto_run_settings = run_settings
        self.mode = "auto"
        self.begin_run_request()
        self.scheduler_running = True
        self.last_run_minute = None

        self.set_ui_running(True)
        self.set_status("Auto Running", "#88c0d0", "#3b4252")
        next_run = self.get_next_scheduler_run_time()
        self.set_next_run_display(next_run)
        self.write_log(
            f"Auto scheduler started | minute={self.scheduler_run_minute:02} | data_window={self.scheduler_start_hour:02}:00-{self.scheduler_end_hour:02}:00",
            level="START",
        )
        self.write_log(f"Next auto run: {self.format_next_scheduler_run(next_run)}")
        threading.Thread(target=self.scheduler_loop, daemon=True).start()

    def scheduler_loop(self):
        while self.scheduler_running:
            try:
                now = datetime.now()
                minute = self.scheduler_run_minute
                now_key = now.strftime("%Y-%m-%d %H:%M")
                if now.minute == minute and self.last_run_minute != now_key:
                    if self.running:
                        self.last_run_minute = now_key
                        self.write_log(
                            f"Skip auto run {now.strftime('%H:%M')} (busy with {self.current_run_source})"
                        )
                        continue

                    self.last_run_minute = now_key
                    self.current_run_source = "auto"

                    self.write_log(f"Trigger auto run at {now.strftime('%H:%M')}")
                    settings = dict(self.auto_run_settings or {})
                    run_generation = self.begin_run_request()
                    self.set_next_run_display(self.get_next_scheduler_run_time(now + timedelta(minutes=1)))
                    self.task_queue.put(lambda settings=settings, g=run_generation: self.run_process(settings, g))

                # ตรวจ token เชิงรุกที่นาที :20/:40 (thread แยก ไม่บล็อกลูป)
                if now.minute in TOKEN_HEALTHCHECK_MINUTES and getattr(self, "last_token_check_minute", None) != now_key:
                    self.last_token_check_minute = now_key
                    threading.Thread(target=self.run_token_healthcheck, daemon=True).start()

                time.sleep(1)
            except Exception as e:
                self.write_log(f"Scheduler error: {e}")
                time.sleep(1)

    def stop_scheduler(self):
        self.scheduler_running = False

        self.btn_stop_auto.grid_remove()
        self.btn_start.configure(state="normal")
        self.set_next_run_display(clear=True)

        if not self.running:
            self.cancel_pending_runs()
            self.stop_requested = False
            self.set_ui_running(False)

        self.write_log("Auto scheduler stopped", level="SUCCESS")

        if self.running:
            self.set_status("Processing", "#ebcb8b", "#4a4433")
        else:
            self.set_status("Idle", "#a3be8c", "#3b4a3e")


    def stop_process(self):
        if not self.running:
            self.cancel_pending_runs()
            self.stop_requested = False
            return

        self.cancel_pending_runs()
        self.running = False
        self.stop_requested = True
        self.current_run_source = None

        self.btn_stop_run.grid_remove()
        self.btn_run.grid(
            row=3,
            column=8,
            padx=(8, 16),
            pady=(6, 16),
            sticky="ew"
        )

        if not self.scheduler_running:
            self.set_ui_running(False)

        self.write_log("Current process stopped by user", level="WARN")

        if self.scheduler_running:
            self.set_next_run_display(self.get_next_scheduler_run_time())
            self.set_status("Auto Waiting", "#88c0d0", "#3b4252")
        else:
            self.set_next_run_display(clear=True)
            self.set_status("Stopped", "#d3868e", "#4a3438")

    def stop_all(self):
        self.last_run_minute = None
        self.cancel_pending_runs()
        self.scheduler_running = False
        self.running = False
        self.stop_requested = True
        self.set_next_run_display(clear=True)
        self.set_status("Stopped", "#d3868e", "#4a3438")
        self.set_ui_running(False)
        self.write_log("All running tasks stopped", level="WARN")

    def get_feishu_config_value(self, key: str, default: str = "") -> str:
        entry_map = {
            "APP_ID": "app_id_entry",
            "APP_SECRET": "app_secret_entry",
            "CHAT_ID": "chat_id_entry",
            "BOT_PORT": "bot_port_entry",
            "BOT_NAME": "bot_name_entry",
            "VERIFY_TOKEN": "verify_token_entry",
            "NGROK_URL": "ngrok_url_entry",
            "JMS_TOKEN": "auth_token_entry",
            "AUTH_TOKEN": "auth_token_entry",
        }
        attr = entry_map.get(key)
        if attr and hasattr(self, attr):
            return entry_value(getattr(self, attr), collapse_internal_spaces=(key in {"APP_ID", "APP_SECRET", "CHAT_ID", "JMS_TOKEN", "AUTH_TOKEN", "BOT_PORT"}))
        if "FEISHU" in self.config:
            if key == "JMS_TOKEN":
                return self.config["FEISHU"].get("JMS_TOKEN", self.config["FEISHU"].get("AUTH_TOKEN", default))
            if key == "AUTH_TOKEN":
                return self.config["FEISHU"].get("AUTH_TOKEN", self.config["FEISHU"].get("JMS_TOKEN", default))
            return self.config["FEISHU"].get(key, default)
        return default

    def add_log(self, message):
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
        self.queue_log_line("controller", line)

    def jms_log(self, message):
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
        self.queue_log_line("jms", line)

    def load_controller_clients(self):
        self.controller_clients = []
        for section in self.config.sections():
            if not section.startswith("DWS") or not self.config.has_option(section, "IP"):
                continue
            try:
                port = self.config.getint(section, "PORT")
            except Exception:
                port = int(DEFAULT_CONTROLLER_CLIENTS.get(section, ("", "4000"))[1])
            self.controller_clients.append({
                "name": section,
                "ip": self.config.get(section, "IP", fallback=DEFAULT_CONTROLLER_CLIENTS.get(section, ("", ""))[0]),
                "port": port,
            })
        self.controller_clients.sort(key=lambda c: list(DEFAULT_CONTROLLER_CLIENTS).index(c["name"]) if c["name"] in DEFAULT_CONTROLLER_CLIENTS else 999)

    def load_dynamic_plans(self):
        self.dynamic_plans.clear()
        for client in self.controller_clients:
            try:
                response = requests.get(f"http://{client['ip']}:{client['port']}/plans", timeout=3)
                data = response.json()
                if data.get("success"):
                    for plan in data.get("plans", []):
                        if plan not in self.dynamic_plans:
                            self.dynamic_plans.append(plan)
                    return
            except Exception:
                pass
        self.dynamic_plans = ["DWSA", "DWSB"]

    def start_controller_api(self):
        global controller_instance
        controller_instance = self
        try:
            register_controller(self)
            threading.Thread(target=start_api, daemon=True).start()
            self.add_log("[SYSTEM] Controller API Started : 6100")
        except Exception as e:
            self.add_log(f"[ERROR] Controller API failed -> {e}")

    def update_bot_ui(self):
        if not hasattr(self, "btn_start_bot"):
            return
        if self.bot_running:
            self.btn_start_bot.grid_remove()
            self.btn_stop_bot.grid(row=0, column=3, padx=6, pady=14)
            self.bot_status_label.configure(text="Bot : ONLINE", text_color="#a3be8c")
        else:
            self.btn_stop_bot.grid_remove()
            self.btn_start_bot.grid(row=0, column=3, padx=6, pady=14)
            self.bot_status_label.configure(text="Bot : OFFLINE", text_color="#d3868e")

    def update_jms_ui(self):
        if not hasattr(self, "btn_start_jms"):
            return
        if self.jms_running:
            self.btn_start_jms.grid_remove()
            self.btn_stop_jms.grid(row=0, column=0, padx=(16, 8), pady=14)
            self.jms_status_label.configure(text="Bot : ONLINE", text_color="#a3be8c")
        else:
            self.btn_stop_jms.grid_remove()
            self.btn_start_jms.grid(row=0, column=0, padx=(16, 8), pady=14)
            self.jms_status_label.configure(text="Bot : OFFLINE", text_color="#d3868e")

    def refresh_status(self):
        if not hasattr(self, "controller_status_frame"):
            return
        threading.Thread(target=self.refresh_status_thread, daemon=True).start()

    def refresh_status_thread(self):
        rows = []
        for client in self.controller_clients:
            try:
                response = requests.get(f"http://{client['ip']}:{client['port']}/status", timeout=3)
                data = response.json()
                rows.append({
                    "pc": client["name"],
                    "ip": client["ip"],
                    "port": client["port"],
                    "status": "ONLINE" if data.get("success") else "ERROR",
                    "plan": data.get("current_plan", "-"),
                    "message": data.get("message", ""),
                })
            except Exception as e:
                rows.append({"pc": client["name"], "ip": client["ip"], "port": client["port"], "status": "OFFLINE", "plan": "-", "message": str(e)})
        self.after(0, lambda: self.render_controller_status(rows))

    def render_controller_status(self, rows):
        frame = getattr(self, "controller_status_frame", None)
        if frame is None:
            return
        for widget in frame.winfo_children():
            widget.destroy()
        headers = [("pc", "PC"), ("ip", "IP Address"), ("port", "Port"), ("status", "Status"), ("plan", "Current Plan"), ("message", "Message")]
        for col, (_, title) in enumerate(headers):
            ctk.CTkLabel(frame, text=title, text_color="#aeb8cc", font=ctk.CTkFont(weight="bold")).grid(row=0, column=col, padx=8, pady=(8, 5), sticky="w")
        for idx, row in enumerate(rows, start=1):
            status_color = "#a3be8c" if row["status"] == "ONLINE" else "#d3868e"
            values = [row["pc"], row["ip"], row["port"], row["status"], row["plan"], row["message"]]
            for col, value in enumerate(values):
                color = status_color if col == 3 else "#d8dee9"
                ctk.CTkLabel(frame, text=str(value), text_color=color, anchor="w").grid(row=idx, column=col, padx=8, pady=4, sticky="ew")

    def switch_single_client(self, client, target_plan):
        try:
            response = requests.post(f"http://{client['ip']}:{client['port']}/switch_plan", json={"plan": target_plan}, timeout=5)
            data = response.json()
            if data.get("success"):
                self.add_log(f"[OK] {client['name']} -> {target_plan}")
            else:
                self.add_log(f"[FAIL] {client['name']} -> {data.get('message')}")
        except Exception as e:
            self.add_log(f"[ERROR] {client['name']} -> {e}")

    def switch_plan_thread(self, target_plan):
        self.add_log(f"[SYSTEM] SWITCH PLAN -> {target_plan}")
        with ThreadPoolExecutor(max_workers=20) as executor:
            for client in self.controller_clients:
                executor.submit(self.switch_single_client, client, target_plan)
        self.refresh_status()

    def switch_plan(self, target_plan):
        threading.Thread(target=self.switch_plan_thread, args=(target_plan,), daemon=True).start()

    def change_plan_from_entry(self):
        target_plan = self.controller_plan_entry.get().strip() if hasattr(self, "controller_plan_entry") else ""
        if not target_plan:
            self.add_log("[ERROR] PLAN EMPTY")
            return
        self.switch_plan(target_plan)

    def start_jms_placeholder(self):
        self.save_config()
        if not self.get_jms_auth_token():
            self.jms_running = False
            self.update_jms_ui()
            self.jms_log("[ERROR] Missing JMS_TOKEN")
            self.notify_it_alert(
                "missing_jms_token_bot",
                "JMS_TOKEN ยังไม่ได้ตั้งค่า",
                "BOT JMS USER ไม่สามารถเริ่มทำงานได้ กรุณาอัปเดต JMS_TOKEN ที่หน้า ตั้งค่า"
            )
            messagebox.showwarning("JMS_TOKEN", "ยังไม่ได้ตั้งค่า JMS_TOKEN\nกรุณาอัปเดต JMS_TOKEN ที่หน้า ตั้งค่า")
            return
        if not self.bot_running:
            self.start_feishu_bot()
        self.jms_running = True
        self.update_jms_ui()
        self.jms_log("[SYSTEM] JMS BOT ONLINE")

    def stop_jms_placeholder(self):
        self.jms_running = False
        self.update_jms_ui()
        self.jms_log("[SYSTEM] JMS BOT OFFLINE")

    def run_feishu_server(self):
        try:
            port = int(self.get_feishu_config_value("BOT_PORT", "7000"))
        except Exception:
            port = 7000
        self.add_log(f"[SYSTEM] Feishu Server Running : {port}")
        try:
            serve(bot_app, host="0.0.0.0", port=port, threads=20)
        except Exception as e:
            self.bot_running = False
            self.bot_thread = None
            self.add_log(f"[ERROR] Feishu Server stopped -> {e}")
            self.run_on_ui_thread(self.update_bot_ui)

    def start_feishu_bot(self):
        if self.bot_running:
            return
        self.save_config()
        self.bot_running = True
        if self.bot_thread is not None and self.bot_thread.is_alive():
            self.update_bot_ui()
            self.add_log("[SYSTEM] Feishu Bot Re-enabled")
            return
        self.bot_thread = threading.Thread(target=self.run_feishu_server, daemon=True)
        self.bot_thread.start()
        self.update_bot_ui()
        self.add_log("[SYSTEM] Feishu Bot Started")

    def stop_feishu_bot(self):
        self.bot_running = False
        self.update_bot_ui()
        self.add_log("[SYSTEM] Bot Marked As Offline")
        self.add_log("[INFO] Restart program to fully stop server")

    def handle_jms_command(self, text, chat_id, message_id, parent_id=None, root_id=None):
        lower_text = text.lower()
        normalized_lower_text = re.sub(r"\s+", " ", lower_text).strip()
        app_keywords = ["รีapp", "รี app", "รีแอพ", "รี แอพ", "รีรหัสapp", "รีรหัส app", "รีรหัสแอพ", "รีรหัส แอพ", "รีแอป", "รี แอป", "รีรหัสแอป", "รีรหัส แอป", "รีเซ็ตapp", "reset app", "reset password app", "รีแอพให้หน่อย", "รีรหัสแอพให้หน่อย", "รีรหัสpda", "รีรหัสpdaให้หน่อย", "รีรหัส pda ให้หน่อย"]
        jms_keywords = ["รีjms", "รีรหัสjms", "รีรหัส jms", "รี jms", "รีเซ็ตjms", "reset jms", "reset password jms", "รี jms ให้หน่อย", "รีรหัส jms ให้หน่อย"]
        enable_keywords = ["เปิดยูส", "เปิด user", "enable", "เปิดใช้งาน", "ปลดล็อค", "ปลดล้อค", "unlock", "ระงับ", "โดนระงับ", "เข้าไม่ได้", "ใช้งานไม่ได้", "ปลด user", "เปิดรหัส", "เปิดไอดี"]
        staff_list = extract_staff_numbers(text, self.get_feishu_config_value("BOT_NAME", "BOT_JMSKKN"))
        command_type = detect_jms_intent(text)
        generic_reset_request = bool(staff_list and contains_any(normalized_lower_text, ["รี", "รีรหัส", "รีเซ็ต", "reset", "password", "รหัส"]))
        if not command_type and any(keyword in lower_text or keyword in normalized_lower_text for keyword in app_keywords):
            command_type = "APP"
        elif not command_type and any(keyword in lower_text or keyword in normalized_lower_text for keyword in jms_keywords):
            command_type = "JMS"
        elif not command_type and any(keyword in lower_text or keyword in normalized_lower_text for keyword in enable_keywords):
            command_type = "ENABLE"
        elif not command_type and generic_reset_request:
            command_type = "LOOKUP_ONLY"
        elif command_type:
            pass
        elif staff_list and any(keyword in lower_text or keyword in normalized_lower_text for keyword in ["ล็อค", "ล๊อค", "โดนล็อค", "โดนล๊อค", "locked", "ระงับ", "โดนระงับ", "เข้าไม่ได้", "ใช้งานไม่ได้", "ปลดล็อค", "ปลดล้อค", "unlock", "รหัสปิด"]):
            command_type = "ENABLE"
        else:
            return False
        if not self.jms_running:
            self.jms_log("[SKIP] JMS BOT OFFLINE")
            self.notify_it_alert(
                "bot_jms_user_offline",
                "ต้องเปิด BOT JMS USER",
                "มีคำสั่ง JMS จาก Feishu เข้ามา แต่ BOT JMS USER ยังไม่ได้ START BOT"
            )
            return True
        if not staff_list:
            reply_feishu_message(message_id, "❌ ไม่พบ USER")
            return True

        success_text, fail_text = [], []
        for staff_no in staff_list:
            try:
                self.jms_log(f"[SEARCH] {staff_no}")
                user = search_user(staff_no)
                if not user or not isinstance(user, dict):
                    fail_text.append(f"❌ ไม่พบ USER {staff_no}")
                    continue
                user_id = user.get("id")
                user_name = str(user.get("name") or "")
                if not user_id:
                    fail_text.append(f"❌ USER DATA INVALID {staff_no}")
                    continue
                if command_type == "APP":
                    new_password = reset_app_password(user_id)
                    enable_user(user)
                    success_text.append(f"Name : {user_name}\nUser : {staff_no}\nAPP Password : {new_password}\nStatus : เปิดใช้งานแล้ว")
                    self.jms_log(f"[RESET APP + ENABLE] {staff_no}")
                    write_log(status="SUCCESS", user=staff_no, name=user_name, action="APP", detail=f"PASSWORD : {new_password} | USER ENABLE SENT")
                elif command_type == "JMS":
                    new_password = reset_jms_password(user_id)
                    enable_user(user)
                    success_text.append(f"Name : {user_name}\nUser : {staff_no}\nJMS Password : {new_password}\nStatus : เปิดใช้งานแล้ว")
                    self.jms_log(f"[RESET JMS + ENABLE] {staff_no}")
                    write_log(status="SUCCESS", user=staff_no, name=user_name, action="JMS", detail=f"PASSWORD : {new_password} | USER ENABLE SENT")
                elif command_type == "ENABLE":
                    enable_user(user)
                    success_text.append(f"Name : {user_name}\nUser : {staff_no}\nStatus : เปิดใช้งานแล้ว")
                    self.jms_log(f"[ENABLE USER] {staff_no}")
                    write_log(status="SUCCESS", user=staff_no, name=user_name, action="ENABLE", detail="USER ENABLE SENT")
                elif command_type == "LOOKUP_ONLY":
                    fail_text.append(f"❌ ระบุคำสั่งไม่ชัดเจน {staff_no} : กรุณาพิมพ์ รี app หรือ รี jms")
            except Exception as e:
                error_text = str(e)
                if "JMS_TOKEN" in error_text or "Missing JMS_TOKEN" in error_text:
                    self.notify_it_alert(
                        "jms_token_bot_failed",
                        "JMS_TOKEN ใช้งานไม่ได้",
                        "กรุณาอัปเดต JMS_TOKEN ที่หน้า ตั้งค่า แล้วกด START BOT ที่หน้า BOT JMS USER"
                    )
                    self.jms_log(f"[ERROR] {staff_no} -> {e}")
                    write_log(status="FAILED", user=staff_no, action=command_type, detail=str(e))
                    continue
                display_error = re.sub(r"\s*\(isEnable=.*?\)", "", error_text)
                if display_error.startswith("เปิดใช้งานไม่สำเร็จ:"):
                    display_error = "เปิดใช้งานไม่สำเร็จ " + staff_no + " : " + display_error.split(":", 1)[1].strip()
                    fail_text.append(f"❌ {display_error}")
                else:
                    fail_text.append(f"{staff_no} : {display_error}")
                self.jms_log(f"[ERROR] {staff_no} -> {e}")
                write_log(status="FAILED", user=staff_no, action=command_type, detail=str(e))

        final_message = ""
        if success_text:
            final_message += "ดำเนินการเสร็จเรียบร้อย\n\n" + "\n\n".join(success_text)
        if fail_text:
            final_message += "\n\n⚠ FAILED\n\n" + "\n".join(fail_text)
        if not final_message:
            return True
        if len(final_message) > 3000:
            for i in range(0, len(final_message), 3000):
                reply_feishu_message(message_id, final_message[i:i + 3000])
        else:
            reply_feishu_message(message_id, final_message)
        return True

    def on_close(self):
        self.stop_all()
        self.worker_running = False
        self.destroy()


if __name__ == "__main__":
    if not acquire_single_instance_lock():
        messagebox.showwarning("Auto Report", "โปรแกรมกำลังเปิดใช้งานอยู่แล้ว")
        sys.exit(0)
    app = App()
    app.mainloop()
