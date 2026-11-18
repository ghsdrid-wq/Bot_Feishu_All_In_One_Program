"""ส่วนเสริมของ Engine สำหรับหน้าเว็บ: ผลล่าสุดของ VIP AWB (แปลงเป็น JSON)

VIP AWB สั่งงานได้ 2 ทาง: ขั้นตอน VIP AWB ของรอบรายงาน (Engine.start_vip_awb_from_pipeline)
และปุ่มบนหน้าเว็บ (Engine.run_vip_awb_in_background) — ทั้งสองทางเรียก vip_awb.run
ไฟล์นี้ห่อ vip_awb.run ไว้เก็บผลล่าสุดให้หน้าเว็บแสดง ไม่ว่าจะสั่งมาจากทางไหน
"""
from __future__ import annotations

import os
import threading
from datetime import datetime

import vip_awb
from vip_awb.core import hub_status

_last: dict | None = None
_lock = threading.Lock()


def report_to_dict(d, png: str | None, xlsx: str | None) -> dict:
    groups = (hub_status.SENT, hub_status.STUCK, hub_status.MISSED)
    return {
        "title": d.title,
        "period": d.period_label,
        "until": d.until.strftime("%Y-%m-%d %H:%M"),
        "closing": d.closing,
        "status_at": d.status_checked_at.strftime("%Y-%m-%d %H:%M") if d.status_checked_at else None,
        "hours": [h.strftime("%H:%M") for h in d.hours],
        "brands": [{
            "name": b, "total": d.total(b), "last_hour": d.last_hour(b), "hourly": d.grid[b],
            "sent": d.group_count(b, groups[0]), "stuck": d.group_count(b, groups[1]),
            "missed": d.group_count(b, groups[2]),
        } for b in d.brands],
        "totals": {"in": d.grand_total, "sent": d.group_total(groups[0]), "stuck": d.group_total(groups[1]),
                   "missed": d.group_total(groups[2])},
        "pending": d.pending,
        "stuck_list": [{
            "awb": a["awb"], "brand": a["brand"], "first_scan": a["first_scan"],
            "hours": a.get("stuck_hours"), "status": a.get("status", ""), "last": a.get("last_type", ""),
        } for a in d.awbs if a.get("group") in (hub_status.STUCK, hub_status.MISSED)],
        "image": os.path.basename(png) if png else None,
        "excel": os.path.basename(xlsx) if xlsx else None,
    }


def install_vip_capture() -> None:
    """ห่อ vip_awb.run ครั้งเดียว — ทั้ง vip_awb.run และ bot_main.vip_awb.run เป็นออบเจกต์เดียวกัน"""
    if getattr(vip_awb.run, "_webui_capture", False):
        return
    original = vip_awb.run

    def run(*args, **kwargs):
        res = original(*args, **kwargs)
        global _last
        with _lock:
            _last = report_to_dict(res["data"], res["png"], res["xlsx"])
            _last.update(sent=res["sent"], skipped=res["skipped"], finished=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return res

    run._webui_capture = True
    vip_awb.run = run


def last_vip() -> dict:
    with _lock:
        return dict(_last) if _last else {}
