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

import feishu_client


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


# รอบงาน+ชั่วโมงที่เพิ่งสั่งเก็บข้อมูลไป กันสั่งซ้ำหลายรอบในชั่วโมงเดียวกัน
# ตอนที่แหล่งข้อมูลยังไม่มียอดของชั่วโมงนั้นจริง ๆ (เช่นสายพานยังไม่เดิน)
_last_ingest_slot: Optional[Tuple[str, int]] = None


def _business_hour_position(hour: int, start_hour: int) -> int:
    """ลำดับของชั่วโมงในรอบงาน — รอบเริ่ม 14:00 ดังนั้น 14 คือ 0 และ 2 คือ 12

    เทียบชั่วโมงดิบตรง ๆ ไม่ได้ เพราะรอบงานข้ามเที่ยงคืน ตี 2 มาทีหลังสองทุ่ม
    แต่เลข 2 น้อยกว่า 20
    """
    return (hour - start_hour) % 24


def _stale_sources(business_date: str) -> bool:
    """ข้อมูลที่ไม่ใช่ AutoPacking ตามหลังชั่วโมงปัจจุบันแล้วหรือยัง

    AutoPacking ถูกอ่านซ้ำทุกครั้งก่อนสร้างการ์ด (refresh_autopacking) แหล่ง
    อื่นไม่ใช่ ถ้าเช็กแค่ว่า "มีแถวของแหล่งอื่นไหม" พอชั่วโมงแรกเก็บข้อมูลไป
    แล้ว เงื่อนไขจะเป็นจริงตลอดทั้งวัน แล้วไม่เก็บข้อมูลอีกเลย การ์ดจึงค้าง
    ยอด DWS/PDA/กระสอบ ไว้ที่ชั่วโมงแรก ส่วน AutoPacking เดินต่อไปเรื่อย ๆ

    จึงต้องดูว่าข้อมูลไปถึงชั่วโมงที่กำลังรายงานหรือยัง ไม่ใช่แค่มีอยู่ไหม
    """
    try:
        import sqlite3
        from metrics import core

        start_hour = int(core.load_config()["business_day"]["start_hour"])
        conn = sqlite3.connect(core.DEFAULT_DB_PATH)
        try:
            hours = [row[0] for row in conn.execute(
                "SELECT DISTINCT hour_start FROM fact_hourly "
                "WHERE business_date = ? AND source <> 'AUTOPACK'",
                (business_date,)) if row[0] is not None]
        finally:
            conn.close()

        if not hours:
            return True

        newest = max(_business_hour_position(int(h), start_hour) for h in hours)
        current = _business_hour_position(datetime.now().hour, start_hour)
        return newest < current
    except Exception:
        # เช็กไม่ได้ก็ถือว่าไม่ต้องเก็บใหม่ ดีกว่าไปไล่ ingest ทุกรอบ
        return False


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
    if not business_date:
        try:
            from dashboard import pipeline as dash_pipeline
            business_date = dash_pipeline.current_business_date()
        except Exception:
            business_date = None

    # อ่านไฟล์ AutoPacking ซ้ำก่อนเสมอ ถูกมากและปิดช่องว่างชั่วโมงล่าสุด
    refresh_autopacking(write)
    summary = load_summary(business_date)
    # มีข้อมูลแล้วก็ยังต้องเช็กว่าตามทันชั่วโมงปัจจุบันไหม — ไม่ใช่แค่ว่ามี
    # ข้อมูลอยู่ ไม่งั้นรอบที่ไม่ได้ติ๊กขั้นตอน Dashboard จะค้างยอด DWS/PDA/
    # กระสอบ ไว้ที่ชั่วโมงแรกของวัน แล้วเดินต่อเฉพาะ AutoPacking
    global _last_ingest_slot
    if summary is not None and not (business_date and _stale_sources(business_date)):
        return summary

    # สั่งเก็บข้อมูลได้ไม่เกินชั่วโมงละครั้ง ถ้าแหล่งข้อมูลยังไม่มียอดของ
    # ชั่วโมงนี้จริง ๆ จะได้ไม่ไล่ ingest ซ้ำทุกรอบที่การ์ดถูกสร้าง
    slot = (business_date or "", datetime.now().hour)
    if summary is not None and _last_ingest_slot == slot:
        return summary
    _last_ingest_slot = slot

    try:
        from dashboard import pipeline as dash_pipeline
        write("Cycle data incomplete - ingesting before building card")
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
        # เก็บข้อมูลไม่ได้ก็ใช้เท่าที่มี ห้ามล้มงานหลัก
        write("Ingest failed: {}".format(exc))
        return summary

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

# สีของกราฟ เรียงตามลำดับชนิดงานที่เข้ากราฟ
# AutoPacking -> DWS 1-11 -> PDA ลงรถ -> PDA บรรจุมือ
#
# พาเลตแดงหลัก เทานิ่ง ชมพูไฮไลต์ ตาม visual reference ของรายงาน:
# แดง = series หลัก, ชมพู = highlight, เทา = ข้อมูลรอง, คอรัล = series เสริม
# สีดำเทา #2F2F2F สงวนไว้สำหรับข้อความ จึงไม่ใช้เป็นสีข้อมูลในกราฟ
CHART_COLORS = ["#C62828", "#F4B6C2", "#D9D9D9", "#F57573"]

# Feishu renders the chart in a narrow card. Labels need enough vertical room
# to stay inside each stack segment; smaller segments remain available through
# the chart tooltip instead of producing overlapping text.
CHART_LABEL_MIN_SHARE = 0.06
# Darker counterparts of CHART_COLORS, in the same series order. These retain
# the original color identity while remaining readable after Feishu scales the
# card down.
CHART_LABEL_COLORS = ["#9E1B1B", "#B8325A", "#5F6368", "#C73E3A"]
CHART_LABEL_FONT_SIZE = 10
CHART_LABEL_SERIES_DY = [4, 0, -4, -8]
CHART_LABEL_HOUR_DY = [-2, 2]
# AutoPacking occupies the tall base segment. Alternating its labels between
# two pronounced lanes prevents neighboring full values from merging while
# keeping every label inside its own bar.
CHART_PRIMARY_LABEL_DY = [34, 0]


def _chart(spec: Dict[str, Any]) -> Dict[str, Any]:
    spec = dict(spec)
    spec.setdefault("color", CHART_COLORS)
    return {"tag": "chart", "height": CHART_HEIGHT,
            "chart_spec": spec, "preview": True, "margin": "4px 0"}


def _chart_rows_with_labels(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add compact labels only where a stacked segment has room to show them."""
    totals: Dict[str, int] = {}
    hour_order: Dict[str, int] = {}
    series_order: Dict[str, int] = {}
    for row in rows:
        hour = str(row.get("hour", ""))
        series = str(row.get("type", ""))
        hour_order.setdefault(hour, len(hour_order))
        series_order.setdefault(series, len(series_order))
        totals[hour] = totals.get(hour, 0) + max(int(row.get("value") or 0), 0)

    peak_total = max(totals.values(), default=0)
    minimum = peak_total * CHART_LABEL_MIN_SHARE
    labeled: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        value = max(int(item.get("value") or 0), 0)
        item["label_value"] = _money(value) if value and value >= minimum else ""
        hour_idx = hour_order.get(str(item.get("hour", "")), 0)
        series_idx = series_order.get(str(item.get("type", "")), 0)
        if series_idx == 0:
            item["label_dy"] = CHART_PRIMARY_LABEL_DY[hour_idx % 2]
        else:
            series_dy = CHART_LABEL_SERIES_DY[
                min(series_idx, len(CHART_LABEL_SERIES_DY) - 1)]
            item["label_dy"] = series_dy + CHART_LABEL_HOUR_DY[hour_idx % 2]
        labeled.append(item)
    return labeled


def _hourly_chart_spec(rows: Sequence[Dict[str, Any]], unit: str) -> Dict[str, Any]:
    """Build the Feishu VChart spec with readable labels for a narrow card."""
    axis_label = {"style": {"fontSize": 10, "fill": "#60656F"}}
    hour_axis_label = {
        "autoRotate": True,
        "autoRotateAngle": [45, 60],
        "autoHide": True,
        "autoHideMethod": "parity",
        "minGap": 6,
        **axis_label,
    }
    series_names = list(dict.fromkeys(str(row.get("type", "")) for row in rows))
    return {
        "type": "bar",
        "title": {"text": "ยอดรายชั่วโมง ({})".format(unit)},
        "data": {"values": _chart_rows_with_labels(rows)},
        "scales": [{
            "id": "labelColor",
            "type": "ordinal",
            "domain": series_names,
            "range": CHART_LABEL_COLORS,
        }, {
            "id": "labelDy",
            "type": "linear",
            "domain": [-40, 40],
            "range": [-40, 40],
        }],
        "xField": "hour",
        "yField": "value",
        "seriesField": "type",
        "stack": True,
        "label": {
            "visible": True,
            "position": "inside",
            "offset": 0,
            "smartInvert": False,
            "formatter": "{label_value}",
            "style": {
                "fill": {"scale": "labelColor", "field": "type"},
                "dy": {"scale": "labelDy", "field": "label_dy"},
                "stroke": "#FFFFFF",
                "lineWidth": 2,
                "fontSize": CHART_LABEL_FONT_SIZE,
                "fontWeight": "bold",
            },
            "overlap": {"hideOnHit": True},
        },
        "legends": {"visible": True, "orient": "bottom"},
        "axes": [
            {
                "orient": "bottom",
                "sampling": True,
                "paddingInner": 0.25,
                "paddingOuter": 0.08,
                "label": hour_axis_label,
            },
            {"orient": "left", "label": axis_label},
        ],
    }


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
        unit = main.get("unit", "")
        out.append({"tag": "hr"})
        # กราฟเดียวพอ — แท่งซ้อนบอกทั้งยอดรวมของชั่วโมง (ความสูงแท่ง) และ
        # สัดส่วนของแต่ละชนิด (สี) ส่วนกราฟเส้นที่เคยมีคู่กันบอกซ้ำของเดิม
        # แต่กินพื้นที่แชทอีกเท่าตัว
        #
        # Labels stay inside segments when there is room. Tiny segments keep
        # their exact value in the tooltip so the static card remains legible.
        out.append(_chart(_hourly_chart_spec(rows, unit)))
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

    return feishu_client.send_card(token, chat_id, content)
