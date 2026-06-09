import os
import threading
import configparser
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from collections import deque
import time
from controller.controller_api import (
    register_controller,
    start_api
)

import requests
from flask import Flask, request, jsonify
from waitress import serve

import json
import re
import lark_oapi as lark

from core.jms_api import (
    search_user,
    reset_app_password,
    reset_jms_password,
    enable_user
)

from core.logger import write_log

import tkinter as tk
from tkinter import ttk, font
from tkinter.scrolledtext import ScrolledText
from tkinter import scrolledtext
from tkinter import Text

# =========================================
# CONFIG
# =========================================
CONFIG_FILE = "config.ini"

DEFAULT_CLIENTS = {
    "DWS1": ("10.30.32.32", "4000"),
    "DWS2": ("10.30.32.33", "4000"),
    "DWS3": ("10.30.32.34", "4000"),
    "DWS4": ("10.30.32.35", "4000"),
    "DWS5": ("10.30.32.36", "4000"),
    "DWS6": ("10.30.32.37", "4000"),
    "DWS7": ("10.30.32.38", "4000"),
    "DWS8": ("10.30.32.39", "4000"),
    "DWS9-11": ("10.30.32.10", "4001")
}

# =========================================
# FEISHU BOT APP
# =========================================
bot_app = Flask(__name__)
controller_instance = None

def get_tenant_access_token():

    global controller_instance

    try:

        app_id = controller_instance.feishu_entries["APP_ID"].get().strip()
        app_secret = controller_instance.feishu_entries["APP_SECRET"].get().strip()

        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"

        response = requests.post(
            url,
            json={
                "app_id": app_id,
                "app_secret": app_secret
            },
            timeout=10
        )

        data = response.json()

        return data.get("tenant_access_token")

    except Exception as e:

        print(e)
        return None

def reply_feishu_message(
    message_id,
    text
):

    token = get_tenant_access_token()

    if not token:
        return False

    url = (
        "https://open.feishu.cn/open-apis/"
        f"im/v1/messages/{message_id}/reply"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "content": json.dumps({
            "text": text
        }),
        "msg_type": "text"
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=10
    )

    return response.status_code == 200
   
@bot_app.route("/status")
def bot_status():

    return jsonify({
        "success": True,
        "status": "online",
        "message": "Feishu Bot Online"
    })

@bot_app.route("/feishu_event", methods=["POST"])

def feishu_event():

    global controller_instance

    data = request.get_json(silent=True) or {}

    # ====================================
    # URL VERIFY
    # ====================================
    if "challenge" in data:

        return jsonify({
            "challenge": data["challenge"]
        })
    # ====================================
    # EVENT
    # ====================================
    event = data.get("event", {})
        # ====================================
    # DUPLICATE EVENT PROTECTION
    # ====================================
    event_id = (
        data
        .get("header", {})
        .get("event_id")
    )

    if event_id:

        if event_id in controller_instance.processed_events:

            controller_instance.jms_log(
                f"[SKIP DUPLICATE] {event_id}"
            )

            return jsonify({
                "success": True,
                "message": "duplicate_skip"
            })

        controller_instance.processed_events.append(
            event_id
        )
    message = event.get("message", {})
    chat_id = message.get("chat_id")
    #sender = event.get("sender", {})

    message_id = message.get("message_id")
    parent_id = message.get("parent_id")
    root_id = message.get("root_id")

    content_raw = message.get("content", "{}")

    try:

        content_json = json.loads(content_raw)

        text = content_json.get("text", "").strip()
        text = text.replace("\\n", "\n")

        lower_text = text.lower()
        normalized_lower_text = re.sub(
            r"\s+",
            " ",
            lower_text
        ).strip()

        # ====================================
        # REQUIRE BOT MENTION
        # ====================================
        if message.get("chat_type") != "p2p":

            mentions = message.get("mentions", [])

            bot_mentioned = False

            for mention in mentions:

                mention_name = (
                    mention.get("name", "")
                    .strip()
                    .lower()
                )

                bot_name = (
                    controller_instance
                    .feishu_entries["BOT_NAME"]
                    .get()
                    .strip()
                    .lower()
                )

                if mention_name == bot_name:

                    bot_mentioned = True
                    break

            if not bot_mentioned:

                return jsonify({
                    "status": "skip_no_mention"
                })
            
    except:

        return jsonify({"success": False})

    controller_instance.add_log(
        f"[FEISHU] {text}"
    )

        # =====================================
    # HELP
    # =====================================

    def send_help():

        reply_feishu_message(
            message_id,
            (
                "คำสั่งไม่ถูกต้อง ตรวจสอบใหม่อีกครั้ง\n\n"
                "รีเซ็ตรหัส:\n"
                "@BOT รีรหัส app 999004T000XX\n"
                "@BOT รีรหัส jms 999004T000XX\n\n"
                "รหัสล็อค:\n"
                "@BOT ปลดล็อค 999004T000XX\n\n"
                "เปลี่ยนแพลน:\n"
                "@BOT เปลี่ยนแพลนบ่าย\n"
                "@BOT เปลี่ยนแพลนดึก\n"
                )
            )
        
    # ====================================
    # GLOBAL COMMAND FILTER
    # ====================================

    command_keywords = [

        "รีapp",
        "รี app",
        "รีแอพ",
        "รี แอพ",
        "รีรหัสapp",
        "รีรหัส app",
        "รีรหัสแอพ",
        "รีรหัส แอพ",
        "รีแอป",
        "รี แอป",
        "รีรหัสแอป",
        "รีรหัส แอป",
        "รีเซ็ตapp",
        "reset app",
        "reset password app",
        "รีแอพให้หน่อย",
        "รีรหัสแอพให้หน่อย",
        "รีรหัสpda",
        "รีรหัสpdaให้หน่อย",
        "รีรหัส pda ให้หน่อย",

        "รีjms",
        "รีรหัสjms",
        "รีรหัส jms",
        "รี jms",
        "รีเซ็ตjms",
        "reset jms",
        "reset password jms",
        "รี jms ให้หน่อย",
        "รีรหัส jms ให้หน่อย",

        "เปิดยูส",
        "เปิด user",
        "enable",
        "เปิดใช้งาน",
        "ปลดล็อค",
        "ปลดล้อค",
        "unlock",
        "ระงับ",
        "โดนระงับ",
        "เข้าไม่ได้",
        "ใช้งานไม่ได้",
        "ปลด user",
        "เปิดรหัส",
        "เปิดไอดี",

        # DWS
        "dwsa",
        "กะบ่าย",
        "แพลนบ่าย",
        "เปลี่ยนกะบ่าย",
        "เปลี่ยนแพลนบ่าย",
        "dwsb",
        "กะดึก",
        "แพลนดึก",
        "เปลี่ยนกะดึก",
        "เปลี่ยนแพลนดึก",
    ]

    # ====================================
    # FLEXIBLE COMMAND PARSER
    # ====================================

    command_lines = []

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    if not lines:
        lines = [text]

    current_command = []

    for line in lines:

        lower_line = line.lower()

        has_keyword = any(
            keyword in lower_line
            for keyword in command_keywords
        )

        has_user = bool(
            re.search(
                r"\b(?:\d{8}|[0-9A-Z]{10,20})\b",
                line.upper()
            )
        )

        # =========================
        # START NEW COMMAND
        # =========================
        if has_keyword:

            if current_command:
                command_lines.append(
                    "\n".join(current_command)
                )

            current_command = [line]
            continue

        # =========================
        # USER LINE
        # =========================
        if has_user:

            # ยังไม่มี command มาก่อน
            if not current_command:

                current_command = [line]

            else:

                current_command.append(line)

            continue

    # append last
    if current_command:

        command_lines.append(
            "\n".join(current_command)
        )

    # fallback
    if not command_lines:

        command_lines = [text]
    # ====================================
    # COMMAND (MULTI-LINE SUPPORT)
    # ====================================
    processed_any = False

    for command_text in command_lines:

        command_lower = command_text.lower()
        # รองรับ user อยู่หน้า command
        parts = command_text.splitlines()

        merged_text = " ".join(parts)

        command_lower = merged_text.lower()
        normalized_command_lower = re.sub(
            r"\s+",
            " ",
            merged_text.lower()
        ).strip()

        # ====================================
        # JMS COMMAND
        # ====================================
        try:

            handled = controller_instance.handle_jms_command(
                command_text,
                chat_id,
                message_id,
                parent_id,
                root_id
            )

        except Exception as e:

            print("JMS ERROR:", e)

            controller_instance.jms_log(
                f"[ERROR] {e}"
            )

            return jsonify({
                "success": False,
                "error": str(e)
            })

        if handled:
            processed_any = True
            continue

        target_plan = None

        if re.search(
            r"(เปลี่ยน|สลับ).*(บ่าย|dwsa)",
            normalized_command_lower
        ):
            target_plan = "DWSA"

        elif re.search(
            r"(เปลี่ยน|สลับ).*(ดึก|dwsb)",
            normalized_command_lower
        ):
            target_plan = "DWSB"

        if target_plan:

            controller_instance.switch_plan(
                target_plan
            )

            reply_feishu_message(
                message_id,
                (
                    f"PLAN : {target_plan}\n"
                    f"ดำเนินการเปลี่ยนแพลนเสร็จเรียบร้อย\n"
                    f"ปิดโปรแกรมแล้วเข้าใหม่อีกครั้ง"
                )
            )

            controller_instance.add_log(
                f"[FEISHU] SWITCH -> {target_plan}"
            )

            processed_any = True
            continue

        if "/status" in command_lower:

            reply_feishu_message(
                message_id,
                "🟢 Controller Online"
            )

            processed_any = True

    if processed_any:
        return jsonify({
            "success": True,
            "message": "ignored"
        })

    send_help()
    return jsonify({
        "success": True,
        "message": "ignored"
    })

@bot_app.route("/switch_plan", methods=["POST"])

def bot_switch_plan():

    global controller_instance

    data = request.get_json(silent=True) or {}

    plan = data.get("plan", "").strip()

    if not plan:

        return jsonify({
            "success": False,
            "message": "Plan Empty"
        })

    if controller_instance:

        controller_instance.switch_plan(plan)

    return jsonify({
        "success": True,
        "message": f"Switching to {plan}"
    })
# =========================================
# MAIN CLASS
# =========================================
class ControllerGUI:

    def setup_theme(self):

        style = ttk.Style()

        style.theme_use("clam")

        BG = "#f5f7fa"
        CARD = "#ffffff"
        BORDER = "#d0d7de"
        TEXT = "#1f2328"

        # ROOT
        self.root.configure(bg=BG)

        # NOTEBOOK
        style.configure(
            "TNotebook",
            background=BG,
            borderwidth=0
        )

        style.configure(
            "TNotebook.Tab",
            background="#e9edf2",
            foreground=TEXT,
            padding=(14, 7),
            font=("Segoe UI", 9, "bold")
        )

        style.map(
            "TNotebook.Tab",
            background=[("selected", CARD)],
            foreground=[("selected", "#000000")]
        )

        # FRAME
        style.configure(
            "TFrame",
            background=BG
        )

        style.configure(
            "TLabelframe",
            background=BG,
            bordercolor=BORDER,
            relief="solid"
        )

        style.configure(
            "TLabelframe.Label",
            background=BG,
            foreground=TEXT,
            font=("Segoe UI", 9, "bold")
        )

        # LABEL
        style.configure(
            "TLabel",
            background=BG,
            foreground=TEXT,
            font=("Segoe UI", 9)
        )

        # BUTTON
        style.configure(
            "TButton",
            font=("Segoe UI", 9),
            padding=6
        )

        style.configure(
            "Green.TButton",
            font=("Segoe UI", 9, "bold"),
            foreground="#0a7d28"
        )

        style.configure(
            "Red.TButton",
            font=("Segoe UI", 9, "bold"),
            foreground="#c62828"
        )

        style.configure(
            "Black.TButton",
            font=("Segoe UI", 9, "bold"),
            foreground="#1f2328"
        )

        # ENTRY
        style.configure(
            "TEntry",
            fieldbackground="#ffffff",
            padding=5
        )

        # TREEVIEW
        style.configure(
            "Treeview",
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground=TEXT,
            rowheight=26,
            bordercolor=BORDER,
            font=("Segoe UI", 9)
        )

        style.configure(
            "Treeview.Heading",
            background="#eef2f6",
            foreground=TEXT,
            font=("Segoe UI", 9, "bold")
        )

        # SCROLLBAR
        style.configure(
            "Vertical.TScrollbar",
            background="#dce3ea",
            troughcolor="#f5f7fa",
            bordercolor="#dce3ea",
            arrowcolor="#555"
        )

    def __init__(self):

        # =========================
        # CONFIG
        # =========================
        self.config = configparser.ConfigParser()

        self.create_default_config()
        self.load_config()

        # =========================
        # ROOT
        # =========================
        self.root = tk.Tk()
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        self.setup_theme()

        self.root.title("Feishu Bot Main Controller")
        self.root.geometry("950x750")
        self.root.resizable(False, False)

        # =========================
        # MAIN FRAME
        # =========================
        self.main_frame = ttk.Frame(
            self.root,
            padding=10
        )
        self.main_frame.grid_rowconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(0, weight=1)

        self.main_frame.grid(
            row=0,
            column=0,
            sticky="nsew"
        )

        # =========================
        # NOTEBOOK
        # =========================
        self.notebook = ttk.Notebook(
            self.main_frame
        )

        self.notebook.grid(
            row=0,
            column=0,
            sticky="nsew"
        )

        # =========================
        # TAB HOME
        # =========================
        self.tab_dws = ttk.Frame(
            self.notebook
        )
        self.tab_dws.grid_rowconfigure(1, weight=1)
        self.tab_dws.grid_columnconfigure(0, weight=1)

        self.notebook.add(
            self.tab_dws,
            text="DWS Plan"
        )

        self.tab_jms = ttk.Frame(
            self.notebook
        )
        self.tab_jms.grid_rowconfigure(1, weight=1)
        self.tab_jms.grid_columnconfigure(0, weight=1)

        self.notebook.add(
            self.tab_jms,
            text="JMS User"
        )
        # =========================
        # TAB SETTING
        # =========================
        self.tab_setting = ttk.Frame(
            self.notebook
        )
        self.tab_setting.grid_rowconfigure(0, weight=1)
        self.tab_setting.grid_rowconfigure(1, weight=1)
        self.tab_setting.grid_rowconfigure(2, weight=0)
        self.tab_setting.grid_rowconfigure(3, weight=0)

        self.tab_setting.grid_columnconfigure(0, weight=1)

        self.notebook.add(
            self.tab_setting,
            text="Setting"
        )

        # =========================
        # STARTUP
        # =========================
        self.dynamic_plans = []

        self.bot_running = False

        self.jms_running = False

        self.processed_events = deque(maxlen=1000)

        self.load_dynamic_plans()

        # BUILD UI
        self.build_dws_tab()
        self.build_jms_tab()
        self.build_setting_tab()
        self.update_bot_ui()
        self.refresh_status()

        self.start_auto_refresh()

        register_controller(self)
        global controller_instance
        controller_instance = self

        threading.Thread(
            target=start_api,
            daemon=True
        ).start()

        self.add_log(
            "[SYSTEM] Controller API Started : 6100"
        )


    # =====================================
    # CONFIG
    # =====================================
    def create_default_config(self):

        if os.path.exists(CONFIG_FILE):
            return

        config = configparser.ConfigParser()

        config["FEISHU"] = {
            "APP_ID": "",
            "APP_SECRET": "",
            "VERIFY_TOKEN": "mytoken",
            "BOT_PORT": "7000",
            "NGROK_URL": "",
            "AUTH_TOKEN": "",
            "BOT_NAME": "BOT_JMSKKN"
            
        }
        
        for name, value in DEFAULT_CLIENTS.items():

            ip, port = value

            config[name] = {
                "IP": ip,
                "PORT": port
            }

        with open(CONFIG_FILE, "w") as f:

            config.write(f)

    
    def load_config(self):

        self.config.read(CONFIG_FILE)

        default_clients = {
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

        for name, (ip, port) in default_clients.items():

            if not self.config.has_section(name):
                self.config.add_section(name)

            if not self.config.has_option(name, "IP"):
                self.config.set(name, "IP", ip)

            if not self.config.has_option(name, "PORT"):
                self.config.set(name, "PORT", port)

        if not self.config.has_section("FEISHU"):
            self.config.add_section("FEISHU")
            

        defaults = {
            "APP_ID": "",
            "APP_SECRET": "",
            "VERIFY_TOKEN": "mytoken",
            "BOT_PORT": "7000",
            "NGROK_URL": "",
            "AUTH_TOKEN": "",
            "BOT_NAME": "BOT_JMSKKN"
        }

        for key, value in defaults.items():

            if not self.config.has_option("FEISHU", key):
                self.config.set("FEISHU", key, value)

        with open(CONFIG_FILE, "w") as f:
            self.config.write(f)

        self.config.read(CONFIG_FILE)
        self.clients = []

        self.feishu_config = self.config["FEISHU"]

        for section in self.config.sections():

            if not section.startswith("DWS"):
                continue

            self.clients.append({
                "name": section,
                "ip": self.config.get(section, "IP"),
                "port": self.config.getint(section, "PORT")
            })

    def save_config(self):

        for row in self.setting_rows:

            name = row["name"]

            ip = row["ip_var"].get()
            port = row["port_var"].get()

            if not self.config.has_section(name):

                self.config.add_section(name)

            self.config.set(name, "IP", ip)
            self.config.set(name, "PORT", port)

        with open(CONFIG_FILE, "w") as f:

            self.config.write(f)

        # RELOAD CONFIG
        self.load_config()

        # RELOAD JMS API
        try:

            import importlib
            import core.jms_api

            importlib.reload(core.jms_api)

            self.add_log(
                "[SYSTEM] JMS API Reloaded"
            )

        except Exception as e:

            self.add_log(
                f"[ERROR] Reload JMS API -> {e}"
            )

        self.add_log(
            "[SYSTEM] All Config Saved"
        )

    def save_all_config(self):

        # CLIENTS
        for row in self.setting_rows:

            name = row["name"]

            ip = row["ip_var"].get()
            port = row["port_var"].get()

            if not self.config.has_section(name):

                self.config.add_section(name)

            self.config.set(name, "IP", ip)
            self.config.set(name, "PORT", port)

        # FEISHU
        if not self.config.has_section("FEISHU"):
            self.config.add_section("FEISHU")

        for key, entry in self.feishu_entries.items():
            value = entry.get().strip()

            self.config.set(
                "FEISHU",
                key,
                value
            )

        # FORCE SAVE NGROK_URL
        if "NGROK_URL" in self.feishu_entries:

            self.config.set(
                "FEISHU",
                "NGROK_URL",
                self.feishu_entries["NGROK_URL"].get().strip()
            )

        with open(CONFIG_FILE, "w") as f:

            self.config.write(f)

        self.add_log(
            "[SYSTEM] All Config Saved"
        )

        self.load_config()

    # =====================================
    # LOG
    # =====================================
    def add_log(self, message):

        now = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        full_message = f"[{now}] {message}"

        self.log_text.config(state="normal")

        self.log_text.insert(
            tk.END,
            full_message + "\n"
        )

        self.log_text.see(tk.END)

        self.log_text.config(state="disabled")

    def update_setting_tab_state(self):

        disable = (
            self.bot_running
            or self.jms_running
        )

        state = "disabled" if disable else "normal"

        # CLIENT CONFIG
        for row in self.setting_rows:

            for widget in self.setting_frame.winfo_children():

                try:
                    widget.configure(state=state)
                except:
                    pass

        # FEISHU CONFIG
        for widget in self.feishu_frame.winfo_children():

            try:
                widget.configure(state=state)
            except:
                pass

        # JMS CONFIG
        for widget in self.jms_setting_frame.winfo_children():

            try:
                widget.configure(state=state)
            except:
                pass

        # SAVE BUTTON
        try:
            self.btn_save_all.configure(state=state)
        except:
            pass

    def update_bot_ui(self):

        if self.bot_running:

            self.btn_start_bot.grid_remove()

            self.btn_stop_bot.grid(
                row=0,
                column=0
            )

            self.bot_status_label.config(
                text="Bot : ONLINE",
                foreground="green",
                font=("Arial", 9, "bold")
                
            )

        else:

            self.btn_stop_bot.grid_remove()

            self.btn_start_bot.grid(
                row=0,
                column=0
            )

            self.bot_status_label.config(
                text="Bot : OFFLINE",
                foreground="red",
                font=("Arial", 9, "bold")
            )

    # =====================================
    # LOAD DYNAMIC PLANS
    # =====================================
    def load_dynamic_plans(self):

        self.dynamic_plans.clear()

        for client in self.clients:

            try:

                url = f"http://{client['ip']}:{client['port']}/plans"

                response = requests.get(
                    url,
                    timeout=3
                )

                data = response.json()

                if data.get("success"):

                    for plan in data["plans"]:

                        if plan not in self.dynamic_plans:

                            self.dynamic_plans.append(plan)

                    return

            except:
                pass

        # fallback
        self.dynamic_plans = [
            "DWSA",
            "DWSB"
        ]

    # =====================================
    # HOME TAB
    # =====================================
    def build_dws_tab(self):

        self.tab_dws.rowconfigure(1, weight=4)
        self.tab_dws.rowconfigure(2, weight=2)

        self.tab_dws.columnconfigure(0, weight=1)

        # =========================
        # CONTROL FRAME
        # =========================
        self.control_frame = ttk.LabelFrame(
            self.tab_dws,
            text="Plan Control",
            padding=10
        )

        self.control_frame.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=5,
            pady=5
        )

        # =========================
        # PLAN ENTRY
        # =========================
        self.plan_var = tk.StringVar()

        self.entry_plan = ttk.Entry(
            self.control_frame,
            textvariable=self.plan_var,
            width=30
        )

        self.entry_plan.grid(
            row=0,
            column=0,
            padx=5,
            pady=5,
            sticky="w"
        )

        # =========================
        # CHANGE BUTTON
        # =========================
        self.btn_change = ttk.Button(
            self.control_frame,
            text="Change",
            style="Black.TButton",
            width=12,
            command=self.change_plan_from_entry
        )

        self.btn_change.grid(
            row=0,
            column=1,
            padx=5,
            pady=5
        )

        # =========================
        # REFRESH BUTTON
        # =========================
        self.btn_refresh = ttk.Button(
            self.control_frame,
            text="Refresh Status",
            style="Black.TButton",
            width=12,
            command=self.refresh_status
        )

        self.btn_refresh.grid(
            row=0,
            column=2,
            padx=5,
            pady=5
        )

        # =========================
        # BOT BUTTON FRAME
        # =========================
        self.bot_button_frame = ttk.Frame(
            self.control_frame
        )

        self.bot_button_frame.grid(
            row=0,
            column=3,
            padx=5
        )

        # START BOT
        self.btn_start_bot = ttk.Button(
            self.bot_button_frame,
            text="START BOT",
            style="Green.TButton",
            width=12,
            command=self.start_feishu_bot
        )

        self.btn_start_bot.grid(
            row=0,
            column=0
        )

        # STOP BOT
        self.btn_stop_bot = ttk.Button(
            self.bot_button_frame,
            text="STOP BOT",
            style="Red.TButton",
            width=12,
            command=self.stop_feishu_bot
        )

        # STATUS LABEL
        self.bot_status_label = ttk.Label(
            self.control_frame,
            text="Bot : OFFLINE",
            foreground="red",
            font=("Arial", 9, "bold")
        )

        self.bot_status_label.grid(
            row=0,
            column=4,
            padx=10
        )
        # =========================
        # TABLE FRAME
        # =========================
        self.table_frame = ttk.LabelFrame(
            self.tab_dws,
            text="Client Status",
            padding=10
        )

        self.table_frame.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=5,
            pady=5
        )
        self.table_frame.rowconfigure(0, weight=1)
        self.table_frame.columnconfigure(0, weight=1)

        columns = (
            "pc",
            "ip",
            "port",
            "status",
            "plan",
            "message"
        )
        
        self.tree = ttk.Treeview(
            self.table_frame,
            columns=columns,
            show="headings",
            height=18
        )

        self.tree.tag_configure(
            "online",
            foreground="green"
        )

        self.tree.tag_configure(
            "offline",
            foreground="red"
        )

        headings = {
            "pc": "PC",
            "ip": "IP Address",
            "port": "Port",
            "status": "Status",
            "plan": "Current Plan",
            "message": "Message"
        }

        for col in columns:

            self.tree.heading(
                col,
                text=headings[col]
            )

        self.tree.column(
            "pc",
            width=120,
            anchor="center"
        )

        self.tree.column(
            "ip",
            width=180,
            anchor="center"
        )

        self.tree.column(
            "port",
            width=80,
            anchor="center"
        )

        self.tree.column(
            "status",
            width=100,
            anchor="center"
        )

        self.tree.column(
            "plan",
            width=180,
            anchor="center"
        )

        self.tree.column(
            "message",
            width=250,
            anchor="w"
        )

        self.tree.grid(
            row=0,
            column=0,
            sticky="nsew"
        )

        scrollbar = ttk.Scrollbar(
            self.table_frame,
            orient="vertical",
            command=self.tree.yview
        )

        self.tree.configure(
            yscrollcommand=scrollbar.set
        )

        scrollbar.grid(
            row=0,
            column=1,
            sticky="ns"
        )

        # =========================
        # LOG FRAME
        # =========================
        self.log_frame = ttk.LabelFrame(
            self.tab_dws,
            text="Logs",
            padding=10
        )

        self.log_frame.grid(
            row=2,
            column=0,
            sticky="nsew",
            padx=5,
            pady=5
        )
        self.log_frame.rowconfigure(0, weight=1)
        self.log_frame.columnconfigure(0, weight=1)

        # TEXT
        self.log_text = Text(
            self.log_frame,
            height=15,
            state="disabled",
            font=("Consolas", 10),
            bg="#ffffff",
            fg="#111111",
            relief="flat",
            borderwidth=0,
            padx=8,
            pady=8
        )

        self.log_text.grid(
            row=0,
            column=0,
            sticky="nsew"
        )

        # SCROLLBAR
        log_scrollbar = ttk.Scrollbar(
            self.log_frame,
            orient="vertical",
            command=self.log_text.yview
        )

        log_scrollbar.grid(
            row=0,
            column=1,
            sticky="ns"
        )

        self.log_text.configure(
            yscrollcommand=log_scrollbar.set
        )

    def build_jms_tab(self):

        self.tab_jms.grid_rowconfigure(1, weight=1)
        self.tab_jms.grid_columnconfigure(0, weight=1)

        # =========================
        # JMS CONTROL FRAME
        # =========================
        control_frame = ttk.LabelFrame(
            self.tab_jms,
            text="JMS Control",
            padding=10
        )

        control_frame.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=10,
            pady=10
        )

        # =========================
        # JMS BUTTON FRAME
        # =========================
        self.jms_button_frame = ttk.Frame(
            control_frame
        )

        self.jms_button_frame.grid(
            row=0,
            column=0,
            padx=5,
            pady=5
        )

        # START BUTTON
        self.btn_start_jms = ttk.Button(
            self.jms_button_frame,
            text="START BOT",
            style="Green.TButton",
            width=12,
            command=self.start_jms_placeholder
        )
        self.btn_start_jms.grid(
            row=0,
            column=0
        )

        # STOP BUTTON
        self.btn_stop_jms = ttk.Button(
            self.jms_button_frame,
            text="STOP BOT",
            style="Red.TButton",
            width=12,
            command=self.stop_jms_placeholder
        )

        # STATUS LABEL
        self.jms_status_label = tk.Label(
            control_frame,
            text="Bot : OFFLINE",
            fg="red",
            font=("Arial", 9, "bold")
        )

        self.jms_status_label.grid(
            row=0,
            column=1,
            padx=15,
            pady=5,
            sticky="w"
        )

        # =========================
        # JMS LOG FRAME
        # =========================
        log_frame = ttk.LabelFrame(
            self.tab_jms,
            text="JMS Logs",
            padding=10
        )

        log_frame.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=10,
            pady=(0, 10)
        )

        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        # TEXT
        self.jms_log_text = Text(
            log_frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            bg="#ffffff",
            fg="#111111",
            relief="flat",
            borderwidth=0,
            padx=8,
            pady=8
        )

        self.jms_log_text.grid(
            row=0,
            column=0,
            sticky="nsew"
        )

        # SCROLLBAR
        jms_scrollbar = ttk.Scrollbar(
            log_frame,
            orient="vertical",
            command=self.jms_log_text.yview
        )

        jms_scrollbar.grid(
            row=0,
            column=1,
            sticky="ns"
        )

        self.jms_log_text.configure(
            yscrollcommand=jms_scrollbar.set
        )

        self.jms_log(
            "[SYSTEM] JMS TAB READY"
        )
        self.update_jms_ui()

        
    def start_jms_placeholder(self):

        self.jms_running = True

        self.jms_status_label.config(
            text="Bot : ONLINE",
            fg="green",
            font=("Arial", 9, "bold")
        )

        self.update_jms_ui()
        self.update_setting_tab_state()

        self.jms_log(
            "[SYSTEM] JMS BOT ONLINE"
        )


    def stop_jms_placeholder(self):

        self.jms_running = False

        self.jms_status_label.config(
            text="Bot :  OFFLINE",
            fg="red",
            font=("Arial", 9, "bold")
        )

        self.update_jms_ui()
        self.update_setting_tab_state()

        self.jms_log(
            "[SYSTEM] JMS BOT OFFLINE"
        )


    def jms_log(self, message):

        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        self.jms_log_text.insert(
            tk.END,
            f"[{timestamp}] {message}\n"
        )

        self.jms_log_text.see(tk.END)

    def update_jms_ui(self):

        if "ONLINE" in self.jms_status_label.cget("text"):

            self.btn_start_jms.grid_remove()

            self.btn_stop_jms.grid(
                row=0,
                column=0
            )

        else:

            self.btn_stop_jms.grid_remove()

            self.btn_start_jms.grid(
                row=0,
                column=0
            )
    # =====================================
    # SETTING TAB
    # =====================================
    def build_setting_tab(self):

        self.setting_frame = ttk.LabelFrame(
            self.tab_setting,
            text="Client Configuration",
            padding=10
        )

        self.setting_frame.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=10,
            pady=10
        )

        # =====================================
        # CLIENT CONFIG 2 COLUMN
        # =====================================

        self.setting_rows = []

        left_clients = self.clients[:5]
        right_clients = self.clients[5:]

        # LEFT HEADER
        ttk.Label(
            self.setting_frame,
            text="PC Name"
        ).grid(row=0, column=0, padx=5, pady=5)

        ttk.Label(
            self.setting_frame,
            text="IP Address"
        ).grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(
            self.setting_frame,
            text="Port"
        ).grid(row=0, column=2, padx=5, pady=5)

        # RIGHT HEADER
        ttk.Label(
            self.setting_frame,
            text="PC Name"
        ).grid(row=0, column=4, padx=(30, 5), pady=5)

        ttk.Label(
            self.setting_frame,
            text="IP Address"
        ).grid(row=0, column=5, padx=5, pady=5)

        ttk.Label(
            self.setting_frame,
            text="Port"
        ).grid(row=0, column=6, padx=5, pady=5)

        # =====================================
        # LEFT SIDE
        # =====================================

        for row_index, client in enumerate(left_clients, start=1):

            name = client["name"]

            ip_var = tk.StringVar(value=client["ip"])
            port_var = tk.StringVar(value=str(client["port"]))

            ttk.Label(
                self.setting_frame,
                text=name
            ).grid(
                row=row_index,
                column=0,
                padx=5,
                pady=3
            )

            ttk.Entry(
                self.setting_frame,
                textvariable=ip_var,
                width=18
            ).grid(
                row=row_index,
                column=1,
                padx=5,
                pady=3,
                sticky="ew"
            )

            ttk.Entry(
                self.setting_frame,
                textvariable=port_var,
                width=8
            ).grid(
                row=row_index,
                column=2,
                padx=5,
                pady=3
            )

            self.setting_rows.append({
                "name": name,
                "ip_var": ip_var,
                "port_var": port_var
            })

        # =====================================
        # RIGHT SIDE
        # =====================================

        for row_index, client in enumerate(right_clients, start=1):

            name = client["name"]

            ip_var = tk.StringVar(value=client["ip"])
            port_var = tk.StringVar(value=str(client["port"]))

            ttk.Label(
                self.setting_frame,
                text=name
            ).grid(
                row=row_index,
                column=4,
                padx=(30, 5),
                pady=3,
            )

            ttk.Entry(
                self.setting_frame,
                textvariable=ip_var,
                width=18
            ).grid(
                row=row_index,
                column=5,
                padx=5,
                pady=3,
                sticky="ew"
            )

            ttk.Entry(
                self.setting_frame,
                textvariable=port_var,
                width=8
            ).grid(
                row=row_index,
                column=6,
                padx=5,
                pady=3
            )

            self.setting_rows.append({
                "name": name,
                "ip_var": ip_var,
                "port_var": port_var
            })
        # EXPAND IP ENTRY
        self.setting_frame.grid_columnconfigure(1, weight=1)
        self.setting_frame.grid_columnconfigure(5, weight=1)

        # =====================================
        # FEISHU FRAME
        # =====================================
        self.feishu_frame = ttk.LabelFrame(
            self.tab_setting,
            text="Feishu Bot Config",
            padding=10
        )

        self.feishu_frame.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=10,
            pady=10
        )

        labels = [
            "APP_ID",
            "APP_SECRET",
            "VERIFY_TOKEN",
            "BOT_PORT",
            "NGROK_URL",
            "BOT_NAME"
        ]

        self.feishu_entries = {}

        for index, key in enumerate(labels):

            ttk.Label(
                self.feishu_frame,
                text=key
            ).grid(
                row=index,
                column=0,
                sticky="w",
                padx=5,
                pady=3
            )

            entry = ttk.Entry(
                self.feishu_frame,
                width=50
            )

            entry.grid(
                row=index,
                column=1,
                sticky="ew",
                padx=5,
                pady=3
            )

            entry.insert(
                0,
                self.feishu_config.get(key, "")
            )

            self.feishu_entries[key] = entry

        self.feishu_frame.grid_columnconfigure(1, weight=1)
        #self.feishu_frame.grid_columnconfigure(0, weight=0)

        # =====================================
        # JMS FRAME
        # =====================================
        self.jms_setting_frame = ttk.LabelFrame(
            self.tab_setting,
            text="JMS",
            padding=10
        )

        self.jms_setting_frame.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=10,
            pady=(0, 10)
        )

        # AUTH TOKEN LABEL
        ttk.Label(
            self.jms_setting_frame,
            text="AUTH_TOKEN"
        ).grid(
            row=0,
            column=0,
            sticky="w",
            padx=5,
            pady=3
        )

        # AUTH TOKEN ENTRY
        auth_entry = ttk.Entry(
            self.jms_setting_frame,
            width=50
        )

        auth_entry.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=5,
            pady=3
        )

        auth_entry.insert(
            0,
            self.feishu_config.get("AUTH_TOKEN", "")
        )

        self.feishu_entries["AUTH_TOKEN"] = auth_entry

        self.jms_setting_frame.grid_columnconfigure(1, weight=1)
        # =====================================
        # SAVE BUTTON
        # =====================================
        self.btn_save_all = ttk.Button(
            self.tab_setting,
            text="Save Config",
            style="Black.TButton",
            width=12,
            command=self.save_all_config
        )

        self.btn_save_all.grid(
            row=3,
            column=0,
            pady=(5, 10)
        ) 
    # =====================================
    # TABLE
    # =====================================
    def clear_table(self):

        for item in self.tree.get_children():

            self.tree.delete(item)

    def add_table_row(
        self,
        pc,
        ip,
        port,
        status,
        plan,
        message
    ):

        tag = "online"

        if status == "OFFLINE":
            tag = "offline"

        self.tree.insert(
            "",
            tk.END,
            values=(
                pc,
                ip,
                port,
                status,
                plan,
                message
            ),
            tags=(tag,)
        )

    # =====================================
    # CHECK CLIENT
    # =====================================
    def check_single_client(self, client):

        name = client["name"]
        ip = client["ip"]
        port = client["port"]

        url = f"http://{ip}:{port}/status"

        try:

            response = requests.get(
                url,
                timeout=3
            )

            data = response.json()

            status = "ONLINE"

            plan = data.get(
                "current_plan",
                "Unknown"
            )

            message = data.get(
                "message",
                "OK"
            )

        except Exception as e:

            status = "OFFLINE"
            plan = "-"
            message = str(e)

        self.root.after(
            0,
            lambda: self.add_table_row(
                name,
                ip,
                port,
                status,
                plan,
                message
            )
        )

    # =====================================
    # REFRESH
    # =====================================
    def refresh_status_thread(self):

        self.root.after(
            0,
            self.clear_table
        )

        """self.add_log(
            "[SYSTEM] Refreshing Status"
        )"""

        with ThreadPoolExecutor(max_workers=20) as executor:

            executor.map(
                self.check_single_client,
                self.clients
            )

        """self.add_log(
            "[SYSTEM] Refresh Complete"
        )"""

    def refresh_status(self):

        threading.Thread(
            target=self.refresh_status_thread,
            daemon=True
        ).start()

    # =====================================
    # AUTO REFRESH
    # =====================================
    def auto_refresh_loop(self):

        while True:

            time.sleep(5)

            self.refresh_status()

    def start_auto_refresh(self):

        threading.Thread(
            target=self.auto_refresh_loop,
            daemon=True
        ).start()
    # =====================================
    # SWITCH PLAN
    # =====================================
    def switch_single_client(
        self,
        client,
        target_plan
    ):

        name = client["name"]
        ip = client["ip"]
        port = client["port"]

        url = f"http://{ip}:{port}/switch_plan"

        try:

            response = requests.post(
                url,
                json={
                    "plan": target_plan
                },
                timeout=5
            )

            data = response.json()

            if data.get("success"):

                self.add_log(
                    f"[SUCCESS] {name} -> {target_plan}"
                )

            else:

                self.add_log(
                    f"[FAILED] {name}"
                )

        except Exception as e:

            self.add_log(
                f"[ERROR] {name} -> {e}"
            )

    def switch_plan_thread(self, target_plan):

        self.add_log(
            f"[SYSTEM] Switching -> {target_plan}"
        )

        with ThreadPoolExecutor(max_workers=20) as executor:

            for client in self.clients:

                executor.submit(
                    self.switch_single_client,
                    client,
                    target_plan
                )

        self.refresh_status()

    def switch_plan(self, target_plan):

        threading.Thread(
            target=self.switch_plan_thread,
            args=(target_plan,),
            daemon=True
        ).start()

    # =====================================
    # CHANGE PLAN FROM ENTRY
    # =====================================
    def change_plan_from_entry(self):

        target_plan = (
            self.plan_var
            .get()
            .strip()
        )

        if not target_plan:

            self.add_log(
                "[ERROR] PLAN EMPTY"
            )

            return

        self.switch_plan(
            target_plan
        )
    def handle_jms_command(
        self,
        text,
        chat_id,
        message_id,
        parent_id=None,
        root_id=None
    ):

        if not self.jms_running:
            return False

        lower_text = text.lower()
        normalized_lower_text = re.sub(
            r"\s+",
            " ",
            lower_text
        ).strip()

        # =====================================
        # KEYWORDS
        # =====================================

        app_keywords = [
            "รีapp",
            "รี app",
            "รีแอพ",
            "รี แอพ",
            "รีรหัสapp",
            "รีรหัส app",
            "รีรหัสแอพ",
            "รีรหัส แอพ",
            "รีแอป",
            "รี แอป",
            "รีรหัสแอป",
            "รีรหัส แอป",
            "รีเซ็ตapp",
            "reset app",
            "reset password app",
            "รีแอพให้หน่อย",
            "รีรหัสแอพให้หน่อย",
            "รีรหัสpda",
            "รีรหัสpdaให้หน่อย",
            "รีรหัส pda ให้หน่อย",
        ]

        jms_keywords = [
            "รีjms",
            "รีรหัสjms",
            "รีรหัส jms",
            "รี jms",
            "รีเซ็ตjms",
            "reset jms",
            "reset password jms",
            "รี jms ให้หน่อย",
            "รีรหัส jms ให้หน่อย",
        ]

        enable_keywords = [

            "เปิดยูส",
            "เปิด user",
            "enable",
            "เปิดใช้งาน",

            "ปลดล็อค",
            "ปลดล้อค",
            "unlock",

            "ระงับ",
            "โดนระงับ",

            "เข้าไม่ได้",
            "ใช้งานไม่ได้",

            "ปลด user",
            "เปิดรหัส",
            "เปิดไอดี",
        ]

        # =====================================
        # USER LIST
        # =====================================

        staff_list = re.findall(
            r"\b(?:\d{8}|[0-9A-Z]{10,20})\b",
            text.upper()
        )

        staff_list = list(
            dict.fromkeys(staff_list)
        )

        # =====================================
        # COMMAND TYPE
        # =====================================

        command_type = None

        if any(
            (keyword in lower_text)
            or (keyword in normalized_lower_text)
            for keyword in app_keywords
        ):

            command_type = "APP"

        elif any(
            (keyword in lower_text)
            or (keyword in normalized_lower_text)
            for keyword in jms_keywords
        ):

            command_type = "JMS"

        elif any(
            (keyword in lower_text)
            or (keyword in normalized_lower_text)
            for keyword in enable_keywords
        ):

            command_type = "ENABLE"

        elif (
            staff_list
            and any(
                    (keyword in lower_text)
                    or (keyword in normalized_lower_text)
                    for keyword in [
                    "ล็อค",
                    "ล๊อค",
                    "โดนล็อค",
                    "โดนล๊อค",
                    "locked",
                    "ระงับ",
                    "โดนระงับ",
                    "เข้าไม่ได้",
                    "ใช้งานไม่ได้",
                    "ปลดล็อค",
                    "ปลดล้อค",
                    "unlock",
                    "รหัสปิด",
                ]
            )
        ):

            command_type = "ENABLE"

        else:

            #send_help()

            return False
        # =====================================
        # USER CHECK
        # =====================================

        if not staff_list:

            reply_feishu_message(
                message_id,
                "❌ ไม่พบ USER"

            )

            return True

        # =====================================
        # PROCESS
        # =====================================

        success_text = []

        fail_text = []

        for staff_no in staff_list:

            try:

                self.jms_log(
                    f"[SEARCH] {staff_no}"
                )

                user = search_user(staff_no)

                # CHECK
                if not user or not isinstance(user, dict):

                    fail_text.append(
                        f"{staff_no} : ไม่พบ USER"
                    )

                    continue

                user_id = user.get("id")
                user_name = user.get("name")

                if not user_id:

                    fail_text.append(
                        f"{staff_no} : USER DATA INVALID"
                    )

                    continue

                # =================================
                # APP
                # =================================

                if command_type == "APP":

                    new_password = (
                        reset_app_password(
                            user_id
                        )
                    )

                    success_text.append(
                        (
                            f"Name : {user_name}\n"

                            f"User : {staff_no}\n"

                            f"APP Password : "
                            f"{new_password}"
                        )
                    )

                    self.jms_log(
                        f"[RESET APP] {staff_no}"
                    )

                    write_log(
                        status="SUCCESS",
                        user=staff_no,
                        name=user_name,
                        action="APP",
                        detail=f"PASSWORD : {new_password}"
                    )

                # =================================
                # JMS
                # =================================

                elif command_type == "JMS":

                    new_password = (
                        reset_jms_password(
                            user_id
                        )
                    )

                    success_text.append(
                        (
                            f"Name : {user_name}\n"

                            f"User : {staff_no}\n"

                            f"JMS Password : "
                            f"{new_password}"
                        )
                    )

                    self.jms_log(
                        f"[RESET JMS] {staff_no}"
                    )

                    write_log(
                        status="SUCCESS",
                        user=staff_no,
                        name=user_name,
                        action="JMS",
                        detail=f"PASSWORD : {new_password}"
                    )

                # =================================
                # ENABLE
                # =================================

                elif command_type == "ENABLE":

                    enable_user(user)

                    success_text.append(
                        (
                            f"Name : {user_name}\n"

                            f"User : {staff_no}\n"

                            f"Status : เปิดใช้งาน"
                        )
                    )

                    self.jms_log(
                        f"[ENABLE USER] {staff_no}"
                    )

                    write_log(
                        status="SUCCESS",
                        user=staff_no,
                        name=user_name,
                        action="ENABLE",
                        detail="USER ENABLED"
                    )

            except Exception as e:

                fail_text.append(
                    f"{staff_no} : {str(e)}"
                )

                self.jms_log(
                    f"[ERROR] {staff_no} -> {e}"
                )

                write_log(
                    status="FAILED",
                    user=staff_no,
                    action=command_type,
                    detail=str(e)
                )

        # =====================================
        # RESULT
        # =====================================

        final_message = ""

        if success_text:

            final_message += (
                "ดำเนินการเสร็จเรียบร้อย\n\n"
            )

            final_message += (
                "\n\n".join(success_text)
            )

        if fail_text:

            final_message += (
                "\n\n⚠ FAILED\n\n"
            )

            final_message += (
                "\n".join(fail_text)
            )


        if len(final_message) > 3000:

            chunks = [
                final_message[i:i+3000]
                for i in range(0, len(final_message), 3000)
            ]

            for chunk in chunks:

                reply_feishu_message(
                    message_id,
                    chunk
                )

        else:

            reply_feishu_message(
                message_id,
                final_message
            )

        return True

    def run_feishu_server(self):

        try:

            port = int(
                self.feishu_entries["BOT_PORT"].get()
            )

        except:

            port = 7000

        self.add_log(
            f"[SYSTEM] Feishu Server Running : {port}"
        )

        serve(
            bot_app,
            host="0.0.0.0",
            port=port,
            threads=20
        )


    def start_feishu_bot(self):

        if self.bot_running:
            return

        self.bot_running = True

        self.bot_thread = threading.Thread(
            target=self.run_feishu_server,
            daemon=True
        )

        self.bot_thread.start()

        self.update_bot_ui()
        self.update_setting_tab_state()

        self.add_log(
            "[SYSTEM] Feishu Bot Started"
        )

    def stop_feishu_bot(self):

        self.bot_running = False

        self.update_bot_ui()
        self.update_setting_tab_state()

        self.add_log(
            "[SYSTEM] Bot Marked As Offline"
        )

        self.add_log(
            "[INFO] Restart program to fully stop server"
        )
        
    # =====================================
    # RUN
    # =====================================
    def run(self):

        self.root.mainloop()

# =========================================
# MAIN
# =========================================
if __name__ == "__main__":

    app = ControllerGUI()

    app.run()
