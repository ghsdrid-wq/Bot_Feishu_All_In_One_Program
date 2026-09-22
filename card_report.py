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
from datetime import datetime
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


def refresh_autopacking(log: Optional[Any] = None) -> None:
    """อ่านไฟล์ดิบ AutoPacking ซ้ำก่อนสร้างการ์ด

    AutoPacking ไม่ได้ดึงสดเหมือน DWS/PDA แต่อ่านจากไฟล์รายชั่วโมงที่โปรแกรม
    อีกตัวเขียนให้ และไฟล์ของชั่วโมงที่เพิ่งจบถูกเขียน "ตอนหัวชั่วโมงพอดี"
    (04.xlsx = ข้อมูลของ 03:00-04:00 เขียนตอน 04:00)

    รอบรันเริ่มก่อนหัวชั่วโมงเมื่อไหร่ ไฟล์นั้นยังไม่เกิด ยอดชั่วโมงล่าสุด
    จะเป็น 0 ทั้งที่ข้อมูลมีจริง — หัวการ์ดบอกว่านับถึงตอนนี้ แต่เนื้อในไม่ถึง

    อ่านซ้ำตรงนี้อีกครั้งจึงปิดช่องว่างนั้น ต้นทุนแค่ 0.3 วินาที เพราะเป็น
    ไฟล์เล็ก ๆ ในเครื่อง/แชร์ ไม่ได้ยิงเน็ตเวิร์กออกไปไหน
    """
    write = log or (lambda _m: None)
    try:
        from metrics import core, ingest_autopacking

        conn = core.connect(core.DEFAULT_DB_PATH)
        try:
            result = ingest_autopacking.ingest_folder(conn)
        finally:
            conn.close()
        if result.get("rows"):
            write("Re-read AutoPacking before card: {} rows from {} files".format(
                result["rows"], result.get("files", 0)))
    except Exception as exc:
        # อ่านไม่ได้ก็ใช้ของเดิมใน DB ต่อ ไม่ควรล้มการส่ง
        write("Re-read AutoPacking skipped: {}".format(exc))


def ensure_summary(business_date: Optional[str] = None,
                   log: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """อ่านยอด ถ้ารอบนี้ยังไม่มีข้อมูลให้เก็บข้อมูลเองก่อนแล้วอ่านซ้ำ

    ใช้ตอนรัน Bot Export โดยไม่ติ๊กขั้นตอน Dashboard — ไม่มีใคร ingest ข้อมูล
    ของรอบนั้น การ์ดจึงเคยออกมามีแต่รูป ไม่มีตัวเลข

    เก็บข้อมูลอย่างเดียว ไม่ทำรูป (render=False) เพราะรูปที่การ์ดนี้ใช้มาจาก
    Excel อยู่แล้ว ไม่ต้องเปิด Chromium มาทำรูปซ้ำ

    ถ้าขั้นตอน Dashboard ทำงานไปแล้วในรอบเดียวกัน load_summary จะได้ข้อมูล
    ตั้งแต่ครั้งแรก ฟังก์ชันนี้จึงไม่ไป ingest ซ้ำให้เสียเวลา
    """
    write = log or (lambda _m: None)
    # อ่านไฟล์ AutoPacking ซ้ำก่อนเสมอ ถูกมากและปิดช่องว่างชั่วโมงล่าสุด
    refresh_autopacking(write)
    summary = load_summary(business_date)
    if summary is not None:
        return summary

    try:
        from dashboard import pipeline as dash_pipeline
        write("No summary for this cycle - ingesting before building card")
        # ต่อ log เข้ากับของผู้เรียก ไม่งั้น pipeline จะ print ลง stdout
        # ซึ่งหายไปเฉย ๆ เพราะโปรแกรมหลักเป็น GUI ไม่มี console
        result = dash_pipeline.run_cycle(
            log=lambda message, level="INFO": write(message),
            business_date=business_date, render=False)
        if result.get("skipped"):
            write("Ingest skipped: {}".format(result["skipped"]))
            return None
        write("Ingested {} rows".format(result.get("rows", 0)))
    except Exception as exc:
        # เก็บข้อมูลไม่ได้ก็ยังส่งรูปได้ตามเดิม ห้ามล้มงานหลัก
        write("Ingest failed, card will have images only: {}".format(exc))
        return None

    return load_summary(business_date)


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
            # ส่งค่า 0 เข้าไปด้วย ไม่กรองทิ้ง — ถ้ากรอง ชนิดที่ไม่มียอดเลย
            # ทั้งรอบจะหายไปจากทั้งกราฟและคำอธิบายสี คนดูจะไม่รู้ว่ามีชนิดนั้นอยู่
            rows.append({
                "hour": bar["label"],
                "type": src["title"],
                "value": per_hour[idx] if idx < len(per_hour) else 0,
            })
    return rows


# ความสูงกราฟในการ์ด — ใช้ px ตายตัวแทน aspect_ratio เพราะ 16:9 บนการ์ด
# กว้าง ~600px จะสูงราว 340px ต่อกราฟ กินพื้นที่แชทเกินความจำเป็น
CHART_HEIGHT = "280px"


def _chart(spec: Dict[str, Any]) -> Dict[str, Any]:
    return {"tag": "chart", "height": CHART_HEIGHT, "color_theme": "brand",
            "chart_spec": spec, "preview": True, "margin": "4px 0"}


def _summary_elements(summary: Dict[str, Any], link: str = "") -> List[Dict[str, Any]]:
    hero, main = summary["hero"], summary["main"]
    out: List[Dict[str, Any]] = []

    # ยอดรวมกับยอดแยกกะอยู่บรรทัดเดียวกัน เป็นตัวเลขชุดเดียวที่อ่านต่อกัน
    # ไม่ใส่ % ของเป้า เพราะเป้าคิดจากชั่วโมงที่เดินจริง ซึ่งขยับทุกชั่วโมง
    # เห็นตัวเลขเปอร์เซ็นต์เด้งไปมาในแชทแล้วเข้าใจผิดว่ายอดตก
    head = "**ยอดปล่อยรวม {}**\n<font color='blue'>**{}**</font> {} · กะ A {} · กะ B {}".format(
        main["title"], _money(main["total"]), main.get("unit", ""),
        _money(main.get("shift_a", 0)), _money(main.get("shift_b", 0)))
    out.append({"tag": "markdown", "content": head})

    others = hero.get("others") or []
    if others:
        out.append({"tag": "markdown", "content": " · ".join(
            "**{}** {}".format(s["title"], _money(s["total"])) for s in others)})

    out.append({"tag": "markdown", "content":
                "พัสดุตก error {} ({}%) · จุดต่ำกว่าเกณฑ์ {} · เครื่องไม่ส่งข้อมูล {}".format(
                    _money(hero.get("error_qty", 0)), hero.get("error_rate_pct", 0),
                    hero.get("below_target", 0), hero.get("offline", 0))})

    # ปุ่มลิงก์อยู่ใต้ตัวเลขสรุปทั้งหมด ก่อนถึงกราฟ — ยังอยู่ในครึ่งบนที่เห็น
    # ก่อนเลื่อน แต่ไม่ไปคั่นกลางบล็อกตัวเลขให้อ่านสะดุด
    if link:
        out.append(_link_button(link))

    rows = _chart_rows(hero, main)
    if rows:
        axes = [{"orient": "bottom", "label": {"autoRotate": True}}, {"orient": "left"}]
        legend = {"visible": True, "orient": "bottom"}
        unit = main.get("unit", "")
        out.append({"tag": "hr"})
        # กราฟเดียวพอ — แท่งซ้อนบอกทั้งยอดรวมของชั่วโมง (ความสูงแท่ง) และ
        # สัดส่วนของแต่ละชนิด (สี) ส่วนกราฟเส้นที่เคยมีคู่กันบอกซ้ำของเดิม
        # แต่กินพื้นที่แชทอีกเท่าตัว
        #
        # ไม่โชว์ตัวเลขบนแท่ง เพราะแท่งซ้อนจะมีเลขเต็มไปหมดรวมทั้งเลข 0
        # ของชนิดที่ไม่ได้เดิน — กราฟใน Feishu กดดูค่าได้อยู่แล้ว
        out.append(_chart({
            "type": "bar", "title": {"text": "ยอดรายชั่วโมง ({})".format(unit)},
            "data": {"values": rows}, "xField": "hour", "yField": "value",
            "seriesField": "type", "stack": True,
            # โชว์ตัวเลขบนแท่ง อ่านยอดได้โดยไม่ต้องกด
            "label": {"visible": True},
            "legends": legend, "axes": axes}))
    return out


# =====================================================================
# ประกอบการ์ด
# =====================================================================
def _link_button(url: str) -> Dict[str, Any]:
    """ปุ่มเปิดหน้าเว็บ — ใช้ปุ่มแทนลิงก์ในข้อความเพราะต้องสังเกตเห็นง่าย
    ลิงก์ที่ปนอยู่ในบรรทัดข้อความคนเลื่อนผ่านโดยไม่เห็น"""
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": "เปิดหน้าเว็บดูทุกแท็บ"},
        "type": "primary",
        "width": "fill",
        "size": "medium",
        "margin": "8px 0 4px 0",
        "behaviors": [{"type": "open_url", "default_url": url}],
    }


def _business_date() -> Optional[str]:
    """วันรอบงานปัจจุบัน — ใช้ตอนไม่มี summary (เช่น รัน Bot Export เดี่ยว ๆ)"""
    try:
        from dashboard import pipeline as dash_pipeline
        return dash_pipeline.current_business_date()
    except Exception:
        return None


def _cycle_range(business_date: Optional[str]) -> str:
    """ช่วงเวลาที่ยอดในการ์ดนี้ครอบคลุม: ต้นรอบงาน - ต้นชั่วโมงปัจจุบัน

    บอกเป็นช่วงแทนที่จะบอกแค่วัน เพราะการ์ดถูกส่งซ้ำทุกชั่วโมงระหว่างรอบ
    ใบที่ส่งตอนบ่ายกับตอนเช้าเป็นของรอบเดียวกันแต่ยอดไม่เท่ากัน
    เห็นแค่วันอย่างเดียวจะแยกไม่ออกว่าใบไหนนับถึงกี่โมง
    """
    now = datetime.now()
    end = now.strftime("%Y-%m-%d %H:00")
    if not business_date:
        return end
    try:
        from metrics import core
        start_hour = int(core.load_config()["business_day"]["start_hour"])
    except Exception:
        return end
    return "{} {:02d}:00 - {}".format(business_date, start_hour, end)


def build_card(images: Sequence[Tuple[str, str]],
               summary: Optional[Dict[str, Any]] = None,
               title: str = "ยอดปล่อยพัสดุ",
               link: str = "") -> Dict[str, Any]:
    """images = ลำดับของ (คำบรรยาย, img_key) ที่อัปโหลดไว้แล้ว

    link = ลิงก์หน้าเว็บ ถ้าใส่มาจะวางเป็นปุ่มไว้ใต้บล็อกตัวเลขสรุป ก่อนถึงกราฟ
    """
    elements: List[Dict[str, Any]] = []
    if summary:
        elements.extend(_summary_elements(summary, link))
    elif link:
        elements.append(_link_button(link))

    if images:
        if elements:                      # ไม่มีสรุปก็ไม่ต้องมีเส้นคั่นลอยบรรทัดแรก
            elements.append({"tag": "hr"})
            # บรรทัดนำรูปมีไว้คั่นจากบล็อกตัวเลขข้างบน การ์ดที่มีแต่รูป
            # ไม่มีอะไรให้คั่น ใส่ไปก็เป็นบรรทัดเปล่าเปลือง
            elements.append({"tag": "markdown",
                             "content": "**ตารางเต็ม** — กดที่รูปเพื่อดูเต็มความละเอียด"})
        # รูปเดียวไม่ต้องมีชื่อกำกับ เพราะหัวการ์ดบอกไปแล้วว่าคืออะไร
        # จะซ้ำกันสองบรรทัดติด ๆ เปล่า ๆ
        show_caption = len(images) > 1
        for caption, key in images:
            elements.append({
                "tag": "img", "img_key": key,
                "title": {"tag": "plain_text", "content": caption if show_caption else ""},
                "alt": {"tag": "plain_text", "content": caption},
                # fit_horizontal = กว้างเต็มการ์ด ไม่ครอป / preview = กดแล้วขยาย
                "scale_type": "fit_horizontal",
                "preview": True,
                "corner_radius": "4px",
                "margin": "6px 0",
            })

    business_date = summary["business_date"] if summary else _business_date()
    header_title = "{} {}".format(title, _cycle_range(business_date))

    return {
        "schema": "2.0",
        # update_multi ต้องเป็น true ถึงจะอัปเดตการ์ดใบเดิมทีหลังได้
        "config": {"update_multi": True, "wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": header_title},
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
