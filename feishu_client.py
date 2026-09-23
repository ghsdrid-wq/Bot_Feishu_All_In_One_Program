# -*- coding: utf-8 -*-
"""feishu_client.py — ชั้นเดียวที่คุยกับ Feishu จริง ๆ

ก่อนหน้านี้โค้ดยิง API ของ Feishu กระจายอยู่ 4 ไฟล์ และทำงานไม่เหมือนกัน
บางเส้น retry สามครั้ง บางเส้นพังแล้วเงียบ timeout ตั้งแต่ 10 ถึง 30 วินาที
บางเส้นต่อ JSON ด้วย f-string ทั้งที่อีกเส้นใช้ json.dumps ทั้งที่เป็น API
ตัวเดียวกัน เวลาเปลี่ยน app_secret หรือย้าย tenant ต้องไล่แก้ทีละจุด

รวมมาไว้ที่นี่ที่เดียว ทุกเส้นจึงได้ retry เท่ากัน หัวข้อความเหมือนกัน และ
อ่าน error แบบเดียวกัน ส่วนการอ่าน config ยังเป็นของผู้เรียกแต่ละที่เหมือนเดิม
เพราะแต่ละที่มีเหตุผลต่างกันจริง (เช่นตอนส่งไฟล์ต้องใช้ค่าที่ snapshot ไว้ตอน
เริ่มรอบ ไม่ใช่ค่าล่าสุดในช่อง UI ที่ผู้ใช้อาจเพิ่งแก้กลางรอบ)

ชื่อไฟล์ไม่ใช้ feishu.py เพราะในโค้ดมีตัวแปรชื่อ feishu อยู่หลายที่
(feishu = self.config["FEISHU"]) ซึ่งจะบังโมดูลจนเรียกไม่ถึง
"""

from __future__ import annotations

import json
import mimetypes
import os
import time
from typing import Any, Callable, Dict, Optional

import requests

BASE_URL = "https://open.feishu.cn/open-apis"
TOKEN_URL = f"{BASE_URL}/auth/v3/tenant_access_token/internal/"
MESSAGE_URL = f"{BASE_URL}/im/v1/messages?receive_id_type=chat_id"
IMAGE_URL = f"{BASE_URL}/im/v1/images"
FILE_URL = f"{BASE_URL}/im/v1/files"

# แยกตามงาน ไม่ใช่ตั้งมั่ว ๆ ทีละจุดแบบเดิม (10/15/30 ปนกันโดยไม่มีเหตุผล)
TIMEOUT_TOKEN = 15
TIMEOUT_MESSAGE = 30
TIMEOUT_UPLOAD = 60

LogFunc = Callable[..., None]


def _say(log: Optional[LogFunc], message: str) -> None:
    if log is None:
        return
    try:
        log(message)
    except Exception:
        pass


def request_with_retry(
    func: Callable[[], Dict[str, Any]],
    retries: int = 3,
    delay: int = 2,
    log: Optional[LogFunc] = None,
    name: str = "",
) -> Dict[str, Any]:
    """เรียกซ้ำเมื่อเน็ตสะดุด หน่วงเพิ่มขึ้นทีละรอบ

    รอบสุดท้ายที่ยังพังจะโยน error ออกไปตามเดิม ผู้เรียกที่ไม่อยากให้พังทั้ง
    ฟังก์ชันต้องดักเอง
    """
    retries = max(1, retries)
    for attempt in range(retries):
        try:
            return func()
        except Exception as exc:
            _say(log, f"{name} failed ({attempt + 1}/{retries}): {exc}")
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
            else:
                raise
    raise RuntimeError(f"{name or 'request'} failed without returning a response")


def response_json(response: requests.Response, name: str) -> Dict[str, Any]:
    """อ่าน JSON จาก response พร้อมบอกให้ชัดว่าใครพัง

    Feishu ตอบ HTML หน้า error มาบ้างตอนระบบมีปัญหา ถ้าปล่อยให้ .json() พังเอง
    ข้อความที่ได้จะไม่บอกว่าเป็นคำสั่งไหน
    """
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        body = (response.text or "")[:200]
        raise Exception(f"{name}: ตอบกลับไม่ใช่ JSON (HTTP {response.status_code}) {body}") from exc


def _post_json(url: str, token: str, payload: Dict[str, Any], name: str,
               timeout: int = TIMEOUT_MESSAGE, retries: int = 3,
               log: Optional[LogFunc] = None) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    def do_request() -> Dict[str, Any]:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        return response_json(response, name)

    res = request_with_retry(do_request, retries=retries, log=log, name=name)
    if res.get("code") != 0:
        raise Exception(f"{name} failed: {res}")
    return res


# ---------------------------------------------------------------- token

def get_token(app_id: str, app_secret: str, log: Optional[LogFunc] = None,
              retries: int = 3) -> str:
    """ขอ tenant access token — ไม่ได้ก็โยน error"""
    if not app_id or not app_secret:
        raise Exception("Missing APP_ID / APP_SECRET")

    def do_request() -> Dict[str, Any]:
        response = requests.post(
            TOKEN_URL,
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=TIMEOUT_TOKEN,
        )
        return response_json(response, "get_token")

    res = request_with_retry(do_request, retries=retries, log=log, name="get_token")
    if "tenant_access_token" not in res:
        raise Exception(f"Get token failed: {res}")
    _say(log, "TOKEN OK")
    return res["tenant_access_token"]


# ---------------------------------------------------------------- upload

def upload_image(token: str, path: str, log: Optional[LogFunc] = None) -> str:
    """อัปโหลดรูปเข้า Feishu คืน image_key"""
    headers = {"Authorization": f"Bearer {token}"}

    def do_request() -> Dict[str, Any]:
        with open(path, "rb") as fh:
            response = requests.post(
                IMAGE_URL, headers=headers,
                files={"image": fh}, data={"image_type": "message"},
                timeout=TIMEOUT_UPLOAD)
            return response_json(response, "upload_image")

    res = request_with_retry(do_request, log=log, name="upload_image")
    if res.get("code") != 0 or not res.get("data", {}).get("image_key"):
        raise Exception(f"Upload image failed: {res}")
    return res["data"]["image_key"]


def upload_file(token: str, path: str, log: Optional[LogFunc] = None) -> str:
    """อัปโหลดไฟล์แนบเข้า Feishu คืน file_key"""
    filename = os.path.basename(path)
    mime = (mimetypes.guess_type(filename)[0]
            or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    headers = {"Authorization": f"Bearer {token}"}

    def do_request() -> Dict[str, Any]:
        with open(path, "rb") as fh:
            response = requests.post(
                FILE_URL, headers=headers,
                files={"file": (filename, fh, mime)},
                data={"file_type": "xls", "file_name": filename},
                timeout=TIMEOUT_UPLOAD)
            return response_json(response, "upload_file")

    res = request_with_retry(do_request, log=log, name="upload_file")
    if res.get("code") != 0 or not res.get("data", {}).get("file_key"):
        raise Exception(f"Upload file failed: {res}")
    return res["data"]["file_key"]


# ---------------------------------------------------------------- ส่งข้อความ

def send_text(token: str, chat_id: str, text: str,
              log: Optional[LogFunc] = None, retries: int = 3) -> Dict[str, Any]:
    return _post_json(
        MESSAGE_URL, token,
        {"receive_id": chat_id, "msg_type": "text",
         "content": json.dumps({"text": text}, ensure_ascii=False)},
        "send_text", retries=retries, log=log)


def send_image(token: str, chat_id: str, image_key: str,
               log: Optional[LogFunc] = None) -> Dict[str, Any]:
    return _post_json(
        MESSAGE_URL, token,
        {"receive_id": chat_id, "msg_type": "image",
         "content": json.dumps({"image_key": image_key}, ensure_ascii=False)},
        "send_image", log=log)


def send_file(token: str, chat_id: str, file_key: str,
              log: Optional[LogFunc] = None) -> Dict[str, Any]:
    return _post_json(
        MESSAGE_URL, token,
        {"receive_id": chat_id, "msg_type": "file",
         "content": json.dumps({"file_key": file_key}, ensure_ascii=False)},
        "send_file", log=log)


def send_card(token: str, chat_id: str, card_content: str,
              log: Optional[LogFunc] = None) -> Dict[str, Any]:
    """ส่งการ์ด โดย card_content เป็น JSON ที่แปลงเป็นสตริงมาแล้ว

    ผู้เรียกเป็นคนแปลงเอง เพราะต้องวัดขนาดเทียบลิมิต 30KB ก่อนส่งอยู่แล้ว
    """
    return _post_json(
        MESSAGE_URL, token,
        {"receive_id": chat_id, "msg_type": "interactive", "content": card_content},
        "send_card", log=log)


def chat_name(token: str, chat_id: str) -> str:
    """ชื่อกลุ่มของ chat_id — ใช้ยืนยันปลายทางก่อนยิงจริง

    ถามไม่ได้ก็ไม่ควรทำให้ทั้งรอบล้ม จึงคืนข้อความบอกเหตุแทนการโยน error
    """
    try:
        response = requests.get(
            f"{BASE_URL}/im/v1/chats/{chat_id}",
            headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT_TOKEN)
        data = response_json(response, "chat_name")
        return (data.get("data") or {}).get("name") or "(ไม่มีชื่อ)"
    except Exception as exc:
        return f"(ถามชื่อกลุ่มไม่ได้: {exc})"


def reply_text(token: str, message_id: str, text: str,
               log: Optional[LogFunc] = None, retries: int = 2) -> Dict[str, Any]:
    """ตอบกลับข้อความที่ถูกทักมา

    retry น้อยกว่าเส้นอื่นเพราะอยู่ในเส้นทาง webhook ที่ Feishu รออยู่
    ลองซ้ำนานเกินจะกลายเป็นฝั่งนั้น timeout แทน
    """
    return _post_json(
        f"{BASE_URL}/im/v1/messages/{message_id}/reply", token,
        {"msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
        "reply_text", retries=retries, log=log)
