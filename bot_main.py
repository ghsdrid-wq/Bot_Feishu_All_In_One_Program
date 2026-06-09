import os
import re
import json
import mimetypes
import time
import queue
import threading
import configparser
from datetime import datetime, timedelta
from typing import Dict, List

import customtkinter as ctk
from tkinter import filedialog, messagebox
from tkcalendar import DateEntry

import requests
import pandas as pd

from Createphoto import (
    run_create,
    migrate_old_export_config,
    save_config,
    resource_path,
    safe_key,
    unique_key,
)
from Botmessage import run_send

APP_TITLE = "Auto Report Feishu Enterprise Console v10.0"
CONFIG_FILE = resource_path("config.ini")

DEFAULT_FEISHU = {
    "APP_ID": "",
    "APP_SECRET": "",
    "CHAT_ID": "",
}

DEFAULT_PATH = {
    "output_dir": "",
}

DEFAULT_TIME = {
    "run_minute": "5",
    "start_hour": "15",
    "end_hour": "12",
}

DEFAULT_DWS_JMS = {
    "dws_url": "",
    "dws_token": "",
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
    "download_size": "28888",
    "enabled": "true",
    "send_dws_file": "false",
    "send_auto_file": "false",
    "send_dwspda_file": "false",
    "send_realtime_file": "false",
}


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
        super().__init__(master, fg_color="#111827", corner_radius=12)
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
            fg_color="#1e293b",
            text_color="#e2e8f0",
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
            text_color="#94a3b8",
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self.disabled_badge.grid(row=0, column=7, padx=4, pady=8)

        self.delete_btn = ctk.CTkButton(
            self,
            text="✕",
            width=34,
            fg_color="#8b1e2d",
            hover_color="#b3263a",
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
            row_bg = "#111827"
            entry_bg = "#1f2937"
            entry_text = "#f8fafc"
            muted_text = "#cbd5e1"
            no_bg = "#1e293b"
            no_text = "#e2e8f0"
            badge_text = ""
            badge_bg = "transparent"
        else:
            row_bg = "#070b12"
            entry_bg = "#111827"
            entry_text = "#64748b"
            muted_text = "#64748b"
            no_bg = "#334155"
            no_text = "#94a3b8"
            if runtime_locked:
                badge_text = "LOCKED"
                badge_bg = "#1e293b"
            else:
                badge_text = "DISABLED"
                badge_bg = "#3f1d1d" if parent_enabled else "#1e293b"

        self.configure(fg_color=row_bg)
        self.no_box.configure(fg_color=no_bg, text_color=no_text)
        self.disabled_badge.configure(text=badge_text, fg_color=badge_bg, text_color="#fca5a5" if parent_enabled else "#94a3b8")

        # If workbook is disabled or app is running, even the row Use checkbox should look locked.
        self.use_chk.configure(state="normal" if (parent_enabled and not runtime_locked) else "disabled", text_color=muted_text)

        for ent in self.entries.values():
            ent.configure(
                state="normal" if active else "disabled",
                fg_color=entry_bg,
                text_color=entry_text,
                placeholder_text_color="#475569" if not active else "#64748b",
            )

        for widget in [self.delete_chk, self.send_chk]:
            widget.configure(state="normal" if active else "disabled", text_color=muted_text)

        # Keep delete button available when merely disabled, but lock it while running.
        self.delete_btn.configure(
            state="disabled" if runtime_locked else "normal",
            fg_color="#7f1d1d" if active else "#3f1d1d",
            hover_color="#991b1b" if active else "#4c1d1d",
            text_color="#fee2e2" if active else "#94a3b8",
        )

    def delete(self):
        if messagebox.askyesno("Delete sheet", f"ลบรายการเลข {self.export_name} ใช่ไหม?"):
            self.app.delete_export(self.export_name)

class WorkbookCard(ctk.CTkFrame):
    def __init__(self, master, app, workbook_key: str):
        super().__init__(master, fg_color="#0b1220", corner_radius=18)
        self.app = app
        self.workbook_key = workbook_key
        self.sheet_rows: List[SheetRow] = []

        sec = self.app.config[f"WORKBOOK:{workbook_key}"]
        self.enabled_var = ctk.BooleanVar(value=as_bool(sec.get("enabled", "true"), True))
        display = sec.get("display_name", workbook_key)
        path = sec.get("path", "")

        self.grid_columnconfigure(0, weight=1)

        self.header = ctk.CTkFrame(self, fg_color="#111827", corner_radius=14)
        self.header.grid(row=0, column=0, padx=12, pady=(12, 6), sticky="ew")
        self.header.grid_columnconfigure(2, weight=1)

        self.use_chk = ctk.CTkCheckBox(self.header, text="Use", variable=self.enabled_var, command=self.save_workbook, width=58)
        self.use_chk.grid(row=0, column=0, padx=(12, 6), pady=10)

        self.icon_label = ctk.CTkLabel(self.header, text="📘", font=ctk.CTkFont(size=22))
        self.icon_label.grid(row=0, column=1, padx=4, pady=10)

        self.title_label = ctk.CTkLabel(self.header, text=display, font=ctk.CTkFont(size=16, weight="bold"), text_color="#e5f0ff")
        self.title_label.grid(row=0, column=2, padx=4, pady=(8, 0), sticky="w")

        self.path_editing = False
        self.path_entry = None
        self.path_label = ctk.CTkLabel(
            self.header,
            text=path,
            text_color="#94a3b8",
            anchor="w",
            cursor="hand2",
        )
        self.path_label.grid(row=1, column=2, padx=4, pady=(0, 8), sticky="ew")
        self.path_label.bind("<Double-Button-1>", lambda _e: self.start_edit_path())
        self.path_label.bind("<Enter>", lambda _e: self.path_label.configure(text_color="#bfdbfe"))
        self.path_label.bind("<Leave>", lambda _e: self.path_label.configure(text_color="#94a3b8" if not getattr(self.app, "runtime_locked", False) and self.enabled_var.get() else "#475569"))

        self.workbook_badge = ctk.CTkLabel(
            self.header,
            text="ENABLED",
            width=86,
            height=28,
            corner_radius=14,
            fg_color="#123524",
            text_color="#5dff9e",
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self.workbook_badge.grid(row=0, column=3, rowspan=2, padx=(8, 4), pady=10)

        self.add_sheet_btn = ctk.CTkButton(
            self.header,
            text="+ Add Sheet",
            width=116,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self.add_sheet,
        )
        self.add_sheet_btn.grid(row=0, column=4, rowspan=2, padx=8, pady=10)

        self.remove_btn = ctk.CTkButton(
            self.header,
            text="Remove",
            width=86,
            fg_color="#7f1d1d",
            hover_color="#991b1b",
            command=self.delete_workbook,
        )
        self.remove_btn.grid(row=0, column=5, rowspan=2, padx=(0, 12), pady=10)

        labels = ctk.CTkFrame(self, fg_color="transparent")
        labels.grid(row=1, column=0, padx=16, pady=(4, 0), sticky="ew")
        for i, (txt, width) in enumerate([("", 56), ("No.", 64), ("Sheet", 120), ("Range", 110), ("Output File", 120), ("", 74), ("", 64), ("Status", 82)]):
            labels.grid_columnconfigure(i, weight=1 if i in [2, 3, 4] else 0)
            ctk.CTkLabel(labels, text=txt, text_color="#94a3b8", width=width, anchor="w").grid(row=0, column=i, padx=4, sticky="ew")

        self.rows_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.rows_frame.grid(row=2, column=0, padx=12, pady=(0, 12), sticky="ew")
        self.rows_frame.grid_columnconfigure(0, weight=1)
        self.reload_sheets()
        self.apply_visual_state()

    def save_workbook(self):
        self.app.config[f"WORKBOOK:{self.workbook_key}"]["enabled"] = str(self.enabled_var.get()).lower()
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
            fg_color="#1f2937",
            border_color="#38bdf8",
            text_color="#f8fafc",
            height=28,
        )
        self.path_entry.insert(0, current_path)
        self.path_entry.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        save_btn = ctk.CTkButton(
            edit_frame,
            text="Save",
            width=58,
            height=28,
            fg_color="#059669",
            hover_color="#047857",
            command=lambda: self.finish_edit_path(edit_frame, save=True),
        )
        save_btn.grid(row=0, column=1, padx=(0, 4))

        browse_btn = ctk.CTkButton(
            edit_frame,
            text="Browse",
            width=72,
            height=28,
            fg_color="#334155",
            hover_color="#475569",
            command=self.browse_edit_path,
        )
        browse_btn.grid(row=0, column=2, padx=(0, 4))

        cancel_btn = ctk.CTkButton(
            edit_frame,
            text="Cancel",
            width=66,
            height=28,
            fg_color="#475569",
            hover_color="#64748b",
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

        if active:
            self.configure(fg_color="#0b1220")
            self.header.configure(fg_color="#111827")
            self.title_label.configure(text_color="#e5f0ff")
            self.path_label.configure(text_color="#94a3b8")
            self.icon_label.configure(text="📘", text_color="#e5f0ff")
            self.workbook_badge.configure(text="ENABLED", fg_color="#123524", text_color="#5dff9e")
            self.add_sheet_btn.configure(state="normal", fg_color="#2563eb", hover_color="#1d4ed8", text_color="#ffffff")
            self.remove_btn.configure(state="normal", fg_color="#7f1d1d", hover_color="#991b1b", text_color="#fee2e2")
        else:
            self.configure(fg_color="#050914")
            self.header.configure(fg_color="#070b12")
            self.title_label.configure(text_color="#64748b")
            self.path_label.configure(text_color="#475569")
            self.icon_label.configure(text="📕", text_color="#64748b")
            if runtime_locked:
                self.workbook_badge.configure(text="LOCKED", fg_color="#1e293b", text_color="#cbd5e1")
            else:
                self.workbook_badge.configure(text="DISABLED", fg_color="#3f1d1d", text_color="#fca5a5")
            self.add_sheet_btn.configure(state="disabled", fg_color="#1e293b", hover_color="#1e293b", text_color="#64748b")
            self.remove_btn.configure(state="disabled" if runtime_locked else "normal", fg_color="#3f1d1d", hover_color="#4c1d1d", text_color="#94a3b8")

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

        self.config = configparser.ConfigParser()
        self.config.read(CONFIG_FILE, encoding="utf-8")
        self.ensure_config()

        self.task_queue = queue.Queue()
        self.worker_running = True
        self.running = False
        self.current_run_source = None
        self.scheduler_running = False
        self.last_run_minute = None
        self.mode = None
        self.last_activity = time.time()
        self._save_job = None
        self.workbook_cards: List[WorkbookCard] = []
        self.runtime_locked = False
        self.runtime_lock_banner = None
        self.lockable_inputs = []
        self.lockable_buttons = []
        self.button_color_map = {}
        self.stop_requested = False
        self.next_run = None

        self.build_ui()
        self.load_values_to_ui()

        threading.Thread(target=self.worker_loop, daemon=True).start()
        threading.Thread(target=self.watchdog, daemon=True).start()
        self.after(1000, self.update_clock)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def ensure_config(self):
        for section, defaults in [("PATH", DEFAULT_PATH), ("FEISHU", DEFAULT_FEISHU), ("TIME", DEFAULT_TIME), ("DWS_JMS", DEFAULT_DWS_JMS)]:
            if section not in self.config:
                self.config[section] = {}
            for k, v in defaults.items():
                if not self.config[section].get(k):
                    self.config[section][k] = v
        # Clean old retired Feishu keys from previous versions.
        for old_key in (("WEB" + chr(72) + "OOK"), "SECRET"):
            if "FEISHU" in self.config and old_key in self.config["FEISHU"]:
                self.config.remove_option("FEISHU", old_key)
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
            for old_key in (("WEB" + chr(72) + "OOK"), "SECRET"):
                self.config.remove_option("FEISHU", old_key)
            if "DWS_JMS" not in self.config:
                self.config["DWS_JMS"] = {}
            self.config["DWS_JMS"]["dws_url"] = entry_value(self.dws_url)
            if hasattr(self, "raw_path_entry"):
                self.config["DWS_JMS"]["raw_path"] = entry_value(self.raw_path_entry)
            if hasattr(self, "bot_export_var"):
                self.config["DWS_JMS"]["enabled"] = str(self.bot_export_var.get()).lower()
            elif hasattr(self, "dws_enable_var"):
                self.config["DWS_JMS"]["enabled"] = str(self.dws_enable_var.get()).lower()
            if hasattr(self, "bot_chat_var"):
                self.config["DWS_JMS"]["bot_chat_enabled"] = str(self.bot_chat_var.get()).lower()
            self.config["DWS_JMS"]["dws_token"] = entry_value(self.dws_token, collapse_internal_spaces=True)
            self.config["DWS_JMS"]["jms_token"] = entry_value(self.jms_token, collapse_internal_spaces=True)
            if hasattr(self, "download_size_entry"):
                self.config["DWS_JMS"]["download_size"] = str(self.get_download_size(write_warning=False))
            self.config["DWS_JMS"]["name_dws"] = entry_value(self.name_dws) or "DWS9-11.xlsx"
            self.config["DWS_JMS"]["name_auto"] = entry_value(self.name_auto) or "DWSXAUTOPDA.xlsx"
            self.config["DWS_JMS"]["name_dwspda"] = entry_value(self.name_dwspda) or "DWSPDA.xlsx"
            self.config["DWS_JMS"]["name_realtime_db"] = entry_value(self.name_realtime_db) or "RealtimeDB.xlsx"
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

    def build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self, width=230, corner_radius=0, fg_color="#070d1d")
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(9, weight=1)

        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, padx=20, pady=(26, 22), sticky="ew")
        ctk.CTkLabel(brand, text="Auto Report", font=ctk.CTkFont(size=26, weight="bold"), text_color="#f8fafc").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(brand, text="Enterprise Console", text_color="#8ea4c8", font=ctk.CTkFont(size=13)).grid(row=1, column=0, pady=(4, 0), sticky="w")

        self.nav_home = ctk.CTkButton(sidebar, text="⌂  หน้าหลัก", height=44, anchor="w", command=lambda: self.show_page("home"))
        self.nav_home.grid(row=2, column=0, padx=18, pady=6, sticky="ew")
        self.nav_workbooks = ctk.CTkButton(sidebar, text="▣  ไฟล์ Excel", height=44, anchor="w", fg_color="#1f2937", command=lambda: self.show_page("workbooks"))
        self.nav_workbooks.grid(row=3, column=0, padx=18, pady=6, sticky="ew")
        self.nav_dws_jms = ctk.CTkButton(sidebar, text="◉  ส่งออกข้อมูล", height=44, anchor="w", fg_color="#1f2937", command=lambda: self.show_page("dws_jms"))
        self.nav_dws_jms.grid(row=4, column=0, padx=18, pady=6, sticky="ew")
        self.nav_output = ctk.CTkButton(sidebar, text="▤  จัดการไฟล์", height=44, anchor="w", fg_color="#1f2937", command=lambda: self.show_page("output_manager"))
        self.nav_output.grid(row=5, column=0, padx=18, pady=6, sticky="ew")
        self.nav_settings = ctk.CTkButton(sidebar, text="⚙  ตั้งค่า", height=44, anchor="w", fg_color="#1f2937", command=lambda: self.show_page("settings"))
        self.nav_settings.grid(row=6, column=0, padx=18, pady=6, sticky="ew")

        self.side_hint = ctk.CTkLabel(sidebar, text="ลำดับงาน: DWS → Excel → Feishu", text_color="#64748b", wraplength=180, justify="left")
        self.side_hint.grid(row=8, column=0, padx=20, pady=16, sticky="sw")
        self.status_pill = ctk.CTkLabel(sidebar, text="Idle", fg_color="#123524", text_color="#5dff9e", corner_radius=18, height=36, font=ctk.CTkFont(size=13, weight="bold"))
        self.status_pill.grid(row=10, column=0, padx=18, pady=(8, 20), sticky="ew")

        self.content = ctk.CTkFrame(self, fg_color="#0f172a", corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.pipeline_widgets = {}
        self.pages = {
            "home": self.build_home_page(self.content),
            "workbooks": self.build_workbooks_page(self.content),
            "dws_jms": self.build_dws_jms_page(self.content),
            "output_manager": self.build_output_manager_page(self.content),
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
            text_color="#f8fafc",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            frame,
            text=subtitle,
            text_color="#9fb3d8",
            font=ctk.CTkFont(size=13),
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        return frame

    def make_card(self, parent, fg="#08111f", radius=22):
        return ctk.CTkFrame(parent, fg_color=fg, corner_radius=radius)

    def make_metric(self, parent, col, title, value="--", accent="#38bdf8"):
        # Compact status card: keep the command-center feel without stealing space from Live Log.
        card = ctk.CTkFrame(parent, fg_color="#0b1220", corner_radius=16, border_width=1, border_color="#17233a", height=78)
        card.grid(row=0, column=col, padx=6, pady=2, sticky="ew")
        card.grid_propagate(False)
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text=title, text_color="#8ea4c8", font=ctk.CTkFont(size=10)).grid(row=0, column=0, padx=12, pady=(8, 0), sticky="w")
        lbl = ctk.CTkLabel(card, text=value, text_color="#f8fafc", font=ctk.CTkFont(size=14, weight="bold"))
        lbl.grid(row=1, column=0, padx=12, pady=(6, 6), sticky="w")
        bar = ctk.CTkFrame(card, fg_color=accent, width=4, corner_radius=3)
        bar.grid(row=0, column=1, rowspan=2, padx=(0, 8), pady=10, sticky="ns")
        return lbl

    def make_stage(self, parent, col, key, title, icon):
        stage = ctk.CTkFrame(parent, fg_color="#0b1220", corner_radius=18, border_width=1, border_color="#17233a", height=108)
        stage.grid(row=0, column=col, padx=8, pady=8, sticky="ew")
        stage.grid_propagate(False)
        stage.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(stage, text=icon, font=ctk.CTkFont(size=22)).grid(row=0, column=0, padx=12, pady=(10, 0))
        ctk.CTkLabel(stage, text=title, text_color="#dbeafe", font=ctk.CTkFont(size=13, weight="bold")).grid(row=1, column=0, padx=12, pady=(0, 2))
        status = ctk.CTkLabel(stage, text="READY", text_color="#94a3b8", font=ctk.CTkFont(size=11, weight="bold"))
        status.grid(row=2, column=0, padx=12, pady=(0, 8))
        self.pipeline_widgets[key] = {"frame": stage, "status": status}

    def set_pipeline_state(self, key=None, state="ready"):
        palette = {
            "ready": ("#0b1220", "#17233a", "READY", "#94a3b8"),
            "run": ("#172554", "#38bdf8", "RUNNING", "#bae6fd"),
            "ok": ("#123524", "#22c55e", "DONE", "#86efac"),
            "error": ("#4c1118", "#ef4444", "ERROR", "#fecaca"),
            "skip": ("#1e293b", "#475569", "SKIP", "#cbd5e1"),
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
        """Time range for Auto / Run Now. Raw export follows Bot Chat scheduler here."""
        try:
            start_hour = int(self.start_hour_var.get().replace(":00", "") or 15)
        except Exception:
            start_hour = 15
        try:
            end_hour = int(self.end_hour_var.get().replace(":00", "") or 12)
        except Exception:
            end_hour = 12

        now = datetime.now()
        business_date = (now - timedelta(days=1)).date() if now.hour < start_hour else now.date()
        start_dt = datetime.combine(business_date, datetime.min.time()).replace(hour=start_hour)
        end_date = business_date + timedelta(days=1) if start_hour >= end_hour else business_date
        end_dt = datetime.combine(end_date, datetime.min.time()).replace(hour=end_hour)
        return start_dt, end_dt

    def update_business_panel(self):
        start_dt, end_dt = self.get_scheduler_time_range()
        if hasattr(self, "business_date_label"):
            self.business_date_label.configure(text=start_dt.strftime("%Y-%m-%d"))
        if hasattr(self, "date_range_label"):
            self.date_range_label.configure(text=f"{start_dt:%m-%d %H:%M} → {end_dt:%m-%d %H:%M}")

    def build_home_page(self, master):
        page = ctk.CTkFrame(master, fg_color="#0f172a")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(5, weight=1)
        self.header(page, "หน้าหลัก", "รับงาน / ตั้งเวลา / ดูสถานะการทำงานทั้งหมด").grid(row=0, column=0, padx=24, pady=(16, 6), sticky="ew")

        metrics = ctk.CTkFrame(page, fg_color="transparent")
        metrics.grid(row=1, column=0, padx=16, pady=(0, 2), sticky="ew")
        for i in range(4):
            metrics.grid_columnconfigure(i, weight=1)
        self.clock_label = self.make_metric(metrics, 0, "CURRENT TIME", "--", "#38bdf8")
        self.business_date_label = self.make_metric(metrics, 1, "BUSINESS DATE", "--", "#a78bfa")
        self.mode_label = self.make_metric(metrics, 2, "MODE", "MANUAL", "#22c55e")
        self.date_range_label = self.make_metric(metrics, 3, "DWS/JMS RANGE", "--", "#f97316")

        command = self.make_card(page, "#111827", 22)
        command.grid(row=2, column=0, padx=24, pady=6, sticky="ew")
        for i in range(10):
            command.grid_columnconfigure(i, weight=1)
        hours = [f"{i:02}:00" for i in range(24)]
        minutes = [str(i) for i in range(60)]
        self.minute_var = ctk.StringVar(value="5")
        self.start_hour_var = ctk.StringVar(value="15:00")
        self.end_hour_var = ctk.StringVar(value="12:00")

        ctk.CTkLabel(command, text="Scheduler", text_color="#f8fafc", font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=0, columnspan=2, padx=16, pady=(16, 2), sticky="w")
        ctk.CTkLabel(command, text="Run minute", text_color="#aebbd1").grid(row=1, column=0, padx=(16, 4), pady=10, sticky="w")
        self.minute_menu = ctk.CTkOptionMenu(command, values=minutes, variable=self.minute_var, width=82, command=lambda _: self.save_config())
        self.minute_menu.grid(row=1, column=1, padx=4, pady=10, sticky="w")
        ctk.CTkLabel(command, text="Start", text_color="#aebbd1").grid(row=1, column=2, padx=(16, 4), pady=10, sticky="e")
        self.start_menu = ctk.CTkOptionMenu(command, values=hours, variable=self.start_hour_var, width=92, command=lambda _: (self.save_config(), self.update_business_panel()))
        self.start_menu.grid(row=1, column=3, padx=4, pady=10, sticky="w")
        ctk.CTkLabel(command, text="End", text_color="#aebbd1").grid(row=1, column=4, padx=(16, 4), pady=10, sticky="e")
        self.end_menu = ctk.CTkOptionMenu(command, values=hours, variable=self.end_hour_var, width=92, command=lambda _: self.save_config())
        self.end_menu.grid(row=1, column=5, padx=4, pady=10, sticky="w")
        self.btn_start = ctk.CTkButton(command, text="▣ Start Auto", height=40, fg_color="#2563eb", hover_color="#1d4ed8", command=self.start_scheduler)
        self.btn_start.grid(row=1, column=6, padx=8, pady=10, sticky="ew")
        self.btn_run = ctk.CTkButton(command, text="⚡ Run Now", height=40, fg_color="#059669", hover_color="#047857", command=self.run_once)
        self.btn_run.grid(row=1, column=7, padx=8, pady=10, sticky="ew")
        self.btn_stop_auto = ctk.CTkButton(command, text="⛔ Stop Auto", height=40, fg_color="#dc2626", hover_color="#b91c1c", command=self.stop_scheduler)
        self.btn_stop_run = ctk.CTkButton(command, text="⛔ Stop Run", height=40, fg_color="#dc2626", hover_color="#b91c1c", command=self.stop_process)

        ctk.CTkLabel(command, text="เลือกงานที่จะรัน", text_color="#f8fafc", font=ctk.CTkFont(size=17, weight="bold")).grid(row=2, column=0, columnspan=2, padx=16, pady=(8, 2), sticky="w")
        self.bot_export_var = ctk.BooleanVar(value=True)
        self.bot_chat_var = ctk.BooleanVar(value=True)
        # Backward-compatible alias: old code used dws_enable_var to decide raw export.
        self.dws_enable_var = self.bot_export_var
        self.chk_bot_export = ctk.CTkCheckBox(command, text="Bot Export / Raw DWS-JMS", variable=self.bot_export_var, command=self.save_config)
        self.chk_bot_export.grid(row=3, column=0, columnspan=3, padx=(16, 4), pady=(6, 16), sticky="w")
        self.chk_bot_chat = ctk.CTkCheckBox(command, text="Bot Chat / Excel Image + Feishu", variable=self.bot_chat_var, command=self.save_config)
        self.chk_bot_chat.grid(row=3, column=3, columnspan=4, padx=(16, 4), pady=(6, 16), sticky="w")
        ctk.CTkLabel(command, text="ลำดับเมื่อเลือกทั้งคู่: Export → Chat", text_color="#94a3b8").grid(row=3, column=7, columnspan=3, padx=8, pady=(6, 16), sticky="e")

        pipeline = self.make_card(page, "#020617", 22)
        pipeline.grid(row=3, column=0, padx=24, pady=6, sticky="ew")
        for i in range(6):
            pipeline.grid_columnconfigure(i, weight=1)
        ctk.CTkLabel(pipeline, text="ลำดับการทำงาน", text_color="#f8fafc", font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=0, columnspan=6, padx=16, pady=(14, 0), sticky="w")
        stages = [("dws", "DWS", "📥"), ("jms_auto", "JMS AUTO", "📦"), ("jms_pda", "JMS PDA", "📲"), ("realtime", "Realtime DB", "🧭"), ("excel", "Excel Image", "🖼"), ("feishu", "Feishu", "🚀")]
        stage_wrap = ctk.CTkFrame(pipeline, fg_color="transparent")
        stage_wrap.grid(row=1, column=0, columnspan=6, padx=8, pady=(4, 10), sticky="ew")
        for i in range(6):
            stage_wrap.grid_columnconfigure(i, weight=1)
        for col, (key, title, icon) in enumerate(stages):
            self.make_stage(stage_wrap, col, key, title, icon)

        self.progress = ctk.CTkProgressBar(page, height=14, progress_color="#38bdf8")
        self.progress.grid(row=4, column=0, padx=24, pady=(6, 6), sticky="ew")
        self.progress.set(0)

        log_card = self.make_card(page, "#020617", 22)
        log_card.grid(row=5, column=0, padx=24, pady=(6, 18), sticky="nsew")
        log_card.grid_rowconfigure(1, weight=1)
        log_card.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(log_card, fg_color="transparent")
        top.grid(row=0, column=0, padx=16, pady=(14, 6), sticky="ew")
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="Live Log", font=ctk.CTkFont(size=16, weight="bold"), text_color="#e2e8f0").grid(row=0, column=0, sticky="w")
        ctk.CTkButton(top, text="Clear", width=70, fg_color="#334155", hover_color="#475569", command=lambda: self.log_box.delete("1.0", "end")).grid(row=0, column=1, sticky="e")
        self.log_box = ctk.CTkTextbox(log_card, fg_color="#050b16", text_color="#a7f3d0", font=("Consolas", 12), corner_radius=12, height=150)
        self.log_box.grid(row=1, column=0, padx=16, pady=(0, 16), sticky="nsew")
        try:
            self.log_box.tag_config("INFO", foreground="#93c5fd")
            self.log_box.tag_config("START", foreground="#67e8f9")
            self.log_box.tag_config("SUCCESS", foreground="#86efac")
            self.log_box.tag_config("WARN", foreground="#fbbf24")
            self.log_box.tag_config("ERROR", foreground="#fca5a5")
        except Exception:
            pass
        return page

    def build_workbooks_page(self, master):
        page = ctk.CTkFrame(master, fg_color="#0f172a")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)
        self.header(page, "ไฟล์ Excel", "เลือกไฟล์ Excel ได้หลายไฟล์ แล้วเพิ่มรายการ Sheet/Export ได้ง่าย ๆ").grid(row=0, column=0, padx=24, pady=(22, 12), sticky="ew")

        tools = ctk.CTkFrame(page, fg_color="#111827", corner_radius=16)
        tools.grid(row=1, column=0, padx=24, pady=8, sticky="ew")
        tools.grid_columnconfigure(1, weight=1)
        self.btn_add_excel = ctk.CTkButton(tools, text="＋ เพิ่มไฟล์ Excel", height=38, width=150, fg_color="#2563eb", hover_color="#1d4ed8", command=self.add_workbook_files)
        self.btn_add_excel.grid(row=0, column=0, padx=14, pady=14, sticky="w")
        ctk.CTkLabel(tools, text="เลือกได้หลายไฟล์ในครั้งเดียว | กด + Add Sheet ใต้ไฟล์นั้นเพื่อเพิ่มรายการ Export", text_color="#94a3b8").grid(row=0, column=1, padx=12, pady=14, sticky="w")
        self.btn_save_workspace = ctk.CTkButton(tools, text="💾 บันทึก", width=92, fg_color="#334155", hover_color="#475569", command=self.save_all)
        self.btn_save_workspace.grid(row=0, column=2, padx=14, pady=14)

        self.workbook_scroll = ctk.CTkScrollableFrame(page, fg_color="#08111f", corner_radius=18)
        self.workbook_scroll.grid(row=2, column=0, padx=24, pady=(8, 24), sticky="nsew")
        self.workbook_scroll.grid_columnconfigure(0, weight=1)
        return page

    def build_dws_jms_page(self, master):
        # Scrollable page: Data Export has many fields, so it must remain usable
        # even when the window is locked to the compact War Room size.
        page = ctk.CTkScrollableFrame(master, fg_color="#0f172a", corner_radius=0)
        page.grid_columnconfigure(0, weight=1)
        self.header(page, "ส่งออกข้อมูล", "ตั้งค่าการดึงข้อมูล DWS/JMS และทดสอบการทำงานก่อนส่งเข้า Excel/Feishu").grid(row=0, column=0, padx=24, pady=(18, 8), sticky="ew")

        panel = self.make_card(page, "#111827", 22)
        panel.grid(row=1, column=0, padx=24, pady=6, sticky="ew")
        for i in range(4):
            panel.grid_columnconfigure(i, weight=1 if i in (1, 3) else 0)
        ctk.CTkLabel(panel, text="ตั้งค่าการดึงข้อมูล", text_color="#f8fafc", font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, columnspan=4, padx=18, pady=(12, 4), sticky="w")
        ctk.CTkLabel(panel, text="กำหนดตำแหน่งเก็บไฟล์, Token และจำนวนข้อมูลที่โหลดต่อครั้ง", text_color="#94a3b8").grid(row=1, column=0, columnspan=4, padx=18, pady=(0, 4), sticky="w")
        self.raw_path_entry = self.path_row_wide(panel, 2, "ตำแหน่งเก็บ Excel", self.browse_folder)
        self.dws_url = self.setting_row_wide(panel, 3, "URL ระบบ DWS")
        self.dws_token = self.setting_row_wide(panel, 4, "DWS Token")
        self.jms_token = self.setting_row_wide(panel, 5, "Token JMS")
        self.download_size_entry = self.setting_row_wide(panel, 6, "จำนวนรายการต่อรอบ")

        self.send_dws_file_var = ctk.BooleanVar(value=False)
        self.send_auto_file_var = ctk.BooleanVar(value=False)
        self.send_dwspda_file_var = ctk.BooleanVar(value=False)
        self.send_realtime_file_var = ctk.BooleanVar(value=False)

        tests = self.make_card(page, "#020617", 22)
        tests.grid(row=2, column=0, padx=24, pady=(6, 24), sticky="ew")
        tests.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(tests, text="ทดสอบการทำงาน", font=ctk.CTkFont(size=17, weight="bold"), text_color="#e2e8f0").grid(row=0, column=0, padx=16, pady=(12, 2), sticky="w")
        ctk.CTkLabel(tests, text="เลือกช่วงเวลาสำหรับทดสอบ แล้วรันแยกทีละขั้นได้ | Auto และ Run Now จะอิงเวลาจากหน้าหลัก", text_color="#94a3b8").grid(row=1, column=0, padx=16, pady=(0, 8), sticky="w")

        hours = [f"{i:02}:00" for i in range(24)]
        date_frame = ctk.CTkFrame(tests, fg_color="#0b1220", corner_radius=16, border_width=1, border_color="#17233a")
        date_frame.grid(row=2, column=0, padx=16, pady=(0, 8), sticky="ew")
        date_frame.grid_columnconfigure(1, weight=1)  # Start Date
        date_frame.grid_columnconfigure(5, weight=1)  # End Date
        ctk.CTkLabel(date_frame, text="Start", text_color="#dbeafe", width=56, anchor="w").grid(row=0, column=0, padx=(14, 8), pady=10, sticky="w")
        self.start_date = DateEntry(
            date_frame,
            width=28,
            date_pattern="yyyy-mm-dd",
            state="readonly",
            font=("Segoe UI", 15)
        )
        self.start_date.grid(row=0, column=1, padx=(4, 8), pady=10, sticky="we")
        self.start_hour = ctk.CTkOptionMenu(date_frame, values=hours, width=94, command=lambda _: self.save_config())
        self.start_hour.grid(row=0, column=2, padx=(4, 18), pady=10, sticky="w")
        ctk.CTkLabel(
            date_frame,
            text="→",
            text_color="#38bdf8",
            width=30,
            anchor="center",
            font=ctk.CTkFont(size=20, weight="bold")
        ).grid(
            row=0,
            column=3,
            padx=12,
            pady=10
        )
        ctk.CTkLabel(date_frame, text="End", text_color="#dbeafe", width=46, anchor="w").grid(row=0, column=4, padx=(18, 8), pady=10, sticky="e")
        self.end_date = DateEntry(
            date_frame,
            width=28,
            date_pattern="yyyy-mm-dd",
            state="readonly",
            font=("Segoe UI", 15)
        )
        self.end_date.grid(row=0, column=5, padx=(4, 8), pady=10, sticky="we")
        self.end_hour = ctk.CTkOptionMenu(date_frame, values=hours, width=94, command=lambda _: self.save_config())
        self.end_hour.grid(row=0, column=6, padx=(4, 14), pady=10, sticky="w")

        grid = ctk.CTkFrame(tests, fg_color="transparent")
        grid.grid(row=3, column=0, padx=8, pady=6, sticky="ew")
        for i in range(5):
            grid.grid_columnconfigure(i, weight=1)
        cards = [
            ("ดึงข้อมูลทั้งหมด", "DWS → AUTO → PDA → DB", "full", "#7c3aed", "#6d28d9"),
            ("ทดสอบ DWS", "โหลดไฟล์ DWS9-11", "dws", "#2563eb", "#1d4ed8"),
            ("ทดสอบ JMS Auto", "สแกน建包扫描", "jms_auto", "#0891b2", "#0e7490"),
            ("ทดสอบ JMS PDA", "สแกน卸车扫描", "jms_pda", "#059669", "#047857"),
            ("ทดสอบ Realtime DB", "โหลด Track RealTime", "realtime", "#ea580c", "#c2410c"),
        ]
        for idx, (title, desc, key, color, hover) in enumerate(cards):
            c = ctk.CTkFrame(grid, fg_color="#0b1220", corner_radius=18, border_width=1, border_color="#17233a")
            c.grid(row=0, column=idx, padx=6, pady=6, sticky="nsew")
            c.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(c, text=title, text_color="#f8fafc", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, padx=10, pady=(10, 2), sticky="w")
            ctk.CTkLabel(c, text=desc, text_color="#94a3b8", font=ctk.CTkFont(size=11), wraplength=165).grid(row=1, column=0, padx=10, pady=(0, 8), sticky="w")
            btn = ctk.CTkButton(c, text="RUN", height=32, fg_color=color, hover_color=hover, command=lambda k=key: self.run_dws_jms_task(k))
            btn.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="ew")
            self.lockable_buttons.append(btn)
            self.button_color_map[btn] = (color, hover)
        self.btn_dws_stop = ctk.CTkButton(tests, text="⛔ หยุด DWS/JMS", height=40, fg_color="#dc2626", hover_color="#b91c1c", command=self.stop_process)
        self.btn_dws_stop.grid(row=4, column=0, padx=16, pady=(4, 12), sticky="ew")
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

        ent.bind("<KeyPress>", on_key_press, add="+")
        ent.bind("<FocusOut>", normalize, add="+")
        ent.bind("<Control-v>", lambda _e: self.after(80, normalize), add="+")
        ent.bind("<KeyRelease>", lambda _e: self.after(1, normalize) if (collapse_internal_spaces or digits_only) else self.save_config_debounced(), add="+")
        return ent

    def dws_excel_file_row(self, parent, row, col, label, send_var):
        ctk.CTkLabel(parent, text=label, text_color="#cbd5e1", width=120, anchor="w").grid(
            row=row, column=col, padx=(16, 8), pady=10, sticky="w"
        )
        ent = ctk.CTkEntry(parent)
        ent.grid(row=row, column=col + 1, padx=(8, 8), pady=10, sticky="ew")
        self.bind_clean_entry(ent)
        chk = ctk.CTkCheckBox(
            parent,
            text="แนบไฟล์ Excel",
            variable=send_var,
            command=self.save_config,
            width=98,
        )
        chk.grid(row=row, column=col + 2, padx=(0, 16), pady=10, sticky="w")
        self.lockable_inputs.append(ent)
        self.lockable_buttons.append(chk)
        return ent

    def setting_row_wide(self, parent, row, label):
        ctk.CTkLabel(parent, text=label, text_color="#dbeafe", width=150, anchor="w").grid(row=row, column=0, padx=(18, 10), pady=10, sticky="w")
        ent = ctk.CTkEntry(parent, fg_color="#1f2937", border_color="#475569")
        ent.grid(row=row, column=1, columnspan=3, padx=(8, 16), pady=10, sticky="ew")
        self.bind_clean_entry(ent, collapse_internal_spaces=("token" in label.lower() or "chat" in label.lower() or "secret" in label.lower() or "app id" in label.lower()), digits_only=("จำนวนรายการ" in label or "download size" in label.lower()))
        self.lockable_inputs.append(ent)
        return ent

    def path_row_wide(self, parent, row, label, browse_func):
        ctk.CTkLabel(parent, text=label, text_color="#dbeafe", width=150, anchor="w").grid(row=row, column=0, padx=(18, 10), pady=10, sticky="w")
        ent = ctk.CTkEntry(parent, fg_color="#1f2937", border_color="#475569")
        ent.grid(row=row, column=1, columnspan=2, padx=(8, 10), pady=10, sticky="ew")
        btn = ctk.CTkButton(parent, text="Browse", width=108, fg_color="#334155", hover_color="#475569", command=lambda: self.set_path(ent, browse_func()))
        btn.grid(row=row, column=3, padx=(8, 16), pady=10, sticky="e")
        self.bind_clean_entry(ent)
        self.lockable_inputs.append(ent)
        self.lockable_buttons.append(btn)
        return ent

    def setting_row_at(self, parent, row, col, label):
        ctk.CTkLabel(parent, text=label, text_color="#cbd5e1", width=120, anchor="w").grid(row=row, column=col, padx=(16, 8), pady=10, sticky="w")
        ent = ctk.CTkEntry(parent)
        ent.grid(row=row, column=col + 1, padx=(8, 16), pady=10, sticky="ew")
        self.bind_clean_entry(ent)
        self.lockable_inputs.append(ent)
        return ent


    def build_output_manager_page(self, master):
        page = ctk.CTkScrollableFrame(master, fg_color="#0f172a", corner_radius=0)
        page.grid_columnconfigure(0, weight=1)
        self.header(page, "จัดการไฟล์", "รวมชื่อไฟล์ Excel ที่สร้าง และไฟล์ Excel ที่ใช้งานไว้ในที่เดียว").grid(row=0, column=0, padx=24, pady=(18, 8), sticky="ew")

        files = self.make_card(page, "#111827", 22)
        files.grid(row=1, column=0, padx=24, pady=6, sticky="ew")
        files.grid_columnconfigure(1, weight=1)
        files.grid_columnconfigure(4, weight=1)
        ctk.CTkLabel(
            files,
            text="ชื่อไฟล์ Excel ที่สร้าง",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#f8fafc",
        ).grid(row=0, column=0, columnspan=6, padx=16, pady=(10, 2), sticky="w")
        ctk.CTkLabel(
            files,
            text="กำหนดชื่อไฟล์ .xlsx ที่ระบบสร้างจาก DWS/JMS และเลือกได้ว่าจะให้แนบไฟล์ Excel ไปกับ Feishu หรือไม่",
            text_color="#94a3b8",
            wraplength=920,
            justify="left",
        ).grid(row=1, column=0, columnspan=6, padx=16, pady=(0, 6), sticky="w")

        self.name_dws = self.dws_excel_file_row(files, 2, 0, "ไฟล์ DWS", self.send_dws_file_var)
        self.name_auto = self.dws_excel_file_row(files, 2, 3, "ไฟล์ JMS Auto", self.send_auto_file_var)
        self.name_dwspda = self.dws_excel_file_row(files, 3, 0, "ไฟล์ JMS PDA", self.send_dwspda_file_var)
        self.name_realtime_db = self.dws_excel_file_row(files, 3, 3, "ไฟล์ Realtime DB", self.send_realtime_file_var)

        self.workspace_output_frame = ctk.CTkFrame(page, fg_color="#0b1220", corner_radius=18, border_width=1, border_color="#17233a")
        self.workspace_output_frame.grid(row=2, column=0, padx=24, pady=(8, 24), sticky="ew")
        self.workspace_output_frame.grid_columnconfigure(0, weight=1)
        self.workspace_output_rows = []
        self.refresh_workspace_output_names()
        return page

    def build_settings_page(self, master):
        page = ctk.CTkFrame(master, fg_color="#0f172a")
        page.grid_columnconfigure(0, weight=1)
        self.header(page, "ตั้งค่า", "ตั้งค่าโฟลเดอร์เก็บรูป และข้อมูลเชื่อมต่อ Feishu").grid(row=0, column=0, padx=24, pady=(22, 12), sticky="ew")

        path = self.make_card(page, "#111827", 22)
        path.grid(row=1, column=0, padx=24, pady=8, sticky="ew")
        path.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(path, text="โฟลเดอร์เก็บรูป", text_color="#f8fafc", font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=0, padx=16, pady=(16, 0), sticky="w")
        self.out_entry = self.path_row(path, 1, "ตำแหน่งเก็บรูป", self.browse_folder)

        feishu = self.make_card(page, "#111827", 22)
        feishu.grid(row=2, column=0, padx=24, pady=8, sticky="ew")
        feishu.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(feishu, text="ตั้งค่า Feishu (Chat ID Mode)", text_color="#f8fafc", font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=0, padx=16, pady=(16, 0), sticky="w")
        ctk.CTkLabel(feishu, text="ใช้ App ID / App Secret / Chat ID สำหรับส่งเข้า Feishu", text_color="#94a3b8").grid(row=1, column=0, columnspan=2, padx=16, pady=(4, 6), sticky="w")
        self.app_id_entry = self.setting_row(feishu, 2, "App ID")
        self.app_secret_entry = self.setting_row(feishu, 3, "App Secret")
        self.chat_id_entry = self.setting_row(feishu, 4, "Chat ID")

        tips = self.make_card(page, "#08111f", 22)
        tips.grid(row=3, column=0, padx=24, pady=8, sticky="ew")
        ctk.CTkLabel(tips, text="Tip: โฟลเดอร์เก็บรูปคือปลายทางรูปที่สร้างจาก Excel ส่วนโฟลเดอร์เก็บ Excel อยู่ในหน้าส่งออกข้อมูล", text_color="#94a3b8").grid(row=0, column=0, padx=16, pady=14, sticky="w")

        self.btn_save_settings = ctk.CTkButton(page, text="💾 บันทึกการตั้งค่า", height=40, fg_color="#334155", hover_color="#475569", command=self.save_all)
        self.btn_save_settings.grid(row=4, column=0, padx=24, pady=16, sticky="e")
        return page

    def path_row(self, parent, row, label, browse_func):
        ctk.CTkLabel(parent, text=label, text_color="#cbd5e1", width=130, anchor="w").grid(row=row, column=0, padx=(16, 8), pady=10, sticky="w")
        ent = ctk.CTkEntry(parent)
        ent.grid(row=row, column=1, padx=8, pady=10, sticky="ew")
        self.bind_clean_entry(ent)
        btn = ctk.CTkButton(parent, text="Browse", width=92, fg_color="#334155", hover_color="#475569", command=lambda: self.set_path(ent, browse_func()))
        btn.grid(row=row, column=2, padx=(8, 16), pady=10)
        self.lockable_inputs.append(ent)
        self.lockable_buttons.append(btn)
        return ent

    def setting_row(self, parent, row, label):
        ctk.CTkLabel(parent, text=label, text_color="#cbd5e1", width=130, anchor="w").grid(row=row, column=0, padx=(16, 8), pady=10, sticky="w")
        ent = ctk.CTkEntry(parent)
        ent.grid(row=row, column=1, padx=(8, 16), pady=10, sticky="ew")
        self.bind_clean_entry(ent, collapse_internal_spaces=("token" in label.lower() or "chat" in label.lower() or "secret" in label.lower() or "app id" in label.lower()), digits_only=("จำนวนรายการ" in label or "download size" in label.lower()))
        self.lockable_inputs.append(ent)
        return ent

    def show_page(self, name):
        for p in self.pages.values():
            p.grid_remove()
        self.pages[name].grid(row=0, column=0, sticky="nsew")
        for btn in [self.nav_home, self.nav_workbooks, self.nav_dws_jms, self.nav_output, self.nav_settings]:
            btn.configure(fg_color="#1f2937")
        {"home": self.nav_home, "workbooks": self.nav_workbooks, "dws_jms": self.nav_dws_jms, "output_manager": self.nav_output, "settings": self.nav_settings}[name].configure(fg_color="#2563eb")

    def load_values_to_ui(self):
        self.out_entry.insert(0, self.config["PATH"].get("output_dir", ""))
        self.minute_var.set(self.config["TIME"].get("run_minute", "5"))
        self.start_hour_var.set(f'{int(self.config["TIME"].get("start_hour", "15")):02}:00')
        self.end_hour_var.set(f'{int(self.config["TIME"].get("end_hour", "12")):02}:00')
        self.app_id_entry.insert(0, self.config["FEISHU"].get("APP_ID", ""))
        self.app_secret_entry.insert(0, self.config["FEISHU"].get("APP_SECRET", ""))
        if hasattr(self, "chat_id_entry"):
            self.chat_id_entry.insert(0, self.config["FEISHU"].get("CHAT_ID", ""))
        dws = self.config["DWS_JMS"] if "DWS_JMS" in self.config else {}
        if hasattr(self, "raw_path_entry"):
            self.raw_path_entry.insert(0, dws.get("raw_path", "") or self.config["PATH"].get("output_dir", ""))
        if hasattr(self, "bot_export_var"):
            self.bot_export_var.set(as_bool(dws.get("enabled", "true"), True))
        if hasattr(self, "bot_chat_var"):
            self.bot_chat_var.set(as_bool(dws.get("bot_chat_enabled", "true"), True))
        self.dws_url.insert(0, dws.get("dws_url", ""))
        self.dws_token.insert(0, dws.get("dws_token", ""))
        self.jms_token.insert(0, dws.get("jms_token", ""))
        if hasattr(self, "download_size_entry"):
            self.download_size_entry.insert(0, dws.get("download_size", "28888"))
        self.name_dws.insert(0, dws.get("name_dws", "DWS9-11.xlsx"))
        self.name_auto.insert(0, dws.get("name_auto", "DWSXAUTOPDA.xlsx"))
        self.name_dwspda.insert(0, dws.get("name_dwspda", "DWSPDA.xlsx"))
        self.name_realtime_db.insert(0, dws.get("name_realtime_db", "RealtimeDB.xlsx"))
        if hasattr(self, "send_dws_file_var"):
            self.send_dws_file_var.set(as_bool(dws.get("send_dws_file", "false"), False))
            self.send_auto_file_var.set(as_bool(dws.get("send_auto_file", "false"), False))
            self.send_dwspda_file_var.set(as_bool(dws.get("send_dwspda_file", "false"), False))
            self.send_realtime_file_var.set(as_bool(dws.get("send_realtime_file", "false"), False))
        today = datetime.now().date()
        start_date = dws.get("start_date", "") or today.strftime("%Y-%m-%d")
        end_date = dws.get("end_date", "") or (today + timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            self.start_date.set_date(start_date)
            self.end_date.set_date(end_date)
        except Exception:
            self.start_date.set_date(today)
            self.end_date.set_date(today + timedelta(days=1))
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
        if hasattr(self, "workspace_output_frame"):
            self.refresh_workspace_output_names()

    def refresh_workspace_output_names(self):
        """Mirror Excel files into Output Manager for future-proof visibility."""
        frame = getattr(self, "workspace_output_frame", None)
        if frame is None:
            return
        for widget in frame.winfo_children():
            widget.destroy()

        keys = [x.strip() for x in self.config["WORKBOOKS"].get("items", "").split(",") if x.strip()]
        ctk.CTkLabel(
            frame,
            text="ไฟล์ Excel ที่ใช้งาน",
            text_color="#f8fafc",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, padx=12, pady=(10, 2), sticky="w")
        ctk.CTkLabel(
            frame,
            text="รายการนี้ sync จากหน้าไฟล์ Excel อัตโนมัติ ใช้ดูไฟล์ที่เพิ่มเข้ามาเพื่อรองรับการต่อยอดในอนาคต",
            text_color="#94a3b8",
            wraplength=860,
            justify="left",
        ).grid(row=1, column=0, padx=12, pady=(0, 8), sticky="w")

        if not keys:
            ctk.CTkLabel(frame, text="ยังไม่มีไฟล์จากหน้าไฟล์ Excel", text_color="#64748b").grid(row=2, column=0, padx=12, pady=(0, 10), sticky="w")
            return

        for idx, key in enumerate(keys, start=2):
            sec = self.config[f"WORKBOOK:{key}"] if f"WORKBOOK:{key}" in self.config else {}
            display = sec.get("display_name", key)
            path = sec.get("path", "")
            row = ctk.CTkFrame(frame, fg_color="#111827", corner_radius=10)
            row.grid(row=idx, column=0, padx=12, pady=4, sticky="ew")
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(row, text="📘", width=34).grid(row=0, column=0, padx=(10, 4), pady=7)
            ent = ctk.CTkEntry(row, fg_color="#1f2937", border_color="#334155", text_color="#cbd5e1")
            ent.insert(0, display)
            ent.configure(state="disabled")
            ent.grid(row=0, column=1, padx=4, pady=7, sticky="ew")
            ctk.CTkLabel(row, text=path, text_color="#64748b", anchor="w").grid(row=1, column=1, padx=4, pady=(0, 7), sticky="ew")

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

    def write_log(self, msg, level: str = None):
        """Professional Live Log formatter.

        Accepts old debug-style messages from DWS/JMS/Createphoto/Botmessage
        and normalizes them into clean operator-facing text.
        """
        self.last_activity = time.time()
        if not hasattr(self, "log_box"):
            return

        stamp = datetime.now().strftime("%H:%M:%S")
        icon, text, tag = self.format_log_line(str(msg), level=level)

        line = f"[{stamp}] {icon} {text}\n"
        try:
            self.log_box.insert("end", line, tag)
        except Exception:
            self.log_box.insert("end", line)
        self.log_box.see("end")

    def format_log_line(self, raw: str, level: str = None):
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
        self.progress.set(max(0, min(100, val)) / 100)

    def set_status(self, text, color="#5dff9e", bg="#123524"):
        self.status_pill.configure(text=text, text_color=color, fg_color=bg)
        if hasattr(self, "mode_label"):
            self.mode_label.configure(text=text)

    def update_clock(self):
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if hasattr(self, "clock_label"):
            self.clock_label.configure(text=now_text)
        self.update_business_panel()
        self.after(1000, self.update_clock)

    def set_runtime_lock_visuals(self, active: bool):
        """Lock all configuration controls during Run Now / Auto processing and make the disabled state visible."""
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
                    fg_color="#0b1220" if active else "#1f2937",
                    text_color="#64748b" if active else "#f8fafc",
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
                normal_fg, normal_hover = original if original else ("#334155", "#475569")
                btn.configure(
                    state="disabled" if active else "normal",
                    fg_color="#1e293b" if active else normal_fg,
                    hover_color="#1e293b" if active else normal_hover,
                    text_color="#64748b" if active else "#ffffff",
                )
            except Exception:
                pass

        if active:
            self.set_status("UI Locked", "#cbd5e1", "#1e293b")
        elif not self.running and not self.scheduler_running:
            self.set_status("Idle", "#5dff9e", "#123524")

    def set_ui_running(self, active: bool):
        self.set_runtime_lock_visuals(active)
        state = "disabled" if active else "normal"
        for w in [
            getattr(self, "minute_menu", None), getattr(self, "start_menu", None), getattr(self, "end_menu", None),
            getattr(self, "chk_bot_export", None), getattr(self, "chk_bot_chat", None),
            getattr(self, "nav_workbooks", None), getattr(self, "nav_dws_jms", None), getattr(self, "nav_output", None), getattr(self, "nav_settings", None)
        ]:
            if w is None:
                continue
            try:
                w.configure(state=state)
            except Exception:
                pass

        if active and self.mode == "manual":
            try:
                self.btn_run.grid_remove()
                self.btn_stop_run.grid(row=1, column=7, padx=8, pady=10, sticky="ew")
            except Exception:
                pass
        else:
            try:
                self.btn_stop_run.grid_remove()
                self.btn_run.grid(row=1, column=7, padx=8, pady=10, sticky="ew")
            except Exception:
                pass

        if self.scheduler_running:
            try:
                self.btn_start.grid_remove()
                self.btn_stop_auto.grid(row=1, column=6, padx=8, pady=10, sticky="ew")
            except Exception:
                pass
        else:
            try:
                self.btn_stop_auto.grid_remove()
                self.btn_start.grid(row=1, column=6, padx=8, pady=10, sticky="ew")
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
            if self.running and time.time() - self.last_activity > 180:
                self.write_log("⚠ Watchdog: Process stuck → stopping")
                self.stop_all()

    def is_busy(self):
        return self.running

    def validate_run_selection(self):
        run_export = bool(getattr(self, "bot_export_var", ctk.BooleanVar(value=True)).get())
        run_chat = bool(getattr(self, "bot_chat_var", ctk.BooleanVar(value=True)).get())
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
        raw = getattr(self, "raw_path_entry", None)
        if raw is not None and entry_value(raw):
            return entry_value(raw)
        return entry_value(self.out_entry)

    def get_download_size(self, write_warning: bool = True) -> int:
        raw = entry_value(getattr(self, "download_size_entry", None), collapse_internal_spaces=True) or "28888"
        try:
            size = int(raw)
        except Exception:
            size = 28888
            if write_warning and hasattr(self, "write_log"):
                self.write_log("จำนวนรายการต่อรอบไม่ถูกต้อง ระบบใช้ค่าเริ่มต้น 28888", level="WARN")
        size = max(1, min(size, 200000))
        return size

    def validate_dws_jms_ready(self):
        if not self.get_raw_export_folder():
            self.write_log("Please select Raw Export Folder")
            return False
        if not entry_value(self.dws_url):
            self.write_log("Missing DWS URL")
            return False
        if not entry_value(self.dws_token, collapse_internal_spaces=True):
            self.write_log("Missing DWS Token")
            return False
        if not entry_value(self.jms_token, collapse_internal_spaces=True):
            self.write_log("Missing JMS AuthToken")
            return False
        return True

    def get_time_range(self):
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
            time.sleep(1)
        return True

    def run_dws_jms_task(self, mode="full"):
        if self.running:
            self.write_log("DWS/JMS blocked: another process is running")
            return
        if not self.validate_dws_jms_ready():
            return
        self.save_all()
        self.mode = "manual"
        self.current_run_source = f"dws_jms:{mode}"
        self.stop_requested = False
        self.set_ui_running(True)
        self.task_queue.put(lambda m=mode: self.run_dws_jms_process(m))

    def run_dws_jms_process(self, mode="full"):
        if self.running:
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
        try:
            self.running = True
            self.raw_time_source = "manual"
            self.set_pipeline_state(None, "ready")
            self.set_status("DWS/JMS Running", "#ddd6fe", "#3b1f66")
            self.set_progress(5)
            start, end = self.get_time_range()
            self.write_log(f"Start DWS/JMS mode: {mode} | {start} → {end}")
            total = max(1, len(steps))
            for idx, (key, name, func) in enumerate(steps, start=1):
                if self.stop_requested or not self.running:
                    self.write_log("DWS/JMS stopped")
                    self.set_pipeline_state(key, "skip")
                    return
                self.set_pipeline_state(key, "run")
                self.write_log(f"Run step {idx}/{total}: {name}")
                func()
                self.set_pipeline_state(key, "ok")
                self.set_progress(int(idx / total * 100))
                if mode == "full" and idx < total:
                    if not self.sleep_with_stop(3):
                        self.write_log("DWS/JMS stopped during wait")
                        return
            self.write_log("DWS/JMS Done ✅")
            self.set_status("Idle", "#5dff9e", "#123524")
        except Exception as e:
            self.write_log(f"DWS/JMS ERROR: {e}")
            try:
                self.set_pipeline_state(key, "error")
            except Exception:
                pass
            self.set_status("Error", "#fecaca", "#4c1118")
        finally:
            self.running = False
            self.stop_requested = False
            self.raw_time_source = "manual"
            self.current_run_source = None
            if not self.scheduler_running:
                self.set_ui_running(False)

    def run_dws(self):
        self.log("Export started", "DWS", "START")

        start, end = self.get_time_range()

        try:
            url = entry_value(self.dws_url)
            token = entry_value(self.dws_token, collapse_internal_spaces=True)
            data = {
                "startTime": start,
                "endTime": end,
                "barcodeList": [],
                "businessTimeType": "",
                "dbCode": "2",
                "curPage": 1,
                "pageSize": 20
            }
            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "token": token,
                "Origin": "http://10.30.32.10",
                "Referer": "http://10.30.32.10/",
                "User-Agent": "Mozilla/5.0"
            }

            res = requests.post(url, json=data, headers=headers, timeout=(5, 10))

            if res.status_code == 200 and len(res.content) > 100:
                raw_dir = self.get_raw_export_folder()
                os.makedirs(raw_dir, exist_ok=True)
                path = os.path.join(raw_dir, entry_value(self.name_dws) or "DWS9-11.xlsx")
                with open(path, "wb") as f:
                    f.write(res.content)

                size_mb = len(res.content) / 1024 / 1024
                self.write_log(f"DWS9-11.xlsx saved | {size_mb:.2f} MB | HTTP {res.status_code}", level="SUCCESS")
            else:
                self.after(0, lambda: self.log(f"Download failed: {res.text[:200]}"))
                self.after(0, lambda: self.set_status("Error", "#fecaca", "#4c1118"))

        except Exception as e:
            self.after(0, lambda: self.log(f"ERROR: {e}"))
            self.after(0, lambda: self.set_status("Error", "#fecaca", "#4c1118"))

        finally:
            self.log("Export completed", "DWS", "SUCCESS")


    def run_jms_auto(self):
        base = "https://jmsgw.jtexpress.co.th/operatingplatform"
        token = entry_value(self.jms_token, collapse_internal_spaces=True)

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authtoken": token,
            "origin": "https://jms.jtexpress.co.th",
            "referer": "https://jms.jtexpress.co.th/",
            "routename": "scanComparisonNew",
            "lang": "TH",
            "langtype": "TH",
        }

        start, end = self.get_time_range()

        self._export_jms(base, headers, start, end, "建包扫描", entry_value(self.name_auto) or "DWSXAUTOPDA.xlsx")


    def run_jms_pda(self):
        base = "https://jmsgw.jtexpress.co.th/operatingplatform"
        token = entry_value(self.jms_token, collapse_internal_spaces=True)

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authtoken": token,
            "origin": "https://jms.jtexpress.co.th",
            "referer": "https://jms.jtexpress.co.th/",
            "routename": "scanComparisonNew",
            "lang": "TH",
            "langtype": "TH",
        }

        start, end = self.get_time_range()

        self._export_jms(base, headers, start, end, "卸车扫描", entry_value(self.name_dwspda) or "DWSPDA.xlsx")

    def run_realtime_db(self):

        self.log("Loading Realtime DB", "REALTIME")

        token = entry_value(
            self.jms_token,
            collapse_internal_spaces=True
        )

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

        r.raise_for_status()

        export_data = r.json()

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

            resp.raise_for_status()

            data = resp.json()

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
            entry_value(
                self.name_realtime_db
            ) or "RealtimeDB.xlsx"
        )

        if len(file_resp.content) < 1000:
            raise Exception(
                "Downloaded file too small"
            )

        with open(save_path, "wb") as f:
            f.write(file_resp.content)

        self.log(
            f"Downloaded {os.path.basename(save_path)}",
            "REALTIME"
        )

        return save_path

    def _export_jms(self, base, headers, start, end, scanType, filename):

        self.log(
            f"Loading JMS data → {filename}",
            "JMS"
        )

        url = f"{base}/scanningContrast/queryOrder"

        all_rows = []

        page = 1
        size = self.get_download_size()

        while True:

            if self.stop_requested:
                return

            payload = {
                "current": page,
                "size": size,
                "startTimeStr": start,
                "endTimeStr": end,
                "scanNetworkCode": "999004",
                "scanType": scanType,
                "excelType": "downExcelAll",
                "sortName": "scanDate",
                "sortOrder": "asc",
                "billType": 0,
                "countryId": "1",
                "contrastScanType": "发件扫描"
            }

            res = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=(10, 60)
            )

            if "login" in res.text.lower():
                raise Exception("JMS token expired")

            data = res.json()

            records = data.get("data", {}).get("records", [])

            if not records:
                break

            all_rows.extend(records)

            total = data.get("data", {}).get("total", 0)

            self.log(
                f"Page {page} loaded ({len(all_rows)}/{total})",
                "JMS"
            )

            if len(records) < size:
                break

            page += 1

        if not all_rows:
            raise Exception("No JMS data")

        self.log(
            "Creating Excel file",
            "JMS"
        )

        df = pd.DataFrame(all_rows)

        column_map = {
            "billNo": "หมายเลข AWB",
            "belongNo": "หมายเลขกระสอบ",
            "scanType": "ประเภทที่สแกน",
            "scanDate": "เวลาที่สแกน",
            "listNo": "สาขาที่สแกน",
            "upOrNextStation": "สถานีก่อนหน้าหรือสถานีถัดไป",
            "piece": "จำนวนพัสดุ",
            "weight": "น้ำหนัก",
            "goodsType": "ประเภทสินค้า",
            "expreeType": "ประเภทการขนส่ง",
            "sendSite": "สาขาต้นทาง",
            "sendCus": "ลูกค้าผู้ส่ง",
            "scanEmp": "พนักงานสแกน",
            "dispatchReciper": "ผู้จัดส่งหรือพนักงงานรับพัสดุ",
            "remark": "หมายเหตุ",
            "inputDate": "เวลาอัปโหลด"

        }

        df = df.rename(columns=column_map)

        wanted_columns = list(column_map.values())

        existing_columns = [
            c for c in wanted_columns
            if c in df.columns
        ]

        df = df[existing_columns]

        # =========================
        # เพิ่มตรงนี้
        # =========================

        scan_type_map = {
            "卸车扫描": "สแกนกระสอบลงรถ",
            "发件扫描": "สแกนส่งพัสดุ",
            "到件扫描": "สแกนรับพัสดุ",
            "集包扫描": "สแกนรวมถุง",
            "拆包扫描": "สแกนแยกถุง",
        }

        if "ประเภทที่สแกน" in df.columns:
            df["ประเภทที่สแกน"] = (
                df["ประเภทที่สแกน"]
                .map(scan_type_map)
                .fillna(df["ประเภทที่สแกน"])
            )

        # format เวลา

        date_columns = [
            "เวลาที่สแกน",
            "เวลาอัปโหลด"
        ]

        for col in date_columns:
            if col in df.columns:
                df[col] = pd.to_datetime(
                    df[col],
                    errors="coerce"
                ).dt.strftime("%Y-%m-%d %H:%M:%S")

        # เรียงใหม่ให้เหมือนไฟล์ JMS

        final_order = [
            "หมายเลข AWB",
            "หมายเลขกระสอบ",
            "ประเภทที่สแกน",
            "เวลาที่สแกน",
            "สาขาที่สแกน",
            "สถานีก่อนหน้าหรือสถานีถัดไป",
            "จำนวนพัสดุ",
            "น้ำหนัก",
            "ประเภทสินค้า",
            "ประเภทการขนส่ง",
            "สาขาต้นทาง",
            "ลูกค้าผู้ส่ง",
            "พนักงานสแกน",
            "ผู้จัดส่งหรือพนักงงานรับพัสดุ",
            "หมายเหตุ",
            "เวลาอัปโหลด"

        ]

        final_order = [
            c for c in final_order
            if c in df.columns
        ]

        df = df[final_order]

        raw_dir = self.get_raw_export_folder()
        os.makedirs(raw_dir, exist_ok=True)

        save_path = os.path.join(
            raw_dir,
            filename
        )

        df.to_excel(save_path, index=False)

        self.log(
            f"File saved → {filename}",
            "JMS",
            "SUCCESS"
        )
        

    def get_selected_excel_files_for_feishu(self):
        raw_dir = self.get_raw_export_folder()
        selections = [
            (getattr(self, "send_dws_file_var", None), getattr(self, "name_dws", None)),
            (getattr(self, "send_auto_file_var", None), getattr(self, "name_auto", None)),
            (getattr(self, "send_dwspda_file_var", None), getattr(self, "name_dwspda", None)),
            (getattr(self, "send_realtime_file_var", None), getattr(self, "name_realtime_db", None)),
        ]
        paths = []
        for var, entry in selections:
            if var is None or entry is None or not var.get():
                continue
            filename = entry_value(entry)
            if not filename:
                continue
            path = os.path.join(raw_dir, filename)
            if os.path.exists(path):
                paths.append(path)
            else:
                self.write_log(f"Excel attachment not found — {filename}", level="WARN")
        return paths

    def get_tenant_access_token_for_file_send(self):
        app_id = entry_value(self.app_id_entry, collapse_internal_spaces=True)
        app_secret = entry_value(self.app_secret_entry, collapse_internal_spaces=True)
        if not app_id or not app_secret:
            raise Exception("Missing APP_ID / APP_SECRET")

        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/"
        res = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=15).json()
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
            res = requests.post(url, headers=headers, data=data, files=files, timeout=60).json()
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
        res = requests.post(url, headers=headers, json=payload, timeout=30).json()
        if res.get("code") != 0:
            raise Exception(f"Send file message failed: {res}")
        return res

    def send_selected_excel_files_to_feishu(self):
        files = self.get_selected_excel_files_for_feishu()
        if not files:
            return

        chat_id = entry_value(self.chat_id_entry, collapse_internal_spaces=True) if hasattr(self, "chat_id_entry") else ""
        if not chat_id:
            self.write_log("Excel file sending skipped — Chat ID is empty", level="WARN")
            return

        self.write_log(f"Excel attachment delivery started — {len(files)} file(s)", level="START")
        token = self.get_tenant_access_token_for_file_send()
        for file_path in files:
            if not self.running:
                return
            filename = os.path.basename(file_path)
            self.write_log(f"Uploading Excel file — {filename}", level="START")
            file_key = self.upload_excel_file_to_feishu(token, file_path)
            self.write_log(f"Sending Excel file — {filename}", level="START")
            self.send_feishu_file_message(token, chat_id, file_key)
            self.write_log(f"Excel file sent — {filename}", level="SUCCESS")
            time.sleep(0.8)

    def run_process(self):
        if self.running:
            return
        out = self.validate_ready()
        if not out:
            return
        self.save_all()
        try:
            self.running = True
            # UI was already locked on the main thread by Run Now / Start Auto.
            # Keep the status update here only.
            self.set_status("Processing", "#fed7aa", "#4a2d13")
            self.set_progress(8)
            self.set_pipeline_state(None, "ready")
            self.write_log("Pipeline started", level="START")

            run_export = bool(getattr(self, "bot_export_var", ctk.BooleanVar(value=True)).get())
            run_chat = bool(getattr(self, "bot_chat_var", ctk.BooleanVar(value=True)).get())

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
                for idx, (key, name, func) in enumerate(raw_steps, start=1):
                    if not self.running or self.stop_requested:
                        return
                    self.set_pipeline_state(key, "run")
                    self.write_log(f"Raw export step {idx}/4 — {name}", level="START")
                    func()
                    self.set_pipeline_state(key, "ok")
                    self.set_progress(8 + idx * 9)
                self.raw_time_source = "manual"
            else:
                for key in ["dws", "jms_auto", "jms_pda", "realtime"]:
                    self.set_pipeline_state(key, "skip")

            if run_chat:
                self.set_pipeline_state("excel", "run")
                run_create(out, log=self.write_log, is_running=lambda: self.running)
                self.set_pipeline_state("excel", "ok")
                self.set_progress(62)
                if not self.running:
                    self.set_status("Stopped", "#fecaca", "#4c1118")
                    return

                self.write_log("Feishu delivery started", level="START")
                self.set_status("Sending", "#bae6fd", "#0c344d")
                self.set_pipeline_state("feishu", "run")
                self.set_progress(76)
                try:
                    run_send.ui = lambda stage: self.set_status(stage.capitalize(), "#bae6fd", "#0c344d")
                    run_send(out, log=self.write_log, is_running=lambda: self.running)
                    self.send_selected_excel_files_to_feishu()
                    self.set_pipeline_state("feishu", "ok")
                finally:
                    if hasattr(run_send, "ui"):
                        del run_send.ui
            else:
                self.set_pipeline_state("excel", "skip")
                self.set_pipeline_state("feishu", "skip")

            self.set_progress(100)
            self.write_log("Pipeline completed successfully", level="SUCCESS")
            if self.scheduler_running:
                self.set_status("Auto Waiting", "#bfdbfe", "#172554")
            else:
                self.set_status("Idle", "#5dff9e", "#123524")
        except Exception as e:
            self.write_log(f"Pipeline failed — {e}", level="ERROR")
            self.set_pipeline_state("excel", "error")
            self.set_status("Error", "#fecaca", "#4c1118")
        finally:
            self.running = False
            self.raw_time_source = "manual"
            self.current_run_source = None

            if not self.scheduler_running:
                self.set_ui_running(False)

            # คืนสถานะ Pipeline กลับเป็น READY
            self.after(1500, lambda: self.set_pipeline_state())

    def run_once(self):
        # ห้ามกดถ้ามี process กำลังรัน
        if self.running:
            self.write_log("Run Now blocked: another process is running")
            return

        if not self.validate_ready():
            return

        self.mode = "manual"
        self.current_run_source = "manual"

        self.set_ui_running(True)
        self.task_queue.put(self.run_process)

    def start_scheduler(self):
        if self.is_busy():
            return
        if not self.validate_ready():
            return
        self.save_all()
        self.mode = "auto"
        self.scheduler_running = True
        self.last_run_minute = None

        # Auto mode must also lock Workspace/Settings while waiting for the next scheduled run.
        self.set_ui_running(True)
        self.set_status("Auto Running", "#bfdbfe", "#172554")
        self.write_log("Auto scheduler started", level="START")
        threading.Thread(target=self.scheduler_loop, daemon=True).start()

    def scheduler_loop(self):
        while self.scheduler_running:
            try:
                now = datetime.now()
                minute = int(self.minute_var.get() or 5)
                now_key = now.strftime("%Y-%m-%d %H:%M")
                if self.is_in_time_range():
                    if now.minute == minute and self.last_run_minute != now_key:
                        # ถ้ามี process กำลังรัน → ข้ามรอบนี้
                        if self.running:
                            self.last_run_minute = now_key
                            self.write_log(
                                f"Skip auto run {now.strftime('%H:%M')} (busy with {self.current_run_source})"
                            )
                            continue

                        self.last_run_minute = now_key
                        self.current_run_source = "auto"

                        self.write_log(f"Trigger auto run at {now.strftime('%H:%M')}")
                        self.task_queue.put(self.run_process)
                else:
                    if not self.running:
                        self.set_status("Outside Time", "#cbd5e1", "#334155")
                time.sleep(1)
            except Exception as e:
                self.write_log(f"Scheduler error: {e}")
                time.sleep(1)

    def is_in_time_range(self):
        current_hour = datetime.now().hour
        start = int(self.start_hour_var.get().replace(":00", "") or 15)
        end = int(self.end_hour_var.get().replace(":00", "") or 12)
        if start < end:
            return start <= current_hour < end
        return current_hour >= start or current_hour < end
    
    def stop_scheduler(self):
        """หยุดเฉพาะ Auto Scheduler"""
        self.scheduler_running = False

        self.btn_stop_auto.grid_remove()
        self.btn_start.configure(state="normal")

        # ถ้าไม่มี process รันอยู่ → unlock UI
        if not self.running:
            self.set_ui_running(False)

        self.write_log("Auto scheduler stopped", level="SUCCESS")

        if self.running:
            self.set_status("Processing", "#fed7aa", "#4a2d13")
        else:
            self.set_status("Idle", "#5dff9e", "#123524")


    def stop_process(self):
        """หยุดเฉพาะ Process ปัจจุบัน"""
        if not self.running:
            return

        self.running = False
        self.stop_requested = True
        self.current_run_source = None

        self.btn_stop_run.grid_remove()
        self.btn_run.grid(
            row=1,
            column=7,
            padx=(8, 16),
            pady=8,
            sticky="ew"
        )

        # ถ้า auto ยังเปิดอยู่ → lock UI ต่อ
        if not self.scheduler_running:
            self.set_ui_running(False)

        self.write_log("Current process stopped by user", level="WARN")

        if self.scheduler_running:
            self.set_status("Auto Waiting", "#bfdbfe", "#172554")
        else:
            self.set_status("Stopped", "#fecaca", "#4c1118")

    def stop_all(self):
        self.last_run_minute = None
        self.scheduler_running = False
        self.running = False
        self.stop_requested = True
        self.set_status("Stopped", "#fecaca", "#4c1118")
        self.set_ui_running(False)
        self.write_log("All running tasks stopped", level="WARN")

    def on_close(self):
        self.stop_all()
        self.worker_running = False
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
