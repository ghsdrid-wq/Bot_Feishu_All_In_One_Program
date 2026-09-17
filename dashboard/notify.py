"""notify.py — ส่งรูป dashboard เข้ากลุ่ม Feishu

ใช้ช่องทางเดียวกับที่บอทส่งยอดอยู่ทุกวัน (Botmessage.upload_image / send_image_chat)
จะได้ไม่ต้องดูแล token คนละชุด และถ้า app_secret เปลี่ยนก็แก้ที่เดียว

ส่งเฉพาะแท็บที่ตั้งไว้ใน [DASHBOARD] send_tabs — ดีฟอลต์แค่ภาพรวมใบเดียว
เพราะยิง 7 ใบรวดเข้ากลุ่มทุกรอบคือการสแปมคนทั้งกลุ่ม
"""

from __future__ import annotations

import configparser
import os
import sys
from typing import Callable, Optional

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

import Botmessage as feishu_api      # ช่องทางส่งเดียวกับที่บอทใช้ทุกวัน
from metrics import core

LogFunc = Callable[..., None]


def _log(message: str, level: str = "INFO") -> None:
    print(f"[{level}] {message}")


def settings() -> dict:
    parser = configparser.RawConfigParser()
    parser.read(os.path.join(core.PROJECT_ROOT, "config.ini"), encoding="utf-8")
    section = parser["DASHBOARD"] if "DASHBOARD" in parser else {}
    feishu = parser["FEISHU"] if "FEISHU" in parser else {}

    def flag(key: str, default: bool) -> bool:
        return str(section.get(key, str(default))).strip().lower() in ("1", "true", "yes", "on")

    tabs = [t.strip() for t in section.get("send_tabs", "overview").split(",") if t.strip()]
    return {
        "send_enabled": flag("send_enabled", False),
        "send_tabs": tabs,
        "send_link": flag("send_link", True),
        "chat_id": (section.get("chat_id") or feishu.get("chat_id", "")).strip(),
        "public_url": (section.get("public_url") or "").strip(),
        "token": (section.get("token") or "").strip(),
    }


def chat_name(access_token: str, chat_id: str) -> str:
    """ถามชื่อกลุ่มจาก Feishu — ใช้ยืนยันปลายทางก่อนยิงจริง
    ไม่อยากให้ใครเผลอส่งเข้ากลุ่มผิดเพราะจำ chat_id ไม่ได้"""
    try:
        response = requests.get(
            f"https://open.feishu.cn/open-apis/im/v1/chats/{chat_id}",
            headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
        data = response.json()
        return (data.get("data") or {}).get("name") or "(ไม่มีชื่อ)"
    except Exception as exc:
        return f"(ถามชื่อกลุ่มไม่ได้: {exc})"


def send_text(access_token: str, chat_id: str, text: str) -> None:
    response = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        headers={"Authorization": f"Bearer {access_token}",
                 "Content-Type": "application/json"},
        json={"receive_id": chat_id, "msg_type": "text",
              "content": __import__("json").dumps({"text": text}, ensure_ascii=False)},
        timeout=15)
    result = response.json()
    if result.get("code") != 0:
        raise Exception(f"ส่งข้อความไม่สำเร็จ: {result}")


def build_caption(business_date: str, db_path: str, cfg: dict) -> str:
    """ข้อความนำหน้ารูป — ตัวเลขสำคัญเป็น "ข้อความจริง" ที่ copy/ค้นหาได้
    ต่างจากรูปที่อ่านได้อย่างเดียว"""
    from metrics import aggregate

    conn = core.read_connect(db_path)
    try:
        overview = aggregate.build_overview(conn, business_date)
    finally:
        conn.close()

    lines = [f"สรุปยอดคลัง KKN — {business_date} (รอบ 12:00–12:00)"]
    for src in overview["sources"]:
        lines.append(f"  {src['title']}: {src['total']:,} {src['unit']}"
                     f"  (กะ A {src['shift_a']:,} · กะ B {src['shift_b']:,})")
    if cfg["send_link"] and cfg["public_url"] and cfg["token"]:
        lines.append("")
        lines.append(f"ดูรายละเอียดทุกแท็บ: {cfg['public_url']}/?t={cfg['token']}")
    return "\n".join(lines)


def send_dashboard(business_date: str, png_dir: str,
                   db_path: str = core.DEFAULT_DB_PATH,
                   log: Optional[LogFunc] = None,
                   dry_run: bool = False) -> dict:
    write = log or _log
    cfg = settings()

    if not cfg["send_enabled"] and not dry_run:
        return {"skipped": "ปิดไว้ที่ config.ini [DASHBOARD] send_enabled"}
    if not cfg["chat_id"]:
        return {"error": "ไม่มี chat_id"}

    app = feishu_api.get_feishu()
    access_token = feishu_api.get_token(app["APP_ID"], app["APP_SECRET"])

    files = []
    for tab in cfg["send_tabs"]:
        path = os.path.join(png_dir, f"{business_date}_{tab}.png")
        if os.path.isfile(path):
            files.append((tab, path))
        else:
            write(f"ไม่พบรูปของแท็บ {tab}: {path}", level="WARN")

    caption = build_caption(business_date, db_path, cfg)
    target = chat_name(access_token, cfg["chat_id"])

    plan = {
        "chat_id": cfg["chat_id"],
        "chat_name": target,
        "files": [os.path.basename(p) for _, p in files],
        "caption": caption,
    }
    if dry_run:
        plan["dry_run"] = True
        return plan

    send_text(access_token, cfg["chat_id"], caption)
    write(f"ส่งข้อความสรุปเข้ากลุ่ม {target} แล้ว")
    for tab, path in files:
        key = feishu_api.upload_image(access_token, path, log=lambda m: write(m, level="INFO"))
        feishu_api.send_image_chat(access_token, cfg["chat_id"], key)
        size_kb = os.path.getsize(path) / 1024
        write(f"ส่งรูปแท็บ {tab} แล้ว ({size_kb:,.0f} KB)")

    plan["sent"] = len(files) + 1
    return plan


def main() -> int:
    import argparse
    import json

    core.use_utf8_console()
    parser = argparse.ArgumentParser(description="ส่ง dashboard เข้ากลุ่ม Feishu")
    parser.add_argument("--date", required=True)
    parser.add_argument("--png-dir", default=os.path.join(core.PROJECT_ROOT, "out"))
    parser.add_argument("--db", default=core.DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true",
                        help="แสดงว่าจะส่งอะไรเข้ากลุ่มไหน โดยยังไม่ส่งจริง")
    args = parser.parse_args()

    result = send_dashboard(args.date, args.png_dir, args.db, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
