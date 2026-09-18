"""view.py — แปลงผลจาก metrics.aggregate.build_report() ให้เป็น view model
ที่ template วาดได้ตรงๆ (คำนวณ bin สี, geometry ของกราฟ, ป้ายกำกับ)

แยกจาก template โดยตั้งใจ — ตรรกะการ bin สีกับการสเกลกราฟเป็นเรื่องที่ต้องเทสต์ได้
ไม่ควรฝังอยู่ใน Jinja
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any, Optional

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metrics import core


THAI_MONTHS = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
               "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def _safe_avg(total: int, hours: int) -> float:
    return round(total / hours, 1) if hours else 0


def translate(text: str) -> str:
    """แปลศัพท์จีนจาก raw เป็นไทยตามตาราง labels ใน config
    คำที่ไม่มีในตารางคืนค่าเดิม — ข้อมูลใหม่ที่ยังไม่ได้แปลจะได้ไม่หายไป"""
    labels = core.load_config().get("labels") or {}
    return labels.get(text, text)


def heat_class(cell: dict, target: dict, min_qty: int) -> str:
    """สีของช่อง = "เทียบเป้าแล้วเป็นยังไง" ไม่ใช่ "ยอดมากหรือน้อย"

    ของเดิมไล่สีน้ำเงินตามขนาดยอด ซึ่งอ่านแล้วไม่รู้ว่าดีหรือแย่ — ช่องที่ได้ 900
    เป็นน้ำเงินกลางๆ ดูเหมือนปกติ ทั้งที่ต่ำกว่าเป้าอยู่มาก

      no_data  ยังไม่ถึงชั่วโมงนั้น / อ่านไม่ได้      -> '–' จางๆ
      downtime เครื่องหยุด                            -> แดง มีกรอบ
      partial  ยอดต่ำกว่าเกณฑ์ชั่วโมงทำงาน (< 100)    -> เทา ไม่ตัดสินว่าแย่
                (เครื่องเพิ่งเปิด/ปิดกลางชั่วโมง ไม่ใช่ทำงานไม่ได้เป้า)
      under    ต่ำกว่าเกณฑ์เตือน                      -> แดง
      near     ระหว่างเกณฑ์เตือนกับเป้า                -> เหลือง
      onTarget ถึงเป้า                                 -> เขียว
    """
    status = cell["status"]
    if status == "no_data":
        return "cell-nodata"
    if status == "downtime":
        return "cell-down"
    value = cell["value"] or 0
    if value <= 0:
        return "cell-zero"
    if value < min_qty:
        return "cell-partial"
    if value >= target["per_hour"]:
        return "cell-ok"
    if value >= target["warn"]:
        return "cell-near"
    return "cell-under"


def thai_date(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    return f"{dt.day} {THAI_MONTHS[dt.month]} {dt.year + 543}"


def hour_label(hour: int) -> str:
    """ป้ายสั้นสำหรับแกนกราฟ — HH:00 (กราฟไม่มีที่พอสำหรับช่วงเต็ม)"""
    return f"{hour:02d}:00"


def hour_start_label(hour: int) -> str:
    return f"{hour:02d}:00"


def hour_end_label(hour: int) -> str:
    """ปลายช่วงของคอลัมน์นั้น

    หัวตารางต้องบอกเป็นช่วงเต็ม ไม่ใช่ HH:00 เฉยๆ เพราะคนอ่านเข้าใจว่า
    "ยอด ณ เวลานั้น" ทั้งที่จริงคือ "ยอดของช่วง HH:00 ถึง HH+1:00"
    ตรงกับไฟล์ดิบพอดี: 01.xlsx = ช่วง 00:00-01:00, 02.xlsx = 01:00-02:00
    """
    return f"-{(hour + 1) % 24:02d}:00"


def hour_range_label(hour: int) -> str:
    """ช่วงเต็มของคอลัมน์นั้น — คอลัมน์ 11:00 คือยอดของ 11:00-12:00 ไม่ใช่ยอด ณ 11:00
    ใช้ใน tooltip เพื่อไม่ให้เข้าใจผิดว่ารอบงานจบก่อนเที่ยง"""
    return f"{hour:02d}:00–{(hour + 1) % 24:02d}:00"


def build_view(report: dict, health: Optional[list[dict]] = None,
               source_key: Optional[str] = None) -> dict:
    """เติมข้อมูลที่ template ต้องใช้วาด — สี, สเกลกราฟ, ป้าย, สถานะเทียบเป้า"""
    cfg = core.load_config()
    hours = report["hours"]
    key = source_key or report["source"]
    from metrics import aggregate as _agg
    min_qty = _agg.min_qty_for(cfg, key)
    target = (cfg.get("targets") or {}).get(key) or {
        "per_hour": 0, "warn": 0, "label": ""}

    # ชั่วโมงล่าสุดที่มีข้อมูล — ไฮไลต์ไว้ให้รู้ว่า "ตอนนี้อยู่ตรงไหน"
    latest_idx = -1
    for idx in range(len(hours)):
        if any((r["cells"][idx]["value"] or 0) > 0 for r in report["rows"]):
            latest_idx = idx

    # ---------- ตาราง heatmap ----------
    split = _shift_split(hours, cfg)
    table_rows = []
    for row in report["rows"]:
        cells = []
        for idx, cell in enumerate(row["cells"]):
            cells.append({
                **cell,
                "css": heat_class(cell, target, min_qty),
                "text": _cell_text(cell),
                "title": _cell_title(row, cell, target),
                # ตีกรอบเฉพาะช่องที่มีข้อมูล — ถ้าตีทุกช่องรวมทั้ง '–' จะเป็นเส้นสีรกตา
                "latest": idx == latest_idx and cell["status"] not in ("no_data",),
            })
        # แยกเป็นสองช่วงกะ เพื่อแทรกคอลัมน์ยอดรวมกะ A ตรงกลางตารางแบบรายงานเดิม
        # ---- ค่าสรุปสำหรับมุมมองมือถือ ----
        # มือถือแสดงตารางเต็มไม่ไหว จึงต้องมีตัวเลขที่ย่อยมาแล้วให้ดูแถวเดียวจบ
        per_hour = target.get("per_hour") or 0
        target_pct = round(row["avg_per_hour"] / per_hour * 100) if per_hour else 0
        if not row["total"]:
            tone = "none"
        elif target_pct >= 100:
            tone = "ok"
        elif target_pct >= 70:
            tone = "near"
        else:
            tone = "under"
        # ช่องล่าสุดที่มีค่าจริง — บอกว่าตอนนี้จุดนี้ยังเดินอยู่ไหม
        last_cell = next((c for c in reversed(cells) if c["value"]), None)

        table_rows.append({
            **row, "cells": cells,
            "display_name": station_display(row.get("source", ""), row["display_name"]),
            "cells_a": cells[:split], "cells_b": cells[split:],
            "target_pct": target_pct, "tone": tone, "last_cell": last_cell,
        })

    # แถวสรุปท้ายตาราง: ยอดรวมรายชั่วโมง + เฉลี่ยต่อจุดที่เดินในชั่วโมงนั้น
    footer = []
    for idx in range(len(hours)):
        total = sum((r["cells"][idx]["value"] or 0) for r in table_rows)
        running = sum(1 for r in table_rows
                      if (r["cells"][idx]["value"] or 0) >= min_qty)
        footer.append({
            "hour": hours[idx],
            "total": total,
            "per_station": round(total / running) if running else 0,
        })

    # ---------- กราฟแท่งรายชั่วโมง (2 series ซ้อน: AP1 / AP2) ----------
    lines = sorted({r["line"] for r in report["rows"]})
    per_hour: list[dict] = []
    for idx, hour in enumerate(hours):
        stack = {}
        for line in lines:
            stack[line] = sum(
                (r["cells"][idx]["value"] or 0)
                for r in report["rows"] if r["line"] == line
            )
        per_hour.append({"hour": hour, "stack": stack, "total": sum(stack.values())})

    chart = _build_chart(per_hour, lines)
    # กราฟอีกชุดในสัดส่วนของมือถือ — ใช้ viewBox เดียวกับจอคอมไม่ได้
    # เพราะ 1180x190 พอย่อลงจอ 310px จะเหลือสูงแค่ ~50px อ่านไม่ออก
    chart_mobile = _build_chart(per_hour, lines, width=360, height=210,
                                label_every=3)
    line_labels = [translate(name) for name in lines]

    # ---------- error ----------
    # ความยาวแถบเทียบกับช่องที่มากที่สุด ไม่ใช่เทียบยอดรวม — ถ้าเทียบยอดรวม
    # ช่องที่มี 2 ช่องจะได้ ~50% ทั้งคู่ มองไม่ออกว่าอันไหนมากกว่า
    error_total = report["kpi"]["error_qty"]
    error_peak = max((e["total"] for e in report["errors"]), default=0) or 1
    errors = []
    for entry in report["errors"]:
        errors.append({
            **entry,
            "label": translate(entry["station"]),
            "bar_pct": round(entry["total"] / error_peak * 100),
            "share_pct": round(entry["total"] / error_total * 100) if error_total else 0,
            "types": [(translate(k), v) for k, v in
                      sorted(entry["by_type"].items(), key=lambda kv: -kv[1])],
        })

    # ---------- แบ่งตารางเป็น "ชั้น" ตามสาย พร้อมสรุปรายชั่วโมงของแต่ละชั้น ----------
    # รายงานเดิมแยก AP 1 / AP 2 เป็นบล็อก แล้วมีแถว Avg/Hr กับ 合计 ของชั้นนั้น
    # คั่นก่อนขึ้นชั้นถัดไป — อ่านง่ายกว่าไล่ 24 แถวรวดเดียว
    groups = []
    for lt in (report.get("line_totals") or []):
        line = lt["line"]
        grows = [r for r in table_rows if r["line"] == line]
        gfoot = []
        for idx in range(len(hours)):
            gtotal = sum((r["cells"][idx]["value"] or 0) for r in grows)
            running = sum(1 for r in grows
                          if (r["cells"][idx]["value"] or 0) >= min_qty)
            gfoot.append({
                "hour": hours[idx],
                "total": gtotal,
                "per_station": round(gtotal / running) if running else 0,
            })
        groups.append({
            **lt,
            "label": translate(line),
            "rows": grows,
            "footer_a": gfoot[:split],
            "footer_b": gfoot[split:],
            "a_total": sum(f["total"] for f in gfoot[:split]),
            "b_total": sum(f["total"] for f in gfoot[split:]),
            "avg_a": _safe_avg(lt["shift_a"], sum(r["eff_a"] for r in grows)),
            "avg_b": _safe_avg(lt["shift_b"], sum(r["eff_b"] for r in grows)),
            "empty_rows": sum(1 for r in grows if r["total"] == 0),
        })

    # ---------- ตาราง error รายชั่วโมง แยกช่อง ----------
    # รายงานเดิมมีตารางนี้แยกอีกหนึ่งตาราง (1号异常口 / 2号异常口 / 合计)
    # การ์ดแท่งบอกแค่ยอดรวม ไม่บอกว่าพีคตอนไหน ซึ่งเป็นสิ่งที่ต้องรู้เวลาไล่สาเหตุ
    error_table = None
    if report["errors"]:
        er_rows = []
        for entry in report["errors"]:
            er_rows.append({
                "label": translate(entry["station"]),
                "cells": [entry["by_hour"].get(h, 0) for h in hours],
                "total": entry["total"],
            })
        er_total = [sum(r["cells"][i] for r in er_rows) for i in range(len(hours))]
        er_peak = max(er_total) or 1
        error_table = {
            "rows": er_rows,
            "total_cells": er_total,
            "grand_total": sum(er_total),
            "peak_hour": hours[er_total.index(max(er_total))] if any(er_total) else None,
            "heat": [round(v / er_peak * 100) for v in er_total],
            "split": split,
        }

    # ---------- เกณฑ์สถานะของ KPI ----------
    th = cfg["thresholds"]["error_rate_pct"]
    rate = report["kpi"]["error_rate_pct"]
    error_status = "good" if rate <= th["good"] else ("warning" if rate <= th["warn"] else "critical")

    # ---------- แถบสถานะ: "ตอนนี้ต้องดูอะไร" ----------
    alerts = _build_alerts(report, table_rows, health, target, min_qty, latest_idx, cfg)

    # ---------- เทียบเป้าของทั้งวัน ----------
    # เป้าทั้งวัน = เป้าต่อชั่วโมง x ชั่วโมงทำงานจริงรวมทุกจุด
    # ใช้ชั่วโมงจริงแทนจำนวนชั่วโมงตามปฏิทิน เพราะจุดที่ไม่ได้เปิดไม่ควรถูกนับเป็นเป้า
    worked_hours = sum(r["effective_hours"] for r in table_rows)
    day_target = target["per_hour"] * worked_hours
    achieved_pct = round(report["kpi"]["total_qty"] / day_target * 100) if day_target else 0

    hour_cols = [{"hour": h, "label": hour_start_label(h),
                  "end_label": hour_end_label(h), "latest": i == latest_idx}
                 for i, h in enumerate(hours)]

    # kpi.best_station มาจาก build_report ซึ่งยังเป็นชื่อดิบ — เปลี่ยนให้ตรงกับ
    # ชื่อที่โชว์ในตาราง ไม่งั้นหัวการ์ดกับตารางเรียกจุดเดียวกันคนละชื่อ
    kpi = dict(report["kpi"])
    best = max((r for r in table_rows if r["total"] > 0),
               key=lambda r: r["total"], default=None)
    if best is not None:
        kpi["best_station"] = best["display_name"]

    kpi.update({
        "target_per_hour": target["per_hour"],
        "target_label": target.get("label", ""),
        "day_target": day_target,
        "achieved_pct": achieved_pct,
        "worked_hours": worked_hours,
    })

    return {
        "report": report,
        "kpi": kpi,
        "target": target,
        "alerts": alerts,
        "latest_idx": latest_idx,
        "latest_hour": hours[latest_idx] if latest_idx >= 0 else None,
        "hours": hour_cols,
        "hours_a": hour_cols[:split],
        "hours_b": hour_cols[split:],
        "shift_split": split,
        "footer": footer,
        "footer_a": footer[:split],
        "footer_b": footer[split:],
        "footer_total": sum(f["total"] for f in footer),
        "footer_a_total": sum(f["total"] for f in footer[:split]),
        "footer_b_total": sum(f["total"] for f in footer[split:]),
        "rows": table_rows,
        "groups": groups,
        "error_table": error_table,
        "lines": lines,
        "line_labels": line_labels,
        "chart": chart,
        "chart_mobile": chart_mobile,
        "errors": errors,
        "error_status": error_status,
        "health": health or [],
        "title_date": thai_date(report["business_date"]),
        "generated_at": datetime.fromisoformat(report["generated_at"]).strftime("%d/%m/%Y %H:%M"),
        "legend_bins": _legend_bins(target, min_qty),
    }


def _build_alerts(report, rows, health, target, min_qty, latest_idx, cfg) -> list[dict]:
    """สิ่งที่ต้องลงมือทำ เรียงตามความเร่งด่วน — ไม่ใช่รายการตัวเลขเฉยๆ

    งานวิจัยเรื่อง dashboard หน้างานบอกตรงกันว่า ตัวชี้วัดที่ไม่ผูกกับการตัดสินใจ
    จะกลายเป็นวอลเปเปอร์ที่ไม่มีใครมอง — แถบนี้จึงบอกเฉพาะเรื่องที่ต้องทำอะไรต่อ
    """
    alerts: list[dict] = []

    # 1) เครื่องที่ควรเดินแต่แหล่งข้อมูลมีปัญหา — เร่งด่วนสุด เพราะยอดอาจหายไปเงียบๆ
    broken = [h["station"] for h in (health or [])
              if h["health"] in ("offline", "stale")]
    if broken:
        alerts.append({
            "level": "critical", "icon": "✕",
            "text": f"อ่านข้อมูลจากเครื่องไม่ได้: {', '.join(broken)}",
            "action": "แจ้ง IT ตรวจเครื่อง — ยอดของเครื่องนี้จะหายไปจากรายงาน",
        })

    # 2) จุดที่หยุดเดิน
    stopped = [r["display_name"] for r in rows if r["downtime_hours"]]
    if stopped:
        alerts.append({
            "level": "critical", "icon": "✕",
            "text": f"เครื่องหยุดระหว่างกะ: {', '.join(stopped[:6])}"
                    + (f" และอีก {len(stopped) - 6} จุด" if len(stopped) > 6 else ""),
            "action": "ตรวจสาเหตุการหยุดและบันทึกไว้",
        })

    # 3) จุดที่ชั่วโมงล่าสุดต่ำกว่าเกณฑ์เตือน — จับได้ตั้งแต่ยังแก้ทัน
    if latest_idx >= 0 and target["warn"]:
        weak = []
        for r in rows:
            cell = r["cells"][latest_idx]
            value = cell["value"] or 0
            if cell["status"] in ("no_data", "downtime") or value < min_qty:
                continue
            if value < target["warn"]:
                weak.append(f"{r['display_name']} ({value:,})")
        if weak:
            alerts.append({
                "level": "warning", "icon": "!",
                "text": f"ชั่วโมงล่าสุดต่ำกว่าเกณฑ์ {len(weak)} จุด: {', '.join(weak[:6])}"
                        + (f" และอีก {len(weak) - 6} จุด" if len(weak) > 6 else ""),
                "action": f"เกณฑ์เตือน {target['warn']:,} · เป้า {target['per_hour']:,}",
            })

    # 4) error เกินเกณฑ์
    th = cfg["thresholds"]["error_rate_pct"]
    rate = report["kpi"]["error_rate_pct"]
    if rate > th["warn"]:
        alerts.append({"level": "critical", "icon": "✕",
                       "text": f"พัสดุตก error {rate}% (เกณฑ์ {th['warn']}%)",
                       "action": "ตรวจแผนคัดแยกและช่อง error"})
    elif rate > th["good"]:
        alerts.append({"level": "warning", "icon": "!",
                       "text": f"พัสดุตก error {rate}% (เป้า ≤ {th['good']}%)",
                       "action": "เฝ้าดูแนวโน้ม"})

    # 5) จุดที่ควรเดินแต่ไม่มียอดเลยทั้งวัน (ใช้ชื่อที่โชว์ในตาราง ไม่ใช่ชื่อดิบ)
    idle = [r["display_name"] for r in rows if r["in_service"] and r["total"] == 0]
    if idle:
        alerts.append({"level": "warning", "icon": "!",
                       "text": f"ไม่มียอดเลยทั้งวัน {len(idle)} จุด: {', '.join(idle[:8])}",
                       "action": "ยืนยันว่าตั้งใจไม่เปิดใช้"})

    if not alerts:
        alerts.append({"level": "good", "icon": "✓",
                       "text": "ทุกอย่างอยู่ในเกณฑ์", "action": ""})
    # เรียงตามความเร่งด่วน — เรื่องที่ต้องรีบต้องอยู่บรรทัดบนสุดเสมอ
    rank = {"critical": 0, "warning": 1, "good": 2}
    alerts.sort(key=lambda a: rank[a["level"]])
    return alerts


def _cell_text(cell: dict) -> str:
    if cell["status"] == "no_data":
        return "–"
    value = cell["value"] or 0
    return f"{value:,}" if value else "0"


def _cell_title(row: dict, cell: dict, target: dict) -> str:
    """tooltip — ตารางความหนาแน่นสูงแบบนี้ต้องมี hover บอกค่าเต็ม"""
    status_text = {
        "no_data": "ไม่มีข้อมูล",
        "downtime": "เครื่องหยุด",
        "zero": "ไม่มีพัสดุ",
        "ok": "ปกติ",
    }.get(cell["status"], cell["status"])
    value = cell["value"] or 0
    vs = ""
    if target["per_hour"] and value:
        pct = round(value / target["per_hour"] * 100)
        vs = f" · {pct}% ของเป้า {target['per_hour']:,}"
    return (f"{row['display_name']} · {hour_range_label(cell['hour'])} น. · "
            f"{_cell_text(cell)} · {status_text}{vs}")


def _shift_split(hours: list[int], cfg: dict) -> int:
    """ตำแหน่งที่กะ A จบ — ใช้ตีเส้นคั่นในตาราง"""
    bd = cfg["business_day"]
    for idx, hour in enumerate(hours):
        if core.shift_of(hour, bd["shift_a_start"], bd["shift_b_start"]) == "B":
            return idx
    return len(hours)


def _build_chart(per_hour: list[dict], lines: list[str],
                 width: int = 1180, height: int = 190,
                 label_every: int = 1) -> dict:
    """คำนวณ geometry ของกราฟแท่งซ้อนเอง — ไม่พึ่ง JS library เพราะหน้านี้ต้อง
    render ได้ตอนเครื่องไม่มีเน็ต (Playwright โหลด CDN ไม่ได้ = กราฟหาย)"""
    peak = max((h["total"] for h in per_hour), default=0) or 1
    pad_left, pad_bottom, pad_top = 46, 22, 14
    plot_w = width - pad_left - 8
    plot_h = height - pad_bottom - pad_top
    slot = plot_w / len(per_hour)
    bar_w = min(slot - 6, 34)

    bars = []
    peak_hour = max(per_hour, key=lambda h: h["total"])["hour"] if per_hour else None
    for idx, item in enumerate(per_hour):
        x = pad_left + slot * idx + (slot - bar_w) / 2
        y_cursor = pad_top + plot_h
        segments = []
        for s_idx, line in enumerate(lines):
            qty = item["stack"].get(line, 0)
            if qty <= 0:
                continue
            seg_h = qty / peak * plot_h
            y_cursor -= seg_h
            segments.append({
                "line": line, "label": translate(line), "qty": qty, "series": s_idx + 1,
                "x": round(x, 1), "y": round(y_cursor, 1),
                "w": round(bar_w, 1), "h": round(max(seg_h - 2, 0.5), 1),
            })
        bars.append({
            "hour": item["hour"], "label": hour_label(item["hour"]),
            "range_label": hour_range_label(item["hour"]),
            "total": item["total"], "segments": segments,
            "label_x": round(x + bar_w / 2, 1),
            "label_y": round(y_cursor - 6, 1),
            # ป้ายตัวเลขเฉพาะชั่วโมงพีค — ไม่ใส่ทุกแท่ง
            "show_label": item["hour"] == peak_hour and item["total"] > 0,
            # จอแคบใส่ป้ายชั่วโมงทุก label_every แท่ง ไม่งั้นตัวหนังสือทับกัน
            "show_hour": idx % label_every == 0,
        })

    ticks = []
    for step in range(0, 5):
        value = peak * step / 4
        ticks.append({
            "value": round(value),
            "label": f"{round(value / 1000, 1)}k" if value >= 1000 else str(round(value)),
            "y": round(pad_top + plot_h - (value / peak * plot_h), 1),
        })

    # เส้นของแต่ละชนิด — ใช้พิกัดชุดเดียวกับแท่ง เพื่อให้กราฟคู่ซ้าย-ขวาตรงกันเป๊ะ
    # เส้นวัดจากเส้นฐานเสมอ (ไม่ซ้อนกัน) จึงเทียบชนิดต่อชนิดได้ตรงๆ
    series_lines = []
    for s_idx, line in enumerate(lines):
        points = []
        for idx, item in enumerate(per_hour):
            qty = item["stack"].get(line, 0)
            x = pad_left + slot * idx + slot / 2
            y = pad_top + plot_h - (qty / peak * plot_h)
            points.append({"x": round(x, 1), "y": round(y, 1),
                           "qty": qty, "hour": item["hour"]})
        path = "M" + " L".join(f'{pt["x"]},{pt["y"]}' for pt in points)
        series_lines.append({"series": s_idx + 1, "label": translate(line),
                             "path": path, "points": points})

    return {
        "width": width, "height": height, "bars": bars, "ticks": ticks,
        "baseline_y": pad_top + plot_h, "pad_left": pad_left, "peak": peak,
        "series_lines": series_lines,
    }


def _legend_bins(target: dict, min_qty: int) -> list[dict]:
    """คำอธิบายสีเขียนเป็นภาษาคน ไม่ใช่ช่วงตัวเลขให้ไปเทียบเอง"""
    if not target["per_hour"]:
        return []
    return [
        {"css": "cell-ok",      "label": f"ถึงเป้า ({target['per_hour']:,} ขึ้นไป)"},
        {"css": "cell-near",    "label": f"เกือบถึง ({target['warn']:,}–{target['per_hour'] - 1:,})"},
        {"css": "cell-under",   "label": f"ต่ำกว่าเกณฑ์ (ต่ำกว่า {target['warn']:,})"},
        {"css": "cell-partial", "label": f"เปิด/ปิดกลางชั่วโมง (ต่ำกว่า {min_qty})"},
        {"css": "cell-zero",    "label": "ไม่มียอด"},
        {"css": "cell-down",    "label": "เครื่องหยุด"},
        {"css": "cell-nodata nodata-box", "label": "ยังไม่ถึงชั่วโมงนี้"},
    ]


# =====================================================================
# หน้าเต็ม (ภาพรวม + แท็บแยกรายแหล่ง)
# =====================================================================

def station_display(source: str, name: str) -> str:
    """ชื่อจุดที่โชว์ในตาราง — ให้ตรงกับที่ใช้เรียกกันหน้างาน
    raw ส่ง 'Dws_pda01' มา แต่ในรายงานเดิมเรียก 'DWS_PDA01'"""
    if source == "PDA_DWS":
        return name.upper()
    return name


def build_page(conn, business_date: str, only_tab: Optional[str] = None,
               hide_empty: bool = False) -> dict:
    """สร้าง model ของทั้งหน้า

    only_tab: ถ้าระบุ จะสร้างเฉพาะแท็บนั้น — ใช้ตอนแคป PNG ทีละใบส่ง Feishu
              (PNG กดแท็บไม่ได้ จึงต้องแยกเป็นคนละรูป)
    """
    from metrics import aggregate

    overview = aggregate.build_overview(conn, business_date)
    health = aggregate.machine_health(conn)

    tabs = [{"key": "overview", "title": "ภาพรวม"}]
    tabs += [{"key": t["key"], "title": t["title"]} for t in aggregate.TABS]

    panels = []
    wanted = None if only_tab in (None, "all") else only_tab
    for tab in aggregate.TABS:
        if wanted is not None and wanted != tab["key"]:
            continue
        report = aggregate.build_report(conn, business_date, tab["sources"])
        show_health = "DWS" in tab["sources"]
        panel = build_view(report, health if show_health else None, tab["key"])
        panel.update({"key": tab["key"], "title": tab["title"],
                      "unit": tab["unit"], "show_health": show_health})
        panels.append(panel)

    show_overview = wanted is None or wanted == "overview"
    overview_view = _build_overview_view(overview) if show_overview else None
    overview_hero = (_build_overview_hero(overview_view, health, core.load_config())
                     if show_overview else None)
    overview_alerts = (_overview_alerts(overview_view, health, core.load_config())
                       if show_overview else [])
    return {
        # PNG กดติ๊กไม่ได้ จึงต้องตั้งค่าเริ่มต้นตอน render ให้แทน
        "hide_empty": hide_empty,
        "business_date": business_date,
        "title_date": thai_date(business_date),
        "generated_at": datetime.fromisoformat(
            overview["generated_at"]).strftime("%d/%m/%Y %H:%M"),
        "tabs": tabs,
        "active_tab": wanted or "overview",
        "single_tab": wanted is not None,
        "show_overview": show_overview,
        "overview": overview_view,
        "hero": overview_hero,
        "overview_alerts": overview_alerts,
        "panels": panels,
        "health": health,
        # การ์ดสถานะเครื่องเป็น snapshot "ณ ปัจจุบัน" ไม่ใช่ของวันที่กำลังดู
        # ถ้าเปิดดูวันย้อนหลังต้องบอกให้ชัด ไม่งั้นเข้าใจผิดว่าเป็นสถานะของวันนั้น
        "health_is_today": business_date >= _latest_date(conn),
    }


def _latest_date(conn) -> str:
    row = conn.execute("SELECT MAX(business_date) AS d FROM fact_hourly").fetchone()
    return (row["d"] if row and row["d"] else "") or ""


def _overview_alerts(overview: dict, health, cfg) -> list[dict]:
    """แถบสถานะของหน้าภาพรวม — รวมเรื่องที่ต้องดูจากทุกแหล่งไว้บรรทัดเดียวกัน
    คนที่เปิดหน้านี้ส่วนใหญ่ไม่ได้จะมาไล่ดูทุกแท็บ แค่อยากรู้ว่ามีอะไรผิดปกติไหม"""
    alerts = []
    broken = [h["station"] for h in (health or [])
              if h["health"] in ("offline", "stale")]
    if broken:
        alerts.append({"level": "critical", "icon": "✕",
                       "text": f"อ่านข้อมูลจากเครื่องไม่ได้: {', '.join(broken)}",
                       "action": "แจ้ง IT — ยอดของเครื่องนี้จะหายจากรายงาน"})

    th = cfg["thresholds"]["error_rate_pct"]
    for src in overview["sources"]:
        if src["error_rate_pct"] > th["warn"]:
            alerts.append({"level": "critical", "icon": "✕",
                           "text": f"{src['title']}: ตก error {src['error_rate_pct']}% (เกณฑ์ {th['warn']}%)",
                           "action": "ตรวจแผนคัดแยก"})
        elif src["error_rate_pct"] > th["good"]:
            alerts.append({"level": "warning", "icon": "!",
                           "text": f"{src['title']}: ตก error {src['error_rate_pct']}% (เป้า ≤ {th['good']}%)",
                           "action": "เฝ้าดูแนวโน้ม"})

    # แหล่งที่ไม่มีข้อมูลเลยต้องขึ้นเตือน ไม่ใช่ถูกข้ามไปแล้วสรุปว่า "ทุกอย่างปกติ"
    # (เจอตอนเปิดดูวันที่ยังไม่ได้ ingest — ขึ้นแถบเขียวทั้งที่ยอดเป็น 0 ทุกช่อง)
    missing = [s["title"] for s in overview["sources"] if not s["has_data"]]
    if missing:
        alerts.append({
            "level": "warning", "icon": "!",
            "text": f"ไม่มีข้อมูลเลย {len(missing)} แหล่ง: {', '.join(missing)}",
            "action": "เช็กว่า ingest รอบล่าสุดทำงานไหม",
        })

    behind = [s for s in overview["sources"]
              if s["has_data"] and s.get("achieved_pct", 100) < 85]
    if behind:
        names = ", ".join(f"{s['title']} {s['achieved_pct']}%" for s in behind)
        alerts.append({"level": "warning", "icon": "!",
                       "text": f"ต่ำกว่าเป้า: {names}", "action": "เทียบกับเป้าทั้งวัน"})

    if not alerts:
        alerts.append({"level": "good", "icon": "✓",
                       "text": "ทุกแหล่งอยู่ในเกณฑ์", "action": ""})
    rank = {"critical": 0, "warning": 1, "good": 2}
    alerts.sort(key=lambda a: rank[a["level"]])
    return alerts


def _build_overview_view(overview: dict) -> dict:
    """เติม sparkline ให้แต่ละแหล่ง

    จงใจไม่มี 'ยอดรวมทั้งโรงงาน' — แต่ละแหล่งนับคนละหน่วยและคนละงาน
    (พัสดุชิ้นเดียวถูกนับทั้งที่ AutoPacking และที่ PDA ลงรถ) เอามาบวกกัน
    จะได้ตัวเลขที่ไม่มีความหมายและทำให้คนอ่านเข้าใจผิด
    """
    cfg = core.load_config()
    targets = cfg.get("targets") or {}
    sources = []
    for src in overview["sources"]:
        tgt = targets.get(src["key"]) or {}
        per_hour = tgt.get("per_hour", 0)
        day_target = per_hour * src.get("worked_hours", 0)
        sources.append({
            **src,
            "spark": _sparkline(src["per_hour"]),
            "target_per_hour": per_hour,
            "day_target": day_target,
            "achieved_pct": round(src["total"] / day_target * 100) if day_target else 0,
            # ชื่อจุดในการ์ดต้องเป็นชื่อเดียวกับที่โชว์ในตาราง ไม่ใช่ชื่อดิบจาก raw
            "top": [{**t, "name": station_display(t.get("source", ""), t["name"])}
                    for t in src["top"]],
        })
    return {**overview, "sources": sources,
            "hours": [{"hour": h, "label": hour_start_label(h),
                       "end_label": hour_end_label(h)}
                      for h in overview["hours"]]}



# =====================================================================
# หน้าหลัก — สรุปทั้งคลังในหน้าเดียว
# =====================================================================

# ยอดปล่อยรวมนับจาก AutoPacking อย่างเดียว
# ชนิดอื่นเป็นคนละงานและมีโอกาสนับพัสดุชิ้นเดียวซ้ำกัน (เช่น PDA ลงรถ
# ยิงพัสดุที่ผ่าน DWS มาแล้ว) เอามาบวกรวมจะได้ตัวเลขที่ไม่มีความหมาย
HERO_SOURCE = "AUTOPACK"


def _build_overview_hero(overview: dict, health: list, cfg: dict) -> dict:
    """ตัวเลขชุดที่ต้องเห็นภายในวินาทีแรก"""
    by_key = {s["key"]: s for s in overview["sources"]}
    hero = by_key.get(HERO_SOURCE) or (overview["sources"][0]
                                       if overview["sources"] else {})

    others = [s for s in overview["sources"] if s["key"] != HERO_SOURCE]

    # ชั่วโมงที่ทำได้มากสุด — บอกว่าพีคอยู่ช่วงไหนของกะ
    per_hour = hero.get("per_hour") or []
    hours = overview.get("hours") or []
    peak_idx = per_hour.index(max(per_hour)) if any(per_hour) else None
    if peak_idx is not None and peak_idx < len(hours):
        _ph = hours[peak_idx]
        peak_hour = _ph["hour"] if isinstance(_ph, dict) else _ph
    else:
        peak_hour = None

    worked = hero.get("worked_hours") or 0
    avg_per_hour = round(hero.get("total", 0) / worked) if worked else 0

    # จุดที่ทำได้มากสุด รวมทุกชนิด เรียงตามยอดต่อชั่วโมงจริง
    ranked = []
    for src in overview["sources"]:
        for t in src.get("top") or []:
            rate = t.get("avg_per_hour") or 0
            if rate <= 0:
                continue
            ranked.append({"name": t["name"], "source": src["title"],
                           "key": src["key"], "rate": round(rate),
                           "total": t.get("total", 0)})
    ranked.sort(key=lambda r: -r["rate"])

    # สิ่งผิดปกติ — นับจากทุกชนิด ไม่ใช่เฉพาะ AutoPacking
    offline = sum(1 for h in (health or [])
                  if h.get("health") in ("offline", "stale"))
    below = 0
    targets = cfg.get("targets") or {}
    for src in overview["sources"]:
        tgt = (targets.get(src["key"]) or {}).get("warn") or 0
        if not tgt:
            continue
        for t in src.get("top") or []:
            if 0 < (t.get("avg_per_hour") or 0) < tgt:
                below += 1

    return {
        "hero": hero,
        "others": others,
        "peak_hour": peak_hour,
        "peak_qty": max(per_hour) if any(per_hour) else 0,
        "avg_per_hour": avg_per_hour,
        "active": hero.get("active", 0),
        "expected": hero.get("expected", 0),
        "ranked": ranked[:6],
        "error_qty": hero.get("error_qty", 0),
        "error_rate_pct": hero.get("error_rate_pct", 0),
        "below_target": below,
        "offline": offline,
        "stacked": _overview_stacked(overview),
        # macro chart_svg ใช้ title/unit ไปทำ tooltip กับ aria-label
        "chartctx": {"title": "ยอดปล่อยทุกชนิด", "unit": "ชิ้น"},
    }


def _overview_stacked(overview: dict) -> dict:
    """กราฟแท่งซ้อนรายชั่วโมง — แต่ละชั้นคือชนิดการปล่อยงานหนึ่งชนิด

    ใช้หน่วย "ชิ้น" เท่านั้น จึงไม่รวมกระสอบเข้ามา (คนละหน่วย ซ้อนกันไม่ได้)
    """
    parts = [s for s in overview["sources"] if s.get("unit") == "ชิ้น"]
    hours = overview.get("hours") or []
    if not parts or not hours:
        return {"bars": [], "peak": 0, "series": []}

    totals = []
    for idx in range(len(hours)):
        totals.append(sum((p["per_hour"][idx] if idx < len(p["per_hour"]) else 0)
                          for p in parts))
    peak = max(totals) if totals else 0

    def hour_of(item):
        """hours เป็น int ตอนมาจาก overview ดิบ และเป็น dict หลังผ่าน view"""
        return item["hour"] if isinstance(item, dict) else item

    bars = []
    for idx, item in enumerate(hours):
        hour = hour_of(item)
        segs = []
        for s_idx, part in enumerate(parts):
            qty = part["per_hour"][idx] if idx < len(part["per_hour"]) else 0
            if qty <= 0:
                continue
            segs.append({"series": s_idx + 1, "label": part["title"],
                         "qty": qty,
                         "pct": round(qty / peak * 100, 2) if peak else 0})
        bars.append({"hour": hour, "label": hour_label(hour),
                     "range_label": hour_range_label(hour),
                     "total": totals[idx], "segments": segs,
                     "pct": round(totals[idx] / peak * 100, 2) if peak else 0,
                     "is_peak": peak > 0 and totals[idx] == peak})
    # กราฟแท่งตั้ง — ใช้ตัวสร้าง geometry ตัวเดียวกับแท็บรายแหล่ง
    # ชั้นของแท่งคือ "ชนิดการปล่อยงาน" แทนที่จะเป็นสาย AP1/AP2
    stack_rows = []
    for idx, item in enumerate(hours):
        hour = hour_of(item)
        stack = {p["title"]: (p["per_hour"][idx] if idx < len(p["per_hour"]) else 0)
                 for p in parts}
        stack_rows.append({"hour": hour, "stack": stack,
                           "total": sum(stack.values())})
    lines = [p["title"] for p in parts]
    vertical = _build_chart(stack_rows, lines, width=1180, height=210)
    vertical_mobile = _build_chart(stack_rows, lines, width=360, height=220,
                                   label_every=3)

    return {"bars": bars, "peak": peak,
            "chart": vertical, "chart_mobile": vertical_mobile,
            "series": [{"label": p["title"], "series": i + 1}
                       for i, p in enumerate(parts)]}

def _sparkline(values: list[int], width: int = 210, height: int = 34) -> dict:
    """กราฟเส้นจิ๋วในการ์ดสรุป — บอกรูปร่างของวัน ไม่ต้องอ่านค่า"""
    peak = max(values) if values else 0
    if peak <= 0:
        return {"width": width, "height": height, "path": "", "peak": 0, "bars": []}
    slot = width / len(values)
    bar_w = max(slot - 1.6, 1.2)
    bars = []
    for idx, value in enumerate(values):
        h = value / peak * (height - 4)
        bars.append({
            "x": round(idx * slot, 2), "y": round(height - h, 2),
            "w": round(bar_w, 2), "h": round(max(h, 0.8), 2),
            "peak": value == peak,
        })
    return {"width": width, "height": height, "bars": bars, "peak": peak}
