# -*- coding: utf-8 -*-
"""
Feishu Dispatcher + Message Recall Manager:
1. ส่งการ์ดสรุปยอดรายชั่วโมง + รูปตาราง + กราฟ (PNG) + ไฟล์ Excel (.xlsx) เข้ากลุ่ม Feishu
2. บันทึกประวัติ message_id ลงไฟล์ sent_feishu_history.json
3. รองรับยกเลิกข้อความ (Recall / Unsend) รายข้อความ, ยกเลิกชุดล่าสุด, และ Auto-Recall รอบก่อนหน้าอัตโนมัติ
"""
import base64
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import sys
import time
from pathlib import Path
import uuid
import requests

from .cogy_client import format_dws_name, format_wcs_name, short_machine_name

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
HISTORY_FILE = BASE_DIR / "sent_feishu_history.json"
MAX_HISTORY = 300
DPAPI_PREFIX = "DPAPI:"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def unprotect_secret(stored_text: str) -> str:
    if not stored_text:
        return ""
    if not stored_text.startswith(DPAPI_PREFIX):
        return stored_text
    try:
        enc = base64.b64decode(stored_text[len(DPAPI_PREFIX):].encode("ascii"))
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        buf = ctypes.create_string_buffer(enc, len(enc))
        blob_in = _DATA_BLOB(len(enc), buf)
        blob_out = _DATA_BLOB()
        if not crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)):
            return ""
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-8")
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return ""


def load_sent_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_sent_history(items: list[dict]):
    HISTORY_FILE.write_text(
        json.dumps(items[:MAX_HISTORY], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_sent_history(entry: dict):
    items = load_sent_history()
    items.insert(0, entry)
    save_sent_history(items)


class FeishuDispatcher:
    def __init__(self, app_id: str, app_secret: str, chat_id: str):
        self.app_id = app_id.strip()
        self.app_secret = unprotect_secret(app_secret.strip())
        self.chat_id = chat_id.strip()
        self._token = None
        self._expiry = 0

    def get_token(self) -> str:
        now = time.time()
        if self._token and self._expiry > now + 60:
            return self._token
        r = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=12,
        )
        res = r.json()
        if r.status_code != 200 or res.get("code") != 0:
            raise RuntimeError(f"Feishu auth failed: {res}")
        self._token = res["tenant_access_token"]
        self._expiry = now + res.get("expire", 7000)
        return self._token

    def upload_image(self, image_path: Path) -> str:
        tok = self.get_token()
        with open(image_path, "rb") as f:
            r = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/images",
                headers={"Authorization": f"Bearer {tok}"},
                data={"image_type": "message"},
                files={"image": (image_path.name, f, "image/png")},
                timeout=30,
            )
        res = r.json()
        if r.status_code != 200 or res.get("code") != 0:
            raise RuntimeError(f"Feishu upload_image failed: {res}")
        return res["data"]["image_key"]

    def upload_file(self, file_path: Path) -> str:
        tok = self.get_token()
        with open(file_path, "rb") as f:
            r = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/files",
                headers={"Authorization": f"Bearer {tok}"},
                data={"file_type": "xls", "file_name": file_path.name},
                files={"file": (file_path.name, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                timeout=30,
            )
        res = r.json()
        if r.status_code != 200 or res.get("code") != 0:
            raise RuntimeError(f"Feishu upload_file failed: {res}")
        return res["data"]["file_key"]

    def send_message(self, msg_type: str, content: dict) -> str:
        tok = self.get_token()
        r = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
            json={
                "receive_id": self.chat_id,
                "msg_type": msg_type,
                "content": json.dumps(content, ensure_ascii=False),
            },
            timeout=15,
        )
        res = r.json()
        if r.status_code != 200 or res.get("code") != 0:
            raise RuntimeError(f"Feishu send_message failed: {res}")
        return res["data"]["message_id"]

    def update_card(self, message_id: str, card: dict) -> tuple[bool, str]:
        """อัปเดตการ์ด Feishu เดิมแบบ Real-Time (PATCH)"""
        tok = self.get_token()
        r = requests.patch(
            f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}",
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
            json={"content": json.dumps(card, ensure_ascii=False)},
            timeout=12,
        )
        res = r.json()
        ok = r.status_code == 200 and res.get("code") == 0
        return ok, str(res.get("msg", res))

    def recall_message(self, message_id: str) -> tuple[bool, str]:
        """ยกเลิก/ลบข้อความใน Feishu ตาม message_id"""
        tok = self.get_token()
        r = requests.delete(
            f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=12,
        )
        res = r.json()
        ok = r.status_code == 200 and res.get("code") == 0
        items = load_sent_history()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for it in items:
            if it.get("message_id") == message_id:
                if ok:
                    it["recalled"] = True
                    it["recalled_at"] = now_str
                it["recall_note"] = res.get("msg", "")
        save_sent_history(items)
        return ok, str(res.get("msg", ""))

    def recall_batch(self, batch_id: str) -> list[tuple[str, bool, str]]:
        results = []
        for it in load_sent_history():
            if it.get("batch_id") == batch_id and not it.get("recalled"):
                mid = it["message_id"]
                ok, msg = self.recall_message(mid)
                results.append((mid, ok, msg))
        return results

    def list_chats(self) -> list[dict]:
        """ดึงรายชื่อกลุ่มแชททั้งหมดที่บอทอยู่ใน Feishu"""
        token = self.get_token()
        chats: list[dict] = []
        page_token = ""
        for _ in range(5):
            params = {"page_size": 50}
            if page_token:
                params["page_token"] = page_token
            r = requests.get(
                "https://open.feishu.cn/open-apis/im/v1/chats",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=20,
            )
            res = r.json()
            if res.get("code") != 0:
                raise RuntimeError(f"Feishu list_chats error: {res}")
            data = res.get("data") or {}
            for item in data.get("items") or []:
                chats.append({
                    "chat_id": item.get("chat_id", ""),
                    "name": item.get("name", "(ไม่มีชื่อห้อง)"),
                    "description": item.get("description", ""),
                    "chat_mode": item.get("chat_mode", ""),
                })
            if not data.get("has_more"):
                break
            page_token = data.get("page_token", "")
            if not page_token:
                break
        return chats

    def get_chat_info(self, chat_id: str) -> dict:
        """ค้นหาและดึงรายละเอียดห้องแชท Feishu จาก Chat ID"""
        cid = str(chat_id or "").strip()
        if not cid:
            raise ValueError("กรุณากรอก Chat ID")
        token = self.get_token()
        r = requests.get(
            f"https://open.feishu.cn/open-apis/im/v1/chats/{cid}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        res = r.json()
        if res.get("code") != 0:
            raise RuntimeError(f"ไม่พบห้องแชทหรือบอทไม่ได้อยู่ในห้อง ({res.get('msg', res)})")
        d = res.get("data") or {}
        return {
            "chat_id": cid,
            "name": d.get("name", "(ไม่มีชื่อห้อง)"),
            "description": d.get("description", ""),
            "user_count": d.get("user_count", ""),
            "bot_count": d.get("bot_count", ""),
            "chat_mode": d.get("chat_mode", ""),
            "chat_type": d.get("chat_type", ""),
        }

    def recall_latest_batch(self, exclude_batch_id: str | None = None) -> list[tuple[str, bool, str]]:
        for it in load_sent_history():
            bid = it.get("batch_id")
            if not it.get("recalled") and bid and bid != exclude_batch_id:
                return self.recall_batch(bid)
        return []

    def send_hourly_report(
        self,
        branch_name: str,
        data: dict,
        summary: dict,
        chart_path: Path | None = None,
        table_image_path: Path | None = None,
        excel_path: Path | None = None,
        mode_label: str = "อัตโนมัติรายชั่วโมง",
        auto_recall_prev: bool = False,
        dashboard_url: str = "",
    ) -> dict:
        w = summary["wcs"]
        d = summary["dws"]

        wcs_lines = "\n".join(
            f"{short_machine_name(format_wcs_name(k))} : **{v['total']:,}**"
            for k, v in w["per_machine"].items()
        ) or "-"
        dws_lines = "\n".join(
            f"{short_machine_name(format_dws_name(k, with_ip=False))} : **{v['total']:,}**"
            for k, v in d["per_machine"].items()
        ) or "-"

        latest_slot = data["slots"][-1] if data["slots"] else "-"
        latest_wcs = w["per_hour"].get(latest_slot, 0)
        latest_dws = d["per_hour"].get(latest_slot, 0)
        grand_total = w["total"] + d["total"]

        elements = []
        if dashboard_url:
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "เปิด Live Dashboard (ดูยอดสด)"},
                        "type": "primary",
                        "url": dashboard_url,
                    }
                ],
            })

        elements.extend([
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
                        "vertical_align": "top",
                        "elements": [
                            {
                                "tag": "div",
                                "text": {
                                    "tag": "lark_md",
                                    "content": (
                                        f"**WCS** ({len(data['wcs_machines'])} เครื่อง)\n"
                                        f"<font color='red'>**{w['total']:,}**</font> ชิ้น\n"
                                        f"<font color='grey'>A {w['shift_a']:,} · B {w['shift_b']:,}</font>"
                                    ),
                                },
                            }
                        ],
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "vertical_align": "top",
                        "elements": [
                            {
                                "tag": "div",
                                "text": {
                                    "tag": "lark_md",
                                    "content": (
                                        f"**DWS** ({len(data['dws_machines'])} เครื่อง)\n"
                                        f"<font color='orange'>**{d['total']:,}**</font> ชิ้น\n"
                                        f"<font color='grey'>A {d['shift_a']:,} · B {d['shift_b']:,}</font>"
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
                        f"**รวมทั้งหมด:** **{grand_total:,}** ชิ้น  |  "
                        f"**ชม. {latest_slot[11:16]}:** WCS {latest_wcs:,} · DWS {latest_dws:,}"
                    ),
                },
            },
            {"tag": "hr"},
            {
                "tag": "collapsible_panel",
                "expanded": False,
                "background_color": "grey",
                "header": {
                    "title": {
                        "tag": "markdown",
                        "content": f"**ดูยอดรายเครื่องทั้งหมด (WCS {len(data['wcs_machines'])} | DWS {len(data['dws_machines'])}) — คลิกเพื่อกางออก ▾**",
                    },
                    "vertical_align": "center",
                    "padding": "6px 0px 6px 8px",
                },
                "border": {"color": "grey", "corner_radius": "5px"},
                "vertical_spacing": "8px",
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
                                "elements": [
                                    {
                                        "tag": "div",
                                        "text": {
                                            "tag": "lark_md",
                                            "content": f"**WCS ({len(data['wcs_machines'])})** *(ชิ้น)*\n{wcs_lines}",
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
                                            "content": f"**DWS ({len(data['dws_machines'])})** *(ชิ้น)*\n{dws_lines}",
                                        },
                                    }
                                ],
                            },
                        ],
                    }
                ],
            },
        ])

        if table_image_path and table_image_path.exists():
            tbl_key = self.upload_image(table_image_path)
            elements.append({"tag": "img", "img_key": tbl_key, "alt": {"tag": "plain_text", "content": "ตารางสรุปยอด WCS และ DWS"}})

        if chart_path and chart_path.exists():
            img_key = self.upload_image(chart_path)
            elements.append({"tag": "img", "img_key": img_key, "alt": {"tag": "plain_text", "content": "กราฟยอดรายชั่วโมง J&T"}})

        title_str = f"J&T {branch_name} — ยอดคัดแยก CoGY"
        subtitle_str = f"{data['start_dt']:%d/%m %H:%M} – {data['end_dt']:%d/%m %H:%M} | {mode_label}"
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "red",
                "title": {"tag": "plain_text", "content": title_str},
                "subtitle": {"tag": "plain_text", "content": subtitle_str},
            },
            "elements": elements,
        }

        batch_id = uuid.uuid4().hex[:12]
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        card_msg_id = self.send_message("interactive", card)
        append_sent_history({
            "batch_id": batch_id,
            "message_id": card_msg_id,
            "chat_id": self.chat_id,
            "msg_type": "card",
            "label": title_str,
            "sent_at": now_str,
            "recalled": False,
        })

        file_msg_id = None
        if excel_path and excel_path.exists():
            file_key = self.upload_file(excel_path)
            file_msg_id = self.send_message("file", {"file_key": file_key})
            append_sent_history({
                "batch_id": batch_id,
                "message_id": file_msg_id,
                "chat_id": self.chat_id,
                "msg_type": "file",
                "label": excel_path.name,
                "sent_at": now_str,
                "recalled": False,
            })

        recalled_prev = []
        if auto_recall_prev:
            recalled_prev = self.recall_latest_batch(exclude_batch_id=batch_id)

        return {
            "batch_id": batch_id,
            "card_message_id": card_msg_id,
            "file_message_id": file_msg_id,
            "recalled_prev": recalled_prev,
        }
