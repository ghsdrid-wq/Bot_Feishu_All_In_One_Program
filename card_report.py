"""card_report.py — รวมรูปรายงาน Excel ให้เป็นการ์ด Feishu ใบเดียว

เดิม Bot Export อัปโหลดรูปแล้วส่งทีละใบ กลุ่มจึงได้รูปเปล่า ๆ 5 ใบติดกัน
ไม่มีตัวเลขสรุปให้อ่านก่อนเปิดรูป และเลื่อนหาย้อนหลังยาก

ที่นี่ประกอบรูปชุดเดียวกันนั้นเป็นการ์ดใบเดียว: ยอดสรุปอ่านได้ทันที
กราฟรายชั่วโมงที่กดดูค่าได้ แล้วตามด้วยรูปตารางเต็มแบบเดิมที่กดขยายได้

ตารางรายจุด x รายชั่วโมงกว้าง 16-24 คอลัมน์ จงใจคงไว้เป็น "รูป" ไม่แปลงเป็น
component table ของ Feishu เพราะการ์ดกว้างคงที่ โชว์ได้ราว 4-5 คอลัมน์
ก่อนต้องเลื่อนแนวนอน — ลองแล้วอ่านไม่ได้จริง

ยอดสรุปกับกราฟมาจาก store.db ของ Dashboard ถ้าอ่านไม่ได้ (ยังไม่ได้ ingest
รอบนี้ หรือรัน Bot Export เดี่ยว ๆ) จะส่งการ์ดที่มีแต่รูป ไม่ใช่ล้มทั้งงาน
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"

# ลิมิตจริงของแพลตฟอร์ม: การ์ด/rich text ส่งได้ไม่เกิน 30KB ต่อข้อความ
CARD_LIMIT_BYTES = 30 * 1024


def _money(n: Any) -> str:
    try:
        return "{:,}".format(int(n))
    except (TypeError, ValueError):
        return "-"


# =====================================================================
# ยอดสรุป + ข้อมูลกราฟ จาก store.db ของ Dashboard
# =====================================================================
def load_summary(business_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """อ่านยอดของรอบงานมาทำหัวการ์ด คืน None ถ้าอ่านไม่ได้

    ตั้งใจกลืน exception ทุกแบบ เพราะงานหลักของ Bot Export คือส่งรูป
    การ์ดสวยขึ้นเป็นของแถม ห้ามทำให้การส่งรูปล้มเพราะ Dashboard มีปัญหา
    """
    try:
        import sqlite3

        from dashboard import pipeline as dash_pipeline
        from dashboard import view
        from metrics import core

        business_date = business_date or dash_pipeline.current_business_date()
        conn = sqlite3.connect(core.DEFAULT_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            hero = view.build_page(conn, business_date)["hero"]
        finally:
            conn.close()
    except Exception:
        return None

    main = hero.get("hero") or {}
    if not main.get("has_data"):
        return None
    return {"business_date": business_date, "hero": hero, "main": main}


def _chart_rows(hero: Dict[str, Any], main: Dict[str, Any]) -> List[Dict[str, Any]]:
    """ตาราง long-format ที่ VChart กิน: หนึ่งแถว = หนึ่งชั่วโมง x หนึ่งชนิด"""
    all_bars = hero["stacked"]["bars"]
    live = [b for b in all_bars if b["total"] > 0]
    rows: List[Dict[str, Any]] = []
    for src in [main] + hero.get("others", []):
        if src.get("unit") != main.get("unit"):
            continue                      # กระสอบคนละหน่วย ไม่เอามาปนในกราฟชิ้น
        per_hour = src.get("per_hour") or []
        for bar in live:
            idx = all_bars.index(bar)
            rows.append({
                "hour": bar["label"],
                "type": src["title"],
                "value": per_hour[idx] if idx < len(per_hour) else 0,
            })
    return rows


def _chart(spec: Dict[str, Any]) -> Dict[str, Any]:
    return {"tag": "chart", "aspect_ratio": "16:9", "color_theme": "brand",
            "chart_spec": spec, "preview": True, "margin": "4px 0"}


def _summary_elements(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    hero, main = summary["hero"], summary["main"]
    out: List[Dict[str, Any]] = []

    head = "**ยอดปล่อยรวม {}**\n<font color='blue'>**{}**</font> {}".format(
        main["title"], _money(main["total"]), main.get("unit", ""))
    if main.get("day_target"):
        head += "  ·  **{}%** ของเป้า {}".format(
            main.get("achieved_pct", 0), _money(main["day_target"]))
    head += "\nกะ A {} · กะ B {}".format(
        _money(main.get("shift_a", 0)), _money(main.get("shift_b", 0)))
    out.append({"tag": "markdown", "content": head})

    others = hero.get("others") or []
    if others:
        out.append({"tag": "markdown", "content": " · ".join(
            "**{}** {}".format(s["title"], _money(s["total"])) for s in others)})

    out.append({"tag": "markdown", "content":
                "**ต้องดู** — พัสดุตก error {} ({}%) · จุดต่ำกว่าเกณฑ์ {} · เครื่องไม่ส่งข้อมูล {}".format(
                    _money(hero.get("error_qty", 0)), hero.get("error_rate_pct", 0),
                    hero.get("below_target", 0), hero.get("offline", 0))})

    rows = _chart_rows(hero, main)
    if rows:
        axes = [{"orient": "bottom", "label": {"autoRotate": True}}, {"orient": "left"}]
        legend = {"visible": True, "orient": "bottom"}
        unit = main.get("unit", "")
        out.append({"tag": "hr"})
        out.append(_chart({
            "type": "line", "title": {"text": "แยกตามชนิด ({}/ชั่วโมง)".format(unit)},
            "data": {"values": rows}, "xField": "hour", "yField": "value",
            "seriesField": "type", "legends": legend, "axes": axes}))
        out.append(_chart({
            "type": "bar", "title": {"text": "รวมทุกชนิด ({}/ชั่วโมง)".format(unit)},
            "data": {"values": rows}, "xField": "hour", "yField": "value",
            "seriesField": "type", "stack": True, "legends": legend, "axes": axes}))
    return out


# =====================================================================
# ประกอบการ์ด
# =====================================================================
def build_card(images: Sequence[Tuple[str, str]],
               summary: Optional[Dict[str, Any]] = None,
               title: str = "รายงานยอดปล่อย KKN") -> Dict[str, Any]:
    """images = ลำดับของ (คำบรรยาย, img_key) ที่อัปโหลดไว้แล้ว"""
    elements: List[Dict[str, Any]] = []
    if summary:
        elements.extend(_summary_elements(summary))

    if images:
        if elements:                      # ไม่มีสรุปก็ไม่ต้องมีเส้นคั่นลอยบรรทัดแรก
            elements.append({"tag": "hr"})
        elements.append({"tag": "markdown",
                         "content": "**ตารางเต็ม** — กดที่รูปเพื่อดูเต็มความละเอียด"})
        for caption, key in images:
            elements.append({
                "tag": "img", "img_key": key,
                "title": {"tag": "plain_text", "content": caption},
                "alt": {"tag": "plain_text", "content": caption},
                # fit_horizontal = กว้างเต็มการ์ด ไม่ครอป / preview = กดแล้วขยาย
                "scale_type": "fit_horizontal",
                "preview": True,
                "corner_radius": "4px",
                "margin": "6px 0",
            })

    subtitle = "กราฟกดดูได้ + ตารางเต็มแบบเดิม" if summary else "ตารางเต็มแบบเดิม"
    header_title = title
    if summary:
        header_title = "{} — {}".format(title, summary["business_date"])

    return {
        "schema": "2.0",
        # update_multi ต้องเป็น true ถึงจะอัปเดตการ์ดใบเดิมทีหลังได้
        "config": {"update_multi": True, "wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": header_title},
            "subtitle": {"tag": "plain_text", "content": subtitle},
        },
        "body": {"elements": elements},
    }


def card_size(card: Dict[str, Any]) -> int:
    return len(json.dumps(card, ensure_ascii=False).encode("utf-8"))


def send_card(token: str, chat_id: str, card: Dict[str, Any]) -> Dict[str, Any]:
    content = json.dumps(card, ensure_ascii=False)
    size = len(content.encode("utf-8"))
    if size > CARD_LIMIT_BYTES:
        raise Exception(
            "การ์ดใหญ่ {:,} bytes เกินลิมิต {:,} bytes".format(size, CARD_LIMIT_BYTES))

    response = requests.post(
        MESSAGE_URL,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        json={"receive_id": chat_id, "msg_type": "interactive", "content": content},
        timeout=30,
    )
    response.raise_for_status()
    res = response.json()
    if res.get("code") != 0:
        raise Exception("Send card failed: {}".format(res))
    return res
