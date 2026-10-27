# -*- coding: utf-8 -*-
"""sent_messages.py — จำข้อความที่บอทส่งเข้ากลุ่มเป็นรอบ ๆ ไว้ถอน (recall) ทีหลัง

บางรอบส่งรายงานเพี้ยน (รูปเก่า ยอดผิด) ต้องถอนทั้งชุด: การ์ด รูป และไฟล์ Excel
ของรอบเดียวกัน feishu_client.capture_sends() เก็บ message_id ของทุกข้อความที่ส่ง
ระหว่างรอบ แล้วไฟล์นี้บันทึกเป็น 1 รอบลง sent_messages.json ข้าง config.ini

Feishu ให้บอทถอนได้เฉพาะข้อความของตัวเอง และภายในเวลาที่แอดมินองค์กรตั้งไว้
เกินเวลานั้น Feishu จะตอบ error กลับมา ซึ่งจะแสดงให้ผู้ใช้เห็นตามจริง
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import feishu_client

FILE_NAME = "sent_messages.json"
# เก็บรอบล่าสุดไว้เท่านี้ — เก่ากว่านี้ Feishu ก็ไม่ให้ถอนแล้วอยู่ดี
MAX_ROUNDS = 100

LogFunc = Callable[..., None]
_lock = threading.Lock()


def load_rounds(path: str) -> List[Dict[str, Any]]:
    """รอบที่บันทึกไว้ ใหม่สุดก่อน"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    rounds = [r for r in data if isinstance(r, dict) and r.get("messages")] if isinstance(data, list) else []
    return list(reversed(rounds))


def _save(path: str, rounds_oldest_first: List[Dict[str, Any]]) -> None:
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    handle, temp = tempfile.mkstemp(suffix=".json", dir=folder)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(rounds_oldest_first[-MAX_ROUNDS:], out, ensure_ascii=False, indent=1)
        os.replace(temp, path)
    except Exception:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise


def record_round(path: str, kind: str, label: str, messages: List[Dict[str, str]]) -> Optional[str]:
    """บันทึกข้อความของรอบหนึ่ง คืน id ของรอบ (ไม่มีข้อความ = ไม่บันทึก)"""
    if not messages:
        return None
    entry = {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,
        "label": label,
        "sent_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "messages": list(messages),
        "recalled": [],
    }
    with _lock:
        rounds = list(reversed(load_rounds(path)))
        rounds.append(entry)
        _save(path, rounds)
    return entry["id"]


@contextmanager
def recording(path: str, kind: str, label: str, log: Optional[LogFunc] = None) -> Iterator[list]:
    """จำทุกข้อความที่ส่งใน with นี้เป็น 1 รอบ — ส่งไม่ครบ (error/หยุดกลางทาง) ก็บันทึกเท่าที่ส่งไปแล้ว"""
    with feishu_client.capture_sends() as messages:
        try:
            yield messages
        finally:
            try:
                record_round(path, kind, label, messages)
            except Exception as exc:
                if log:
                    log(f"บันทึกข้อความที่ส่งไว้ถอนไม่ได้ — {exc}", level="WARN")


def recall_round(path: str, round_id: str, token: str,
                 log: Optional[LogFunc] = None) -> Tuple[int, List[str]]:
    """ถอนทุกข้อความของรอบที่ยังไม่ได้ถอน คืน (จำนวนที่ถอนสำเร็จ, รายการ error)"""
    with _lock:
        rounds = list(reversed(load_rounds(path)))
    entry = next((r for r in rounds if r.get("id") == round_id), None)
    if entry is None:
        return 0, [f"ไม่พบรอบ {round_id}"]
    recalled = set(entry.get("recalled") or [])
    done, errors = 0, []
    for message in entry["messages"]:
        message_id = message.get("message_id")
        if not message_id or message_id in recalled:
            continue
        try:
            feishu_client.recall_message(token, message_id, log=log)
            recalled.add(message_id)
            done += 1
        except Exception as exc:
            errors.append(f"{message.get('msg_type', '?')} {message_id}: {exc}")
    with _lock:
        rounds = list(reversed(load_rounds(path)))
        for item in rounds:
            if item.get("id") == round_id:
                item["recalled"] = sorted(recalled)
        _save(path, rounds)
    return done, errors


def round_status(entry: Dict[str, Any]) -> str:
    total = len(entry.get("messages") or [])
    gone = len(entry.get("recalled") or [])
    if gone >= total:
        return "ถอนแล้ว"
    return f"ถอนแล้ว {gone}/{total}" if gone else f"{total} ข้อความ"
