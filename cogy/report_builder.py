# -*- coding: utf-8 -*-
"""
Report Builder:
1. ไฟล์ Excel 2 ชีท (WCS + DWS) พร้อมกรอบตารางชัดเจน แยกแถววันที่/ชั่วโมง และโทนสีแดง-เหลือง-เขียว
2. รูปแคปตาราง (PNG) เหมือนหน้า Excel เพื่อส่งเข้า Feishu ดูได้ทันที
3. กราฟสรุปยอดรายชั่วโมง + ยอดทุกเครื่อง (PNG) โทนสีส้ม-แดง J&T
"""
from datetime import datetime, timedelta
from pathlib import Path
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.utils import get_column_letter

from .cogy_client import format_dws_name, format_wcs_name, short_machine_name

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _is_shift_a(slot_key: str, shift_a_start: int = 12, shift_b_start: int = 0) -> bool:
    hr = int(slot_key.split(" ")[1].split(":")[0])
    if shift_a_start > shift_b_start:
        return hr >= shift_a_start or hr < shift_b_start
    return shift_a_start <= hr < shift_b_start


def compute_summary(data: dict, shift_a_start: int = 12, shift_b_start: int = 0) -> dict:
    slots = data["slots"]
    wcs = data["wcs"]
    dws = data["dws"]

    def _agg(machines: list[str], source: dict) -> dict:
        total = 0
        shift_a = 0
        shift_b = 0
        per_machine = {}
        per_hour = {s: 0 for s in slots}
        for m in machines:
            m_tot = 0
            m_a = 0
            m_b = 0
            act = 0
            act_a = 0
            act_b = 0
            peak = 0
            for s in slots:
                v = int(source.get(s, {}).get(m, 0))
                m_tot += v
                per_hour[s] += v
                if _is_shift_a(s, shift_a_start, shift_b_start):
                    m_a += v
                    if v > 0:
                        act_a += 1
                else:
                    m_b += v
                    if v > 0:
                        act_b += 1
                if v > 0:
                    act += 1
                if v > peak:
                    peak = v
            total += m_tot
            shift_a += m_a
            shift_b += m_b
            per_machine[m] = {
                "total": m_tot,
                "shift_a": m_a,
                "shift_b": m_b,
                "active_hours": act,
                "avg_hr": round(m_tot / act) if act else 0,
                "peak_hr": peak,
                "shift_a_avg": round(m_a / act_a) if act_a else 0,
                "shift_b_avg": round(m_b / act_b) if act_b else 0,
            }
        return {
            "total": total,
            "shift_a": shift_a,
            "shift_b": shift_b,
            "per_machine": per_machine,
            "per_hour": per_hour,
        }

    return {
        "wcs": _agg(data["wcs_machines"], wcs),
        "dws": _agg(data["dws_machines"], dws),
    }


def _ryg_hex(val: int, min_v: int, max_v: int) -> str:
    """คำนวณสีไล่ระดับ แดง (#F8696B) -> เหลือง (#FFEB84) -> เขียว (#63BE7B)"""
    if max_v <= min_v:
        return "#F8696B" if val <= 0 else "#63BE7B"
    ratio = max(0.0, min(1.0, (val - min_v) / (max_v - min_v)))
    if ratio <= 0.5:
        t = ratio * 2.0
        c1 = (0xF8, 0x69, 0x6B)
        c2 = (0xFF, 0xEB, 0x84)
    else:
        t = (ratio - 0.5) * 2.0
        c1 = (0xFF, 0xEB, 0x84)
        c2 = (0x63, 0xBE, 0x7B)
    r = round(c1[0] + (c2[0] - c1[0]) * t)
    g = round(c1[1] + (c2[1] - c1[1]) * t)
    b = round(c1[2] + (c2[2] - c1[2]) * t)
    return f"#{r:02X}{g:02X}{b:02X}"


def _write_matrix_sheet(
    ws,
    title: str,
    machines: list[str],
    slots: list[str],
    source: dict,
    summary_section: dict,
    label_fn,
):
    ws.views.sheetView[0].showGridLines = True
    thin = Side(style="thin", color="000000")
    med = Side(style="medium", color="000000")
    all_thin = Border(thin, thin, thin, thin)

    navy_fill = PatternFill("solid", fgColor="1F4E78")
    green_fill = PatternFill("solid", fgColor="2E7D32")
    date_fill = PatternFill("solid", fgColor="D9E1F2")
    shift_fill = PatternFill("solid", fgColor="E2EFDA")
    total_fill = PatternFill("solid", fgColor="FFF2CC")
    machine_fill = PatternFill("solid", fgColor="F9FAFB")

    f_title = Font(name="Tahoma", bold=True, size=14, color="1F4E78")
    f_hdr = Font(name="Tahoma", bold=True, size=10, color="FFFFFF")
    f_date = Font(name="Tahoma", bold=True, size=10, color="1F4E78")
    f_bold = Font(name="Tahoma", bold=True, size=10, color="000000")
    f_num = Font(name="Tahoma", size=10, color="000000")

    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_al = Alignment(horizontal="left", vertical="center")

    n = len(slots)
    # 4 คอลัมน์สรุปรวม (รวม, ชม.ทำงาน, Avg/Hr, Max/Hr) + 6 คอลัมน์ กะ A และ B (A, เครื่อง, Avg/Hr, B, เครื่อง, Avg/Hr)
    sum_start = n + 2
    shift_start = n + 6
    last_col = n + 11

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(last_col, 12))
    c1 = ws.cell(1, 1, title)
    c1.font = f_title
    c1.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 28

    ws.cell(2, 1, "วันที่").font = f_date
    ws.cell(2, 1).fill = date_fill
    ws.cell(2, 1).alignment = center
    ws.cell(2, 1).border = all_thin

    if n > 0:
        date_groups = []
        cur_d = slots[0][:10]
        start_idx = 2
        for idx, s in enumerate(slots, 2):
            d = s[:10]
            if d != cur_d:
                date_groups.append((cur_d, start_idx, idx - 1))
                cur_d = d
                start_idx = idx
        date_groups.append((cur_d, start_idx, n + 1))

        for d_str, c_start, c_end in date_groups:
            if c_end > c_start:
                ws.merge_cells(start_row=2, start_column=c_start, end_row=2, end_column=c_end)
            d_fmt = datetime.strptime(d_str, "%Y-%m-%d").strftime("%d/%m/%Y")
            for cc in range(c_start, c_end + 1):
                cell = ws.cell(2, cc)
                cell.fill = date_fill
                cell.border = all_thin
            dc = ws.cell(2, c_start, d_fmt)
            dc.font = f_date
            dc.alignment = center

    # แถว 2: หัวกลุ่ม "สรุปประสิทธิภาพ" และ "ยอดปล่อยพัสดุกะ A และ B"
    ws.merge_cells(start_row=2, start_column=sum_start, end_row=2, end_column=sum_start + 3)
    for cc in range(sum_start, sum_start + 4):
        ws.cell(2, cc).fill = date_fill
        ws.cell(2, cc).border = all_thin
    sc = ws.cell(2, sum_start, "สรุปประสิทธิภาพ")
    sc.font = f_date
    sc.alignment = center

    ws.merge_cells(start_row=2, start_column=shift_start, end_row=2, end_column=last_col)
    for cc in range(shift_start, last_col + 1):
        ws.cell(2, cc).fill = total_fill
        ws.cell(2, cc).border = all_thin
    shc = ws.cell(2, shift_start, "ยอดปล่อยพัสดุกะ A และ B")
    shc.font = f_bold
    shc.alignment = center
    ws.row_dimensions[2].height = 24

    hour_labels = []
    for s in slots:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
        nxt = dt + timedelta(hours=1)
        hour_labels.append(f"{dt:%H}:00\n- {nxt:%H}:00")

    tail_labels = [
        "รวม\nทั้งหมด", "ชม.\nทำงาน", "Avg/Hr", "Max/Hr",
        "กะ A\n(12-00)", "เครื่อง", "Avg/Hr\n(กะ A)",
        "กะ B\n(00-12)", "เครื่อง", "Avg/Hr\n(กะ B)",
    ]
    heads = ["เครื่อง"] + hour_labels + tail_labels
    for j, v in enumerate(heads, 1):
        c = ws.cell(3, j, v)
        if j <= n + 1:
            c.fill = green_fill
            c.font = f_hdr
        elif j < shift_start:
            c.fill = navy_fill
            c.font = f_hdr
        else:
            c.fill = PatternFill("solid", fgColor="FFD966")
            c.font = f_bold
        c.alignment = center
        c.border = all_thin
    ws.row_dimensions[3].height = 32

    r = 4
    for m in machines:
        lbl = label_fn(m)
        c0 = ws.cell(r, 1, lbl)
        c0.alignment = left_al if "(" in str(lbl) else center
        c0.border = all_thin
        c0.font = f_bold
        c0.fill = machine_fill

        for idx, s in enumerate(slots, 2):
            v = int(source.get(s, {}).get(m, 0))
            cell = ws.cell(r, idx, v)
            cell.alignment = center
            cell.border = all_thin
            cell.number_format = "#,##0"
            cell.font = f_num

        ms = summary_section["per_machine"][m]
        short_m = str(lbl).split(" (")[0]
        tail = [
            ms["total"], ms["active_hours"], ms["avg_hr"], ms["peak_hr"],
            ms["shift_a"], short_m, ms["shift_a_avg"],
            ms["shift_b"], short_m, ms["shift_b_avg"],
        ]
        for offset, v in enumerate(tail):
            col_idx = sum_start + offset
            cell = ws.cell(r, col_idx, v if isinstance(v, str) else int(v))
            cell.alignment = center
            cell.border = all_thin
            if not isinstance(v, str):
                cell.number_format = "#,##0"
            if offset < 4:
                cell.fill = total_fill
                cell.font = f_bold if offset == 0 else f_num
            else:
                cell.fill = PatternFill("solid", fgColor="FCE4D6") if offset >= 7 else shift_fill
                cell.font = f_bold if offset in (4, 7) else f_num
        ws.row_dimensions[r].height = 22
        r += 1

    last_row = r - 1
    if last_row >= 4:
        for label, fn in (("รวมทั้งหมด", "SUM"), ("เฉลี่ย/เครื่อง", "AVERAGE")):
            c_lbl = ws.cell(r, 1, label)
            c_lbl.font = f_bold
            c_lbl.border = all_thin
            c_lbl.fill = total_fill
            c_lbl.alignment = center
            for j in range(2, last_col + 1):
                col_l = get_column_letter(j)
                if j in (shift_start + 1, shift_start + 4):
                    cell = ws.cell(r, j, "-")
                else:
                    cell = ws.cell(r, j, f"=ROUND({fn}({col_l}4:{col_l}{last_row}),0)")
                    cell.number_format = "#,##0"
                cell.alignment = center
                cell.border = all_thin
                cell.font = f_bold
                cell.fill = total_fill
            ws.row_dimensions[r].height = 24
            r += 1

        # แถวสรุป "เครื่องที่ปล่อยพัสดุได้เยอะสุด" (กะ A และ กะ B)
        top_a_m, top_a_i = max(summary_section["per_machine"].items(), key=lambda x: x[1]["shift_a"])
        top_b_m, top_b_i = max(summary_section["per_machine"].items(), key=lambda x: x[1]["shift_b"])
        ws.merge_cells(start_row=r, start_column=shift_start, end_row=r, end_column=last_col)
        for cc in range(shift_start, last_col + 1):
            ws.cell(r, cc).fill = PatternFill("solid", fgColor="FFD966")
            ws.cell(r, cc).border = all_thin
        tc = ws.cell(r, shift_start, "เครื่องที่ปล่อยพัสดุได้เยอะสุด (กะ A และ กะ B)")
        tc.font = f_bold
        tc.alignment = center
        ws.row_dimensions[r].height = 22
        r += 1

        top_vals = [
            top_a_i["shift_a"], str(label_fn(top_a_m)).split(" (")[0], top_a_i["shift_a_avg"],
            top_b_i["shift_b"], str(label_fn(top_b_m)).split(" (")[0], top_b_i["shift_b_avg"],
        ]
        for offset, tv in enumerate(top_vals):
            cell = ws.cell(r, shift_start + offset, tv if isinstance(tv, str) else int(tv))
            cell.alignment = center
            cell.border = all_thin
            cell.font = f_bold
            cell.fill = PatternFill("solid", fgColor="FFF2CC")
            if not isinstance(tv, str):
                cell.number_format = "#,##0"
        ws.row_dimensions[r].height = 22
        r += 1

        if n >= 1:
            rng = f"B4:{get_column_letter(n + 1)}{last_row}"
            ws.conditional_formatting.add(
                rng,
                ColorScaleRule(
                    start_type="min", start_color="F8696B",
                    mid_type="percentile", mid_value=50, mid_color="FFEB84",
                    end_type="max", end_color="63BE7B",
                ),
            )

        final_row = r - 3
        for rr in range(2, final_row + 1):
            for cc in range(1, last_col + 1):
                cur_b = ws.cell(rr, cc).border
                ws.cell(rr, cc).border = Border(
                    left=med if cc in (1, sum_start, shift_start) else cur_b.left,
                    right=med if cc == last_col else cur_b.right,
                    top=med if rr in (2, 4, last_row + 1) else cur_b.top,
                    bottom=med if rr in (3, final_row) else cur_b.bottom,
                )

    max_a = max((len(str(label_fn(m))) for m in machines), default=12)
    ws.column_dimensions["A"].width = max(14, max_a + 4)
    for j in range(2, n + 2):
        ws.column_dimensions[get_column_letter(j)].width = 11.5
    for j in range(sum_start, last_col + 1):
        # ขยายคอลัมน์ชื่อเครื่องในกะ A/B ให้เห็นครบไม่โดนตัด
        hdr_val = str(ws.cell(3, j).value or "")
        ws.column_dimensions[get_column_letter(j)].width = 20 if hdr_val == "เครื่อง" else 13.0
    ws.freeze_panes = "B4"


def build_excel_report(
    data: dict,
    summary: dict,
    shift_a_start: int = 12,
    shift_b_start: int = 0,
    drop_empty_hours: bool = False,
) -> Path:
    slots = list(data["slots"])
    if drop_empty_hours and slots:
        non_empty = [
            s for s in slots
            if (summary["wcs"]["per_hour"].get(s, 0) + summary["dws"]["per_hour"].get(s, 0)) > 0
        ]
        if non_empty:
            slots = non_empty

    wb = Workbook()
    ws_wcs = wb.active
    ws_wcs.title = "WCS (เครื่องจ่ายงาน)"
    start_str = data["start_dt"].strftime("%Y-%m-%d %H:%M")
    end_str = data["end_dt"].strftime("%Y-%m-%d %H:%M")

    _write_matrix_sheet(
        ws_wcs,
        f"ยอดปล่อยพัสดุ WCS (AutoPacking)   ช่วงเวลา {start_str} ถึง {end_str}",
        data["wcs_machines"],
        slots,
        data["wcs"],
        summary["wcs"],
        lambda m: format_wcs_name(m),
    )

    ws_dws = wb.create_sheet("DWS (เครื่องสแกน)")
    _write_matrix_sheet(
        ws_dws,
        f"ยอดสแกนเครื่อง DWS   ช่วงเวลา {start_str} ถึง {end_str}",
        data["dws_machines"],
        slots,
        data["dws"],
        summary["dws"],
        lambda m: format_dws_name(m, with_ip=True),
    )

    report_date = (data.get("end_dt") or datetime.now()).strftime("%Y-%m-%d")
    out_path = REPORTS_DIR / f"KKN 2 report {report_date}.xlsx"
    wb.save(out_path)
    return out_path


def _render_single_table_ax(
    ax,
    title: str,
    machines: list[str],
    slots: list[str],
    source: dict,
    summary_section: dict,
    label_fn,
):
    ax.axis("off")
    n = len(slots)
    hour_cols = [s.split(" ")[1][:5] for s in slots]
    col_labels = ["เครื่อง"] + hour_cols + ["กะ A", "กะ B", "รวม", "Avg/Hr", "Max/Hr"]
    total_cols = len(col_labels)

    all_vals = [int(source.get(s, {}).get(m, 0)) for m in machines for s in slots]
    min_v = min(all_vals) if all_vals else 0
    max_v = max(all_vals) if all_vals else 1

    cell_text = []
    cell_colors = []
    for m in machines:
        ms = summary_section["per_machine"][m]
        row_t = [label_fn(m)]
        row_c = ["#F9FAFB"]
        for s in slots:
            v = int(source.get(s, {}).get(m, 0))
            row_t.append(f"{v:,}")
            row_c.append(_ryg_hex(v, min_v, max_v))
        row_t += [f"{ms['shift_a']:,}", f"{ms['shift_b']:,}", f"{ms['total']:,}", f"{ms['avg_hr']:,}", f"{ms['peak_hr']:,}"]
        row_c += ["#E2EFDA", "#E2EFDA", "#FFF2CC", "#FFF2CC", "#FFF2CC"]
        cell_text.append(row_t)
        cell_colors.append(row_c)

    tot_row = ["รวมทั้งหมด"]
    avg_row = ["เฉลี่ย/เครื่อง"]
    cnt_m = max(1, len(machines))
    for s in slots:
        hv = summary_section["per_hour"].get(s, 0)
        tot_row.append(f"{hv:,}")
        avg_row.append(f"{round(hv / cnt_m):,}")
    tot_row += [
        f"{summary_section['shift_a']:,}",
        f"{summary_section['shift_b']:,}",
        f"{summary_section['total']:,}",
        f"{round(sum(m['avg_hr'] for m in summary_section['per_machine'].values()) / cnt_m):,}",
        f"{max((m['peak_hr'] for m in summary_section['per_machine'].values()), default=0):,}",
    ]
    avg_row += [
        f"{round(summary_section['shift_a'] / cnt_m):,}",
        f"{round(summary_section['shift_b'] / cnt_m):,}",
        f"{round(summary_section['total'] / cnt_m):,}",
        "-",
        "-",
    ]
    cell_text.extend([tot_row, avg_row])
    cell_colors.extend([["#FFE699"] * total_cols, ["#FFF2CC"] * total_cols])

    raw_weights = [1.35] + [1.0] * n + [0.95, 0.95, 1.0, 1.0, 1.0]
    w_sum = sum(raw_weights)
    col_widths = [w / w_sum for w in raw_weights]
    tbl = ax.table(
        cellText=cell_text,
        colLabels=col_labels,
        cellColours=cell_colors,
        colColours=["#2E7D32"] * (n + 1) + ["#1F4E78"] * 5,
        colWidths=col_widths,
        bbox=[0.0, 0.0, 1.0, 0.90],
        cellLoc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8.5 if n <= 6 else 7.5)

    for (r, c), cell in tbl.get_celld().items():
        cell.set_edgecolor("#000000")
        cell.set_linewidth(0.7)
        if r == 0:
            cell.set_text_props(color="#FFFFFF", fontweight="bold")
        elif r >= len(machines) + 1 or c == 0 or c == n + 3:
            cell.set_text_props(color="#000000", fontweight="bold")
        if c == 0 and r > 0 and "(" in str(cell.get_text().get_text()):
            cell.set_text_props(fontsize=6.5)

    ax.set_title(title, fontsize=11, fontweight="bold", color="#1F4E78", loc="left", pad=10)


def build_table_image(data: dict, summary: dict) -> Path:
    """แคปรูปตาราง WCS + DWS โทนสีแดง-เหลือง-เขียวพร้อมกรอบคมชัด (PNG)"""
    plt.rcParams["font.sans-serif"] = ["Tahoma", "Leelawadee UI", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    active_slots = [
        s for s in data["slots"]
        if (summary["wcs"]["per_hour"].get(s, 0) + summary["dws"]["per_hour"].get(s, 0)) > 0
    ]
    if not active_slots and data["slots"]:
        active_slots = [data["slots"][-1]]

    has_wcs = len(data["wcs_machines"]) > 0
    has_dws = len(data["dws_machines"]) > 0
    start_str = data["start_dt"].strftime("%Y-%m-%d %H:%M")
    end_str = data["end_dt"].strftime("%Y-%m-%d %H:%M")
    w_fig = max(7.2, (len(active_slots) + 6) * 0.88)

    if has_wcs and has_dws:
        h_wcs = max(3.2, (len(data["wcs_machines"]) + 3) * 0.28)
        h_dws = max(3.5, (len(data["dws_machines"]) + 3) * 0.28)
        fig, (ax1, ax2) = plt.subplots(
            2, 1,
            figsize=(w_fig, h_wcs + h_dws),
            gridspec_kw={"height_ratios": [h_wcs, h_dws]},
            dpi=170,
        )
        _render_single_table_ax(
            ax1,
            f"ตารางยอดปล่อยพัสดุ WCS (AutoPacking)  |  {start_str} ถึง {end_str}",
            data["wcs_machines"],
            active_slots,
            data["wcs"],
            summary["wcs"],
            lambda m: short_machine_name(format_wcs_name(m)),
        )
        _render_single_table_ax(
            ax2,
            f"ตารางยอดสแกนเครื่อง DWS  |  {start_str} ถึง {end_str}",
            data["dws_machines"],
            active_slots,
            data["dws"],
            summary["dws"],
            lambda m: short_machine_name(format_dws_name(m, with_ip=False)),
        )
    elif has_dws:
        h_dws = max(3.5, (len(data["dws_machines"]) + 3) * 0.28)
        fig, ax = plt.subplots(1, 1, figsize=(w_fig, h_dws), dpi=170)
        _render_single_table_ax(
            ax,
            f"ตารางยอดสแกนเครื่อง DWS  |  {start_str} ถึง {end_str}",
            data["dws_machines"],
            active_slots,
            data["dws"],
            summary["dws"],
            lambda m: short_machine_name(format_dws_name(m, with_ip=False)),
        )
    else:
        h_wcs = max(3.2, (len(data["wcs_machines"]) + 3) * 0.28)
        fig, ax = plt.subplots(1, 1, figsize=(w_fig, h_wcs), dpi=170)
        _render_single_table_ax(
            ax,
            f"ตารางยอดปล่อยพัสดุ WCS (AutoPacking)  |  {start_str} ถึง {end_str}",
            data["wcs_machines"],
            active_slots,
            data["wcs"],
            summary["wcs"],
            lambda m: short_machine_name(format_wcs_name(m)),
        )

    plt.tight_layout(pad=1.2)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    img_path = REPORTS_DIR / f"CoGY_Table_{ts}.png"
    plt.savefig(img_path, bbox_inches="tight", facecolor="#FFFFFF")
    plt.close(fig)
    return img_path


def build_hourly_chart(data: dict, summary: dict) -> Path:
    """Executive Dashboard โทน J&T ส้ม-แดง: KPI Cards + กราฟรายชั่วโมง/ยอดสะสม/แบ่งกะ + อันดับเครื่องทุกตัว"""
    plt.rcParams["font.sans-serif"] = ["Tahoma", "Leelawadee UI", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    slots = data["slots"]
    labels = [s.split(" ")[1][:5] for s in slots]
    wcs_vals = [summary["wcs"]["per_hour"].get(s, 0) for s in slots]
    dws_vals = [summary["dws"]["per_hour"].get(s, 0) for s in slots]
    tot_vals = [w + d for w, d in zip(wcs_vals, dws_vals)]

    cum_vals = []
    running = 0
    for v in tot_vals:
        running += v
        cum_vals.append(running)

    peak_idx = max(range(len(tot_vals)), key=lambda i: tot_vals[i]) if tot_vals else 0
    peak_hr_lbl = labels[peak_idx] if labels else "-"
    peak_hr_val = tot_vals[peak_idx] if tot_vals else 0

    top_w_m, top_w_info = max(summary["wcs"]["per_machine"].items(), key=lambda x: x[1]["total"], default=("-", {"total": 0}))
    top_d_m, top_d_info = max(summary["dws"]["per_machine"].items(), key=lambda x: x[1]["total"], default=("-", {"total": 0}))
    top_w_name = format_wcs_name(top_w_m) if top_w_m != "-" else "-"
    top_d_name = format_dws_name(top_d_m, with_ip=False) if top_d_m != "-" else "-"

    fig = plt.figure(figsize=(17.5, 12.2), dpi=180, facecolor="#F5F6F8")
    gs = fig.add_gridspec(3, 2, height_ratios=[0.46, 1.12, 1.18], hspace=0.38, wspace=0.26)

    # === 0) แถบหัวเรื่อง + 4 KPI Cards (ดีไซน์ Modern BI) ===
    ax_kpi = fig.add_subplot(gs[0, :])
    ax_kpi.axis("off")
    ax_kpi.set_xlim(0, 1)
    ax_kpi.set_ylim(0, 1)

    # แถบแบนเนอร์สีแดง J&T ด้านบนสุด
    banner = FancyBboxPatch(
        (0.0, 0.72), 1.0, 0.26,
        boxstyle="round,pad=0.0,rounding_size=0.02",
        facecolor="#C62828", edgecolor="none",
    )
    ax_kpi.add_patch(banner)
    ax_kpi.text(
        0.015, 0.85,
        "J&T EXPRESS  |  CoGY SORTING OPERATIONS DASHBOARD",
        fontsize=13.5, fontweight="bold", color="#FFFFFF", va="center",
    )
    ax_kpi.text(
        0.985, 0.85,
        f"ช่วงเวลา: {data['start_dt']:%d/%m/%Y %H:%M} - {data['end_dt']:%d/%m/%Y %H:%M}",
        fontsize=10, fontweight="bold", color="#FFEBEE", ha="right", va="center",
    )

    cards = [
        ("WCS (AutoPacking)", f"{summary['wcs']['total']:,}", f"กะ A: {summary['wcs']['shift_a']:,}   |   กะ B: {summary['wcs']['shift_b']:,}", "#D32F2F", "#FFF5F5"),
        ("DWS (เครื่องสแกน)", f"{summary['dws']['total']:,}", f"กะ A: {summary['dws']['shift_a']:,}   |   กะ B: {summary['dws']['shift_b']:,}", "#FF6D00", "#FFF8F0"),
        ("Peak Hour (ชม.สูงสุด)", f"{peak_hr_val:,}", f"เวลา {peak_hr_lbl} น.   |   รวมทั้งหมด {running:,}", "#E65100", "#FFF5F0"),
        ("Top Machine", f"{top_d_name} ({top_d_info['total']:,})", f"WCS สูงสุด: {top_w_name} ({top_w_info['total']:,})", "#263238", "#F4F6F8"),
    ]
    gap = 0.018
    cw = (1.0 - gap * 3) / 4.0
    for i, (c_title, c_val, c_sub, accent_c, bg_c) in enumerate(cards):
        x0 = i * (cw + gap)
        box = FancyBboxPatch(
            (x0, 0.02), cw, 0.61,
            boxstyle="round,pad=0.0,rounding_size=0.018",
            facecolor="#FFFFFF", edgecolor="#DDE2E8", linewidth=1.2,
        )
        ax_kpi.add_patch(box)
        stripe = FancyBboxPatch(
            (x0, 0.02), 0.008, 0.61,
            boxstyle="round,pad=0.0,rounding_size=0.004",
            facecolor=accent_c, edgecolor="none",
        )
        ax_kpi.add_patch(stripe)
        ax_kpi.text(x0 + 0.018, 0.50, c_title, fontsize=9.5, fontweight="bold", color=accent_c, va="center")
        ax_kpi.text(x0 + 0.018, 0.30, c_val, fontsize=16, fontweight="bold", color="#1A1A1A", va="center")
        ax_kpi.text(x0 + 0.018, 0.11, c_sub, fontsize=8.2, color="#546E7A", va="center")

    # === 1) กราฟรายชั่วโมง (Stacked Bar + Cumulative Line + Shift Shading) ===
    ax_hr = fig.add_subplot(gs[1, :])
    ax_hr.set_facecolor("#FFFFFF")
    x = list(range(len(slots)))

    # แรเงาแบ่งกะ A vs กะ B
    shift_b_start_idx = next((i for i, s in enumerate(slots) if int(s.split(" ")[1][:2]) == 0), None)
    if shift_b_start_idx is not None and shift_b_start_idx > 0:
        ax_hr.axvspan(-0.5, shift_b_start_idx - 0.5, color="#FFF8F5", alpha=0.85, zorder=0)
        ax_hr.axvspan(shift_b_start_idx - 0.5, len(slots) - 0.5, color="#F3F6FA", alpha=0.85, zorder=0)
        ax_hr.axvline(shift_b_start_idx - 0.5, color="#78909C", linestyle="--", linewidth=1.2, zorder=1)
        ax_hr.text(0.02, 0.94, "กะ A (12:00 - 23:59)", transform=ax_hr.transAxes, fontsize=8.5, fontweight="bold", color="#BF360C")
        ax_hr.text(
            (shift_b_start_idx + 0.1) / max(1, len(slots)), 0.94,
            "กะ B (00:00 - 11:59)", transform=ax_hr.transAxes, fontsize=8.5, fontweight="bold", color="#37474F",
        )

    w = 0.56
    b1 = ax_hr.bar(x, wcs_vals, width=w, color="#D32F2F", edgecolor="#B71C1C", linewidth=0.6, label=f"WCS ({summary['wcs']['total']:,})", zorder=3)
    b2 = ax_hr.bar(x, dws_vals, width=w, bottom=wcs_vals, color="#FF6D00", edgecolor="#E65100", linewidth=0.6, label=f"DWS ({summary['dws']['total']:,})", zorder=3)

    max_tot = max(tot_vals) if tot_vals else 1
    ax_hr.set_ylim(0, max_tot * 1.32)
    lbl_fs = 7.5 if len(slots) > 16 else 8.5
    for i, (wv, dv, tv) in enumerate(zip(wcs_vals, dws_vals, tot_vals)):
        if tv > 0:
            lbl_col = "#B71C1C" if wv >= dv else "#BF360C"
            ax_hr.text(i, tv + max_tot * 0.02, f"{tv:,}", ha="center", va="bottom", fontsize=lbl_fs, fontweight="bold", color=lbl_col, zorder=5)

    # เส้นยอดสะสม (แกนขวา)
    ax_cum = ax_hr.twinx()
    ax_cum.plot(x, cum_vals, color="#263238", marker="o", markersize=4.5, linewidth=2.0, linestyle="-", label="ยอดสะสม (Cumulative)", zorder=4)
    ax_cum.set_ylim(0, (max(cum_vals) if cum_vals else 1) * 1.25)
    ax_cum.tick_params(axis="y", labelsize=8.5, colors="#37474F")
    ax_cum.spines["top"].set_visible(False)
    ax_cum.spines["right"].set_color("#B0BEC5")
    if cum_vals:
        ax_cum.text(len(x) - 1, cum_vals[-1] * 1.04, f"สะสม {cum_vals[-1]:,}", ha="right", va="bottom", fontsize=9, fontweight="bold", color="#263238")

    ax_hr.set_xticks(x)
    ax_hr.set_xticklabels(labels, fontsize=8.5, fontweight="bold", rotation=25 if len(slots) > 16 else 0, ha="right" if len(slots) > 16 else "center")
    ax_hr.tick_params(axis="y", labelsize=8.5)
    ax_hr.set_title("ปริมาณงานรายชั่วโมง (แท่ง) และยอดสะสมต่อเนื่อง (เส้น)", fontsize=11, fontweight="bold", color="#263238", loc="left", pad=8)
    ax_hr.yaxis.grid(True, linestyle=":", alpha=0.5, color="#CFD8DC", zorder=0)
    for sp in ("top",):
        ax_hr.spines[sp].set_visible(False)

    h1, l1 = ax_hr.get_legend_handles_labels()
    h2, l2 = ax_cum.get_legend_handles_labels()
    ax_hr.legend(h1 + h2, l1 + l2, loc="upper left", bbox_to_anchor=(0.0, 0.89), ncol=3, fontsize=8.5, frameon=True, facecolor="#FFFFFF", edgecolor="#CFD8DC")

    # === 2) ด้านล่างซ้าย/ขวา: ยอดทุกเครื่องเรียงตามเลขเครื่อง (ไฮไลต์ Top 3 สีเข้ม) ===
    def _plot_rank_barh(ax, machines, per_machine, label_fn, title, colors_pair):
        ax.set_facecolor("#FFFFFF")
        sorted_m = list(machines)
        top3 = set(sorted(machines, key=lambda m: per_machine[m]["total"], reverse=True)[:3])
        m_labels = [label_fn(m) for m in sorted_m]
        m_tots = [per_machine[m]["total"] for m in sorted_m]
        m_avgs = [per_machine[m]["avg_hr"] for m in sorted_m]
        y = list(range(len(sorted_m)))

        c_hi, c_lo = colors_pair
        max_v = max(m_tots) if m_tots else 1
        bar_colors = [c_hi if m in top3 else c_lo for m in sorted_m]
        bars = ax.barh(y, m_tots, color=bar_colors, edgecolor="#424242", linewidth=0.4, height=0.66, zorder=3)
        ax.set_yticks(y)
        ax.set_yticklabels(m_labels, fontsize=9, fontweight="bold")
        ax.set_ylim(len(sorted_m) - 0.35, -0.65)
        ax.set_xlim(0, max_v * 1.48)
        for idx, b in enumerate(bars):
            val = b.get_width()
            avg_v = m_avgs[idx]
            txt = f"{val:,}  ({avg_v:,}/ชม.)" if val > 0 else "0"
            ax.text(val + max_v * 0.02, b.get_y() + b.get_height() / 2, txt, va="center", fontsize=8.5, fontweight="bold", color="#263238")
        ax.set_title(title, fontsize=10.5, fontweight="bold", color=c_hi, loc="left", pad=8)
        ax.xaxis.grid(True, linestyle=":", alpha=0.5, color="#CFD8DC", zorder=0)
        ax.tick_params(axis="x", labelsize=8)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)

    ax_w = fig.add_subplot(gs[2, 0])
    _plot_rank_barh(
        ax_w,
        data["wcs_machines"],
        summary["wcs"]["per_machine"],
        lambda m: format_wcs_name(m),
        f"อันดับยอดปล่อย WCS ทั้งหมด ({len(data['wcs_machines'])} เครื่อง)",
        ("#B71C1C", "#EF5350"),
    )

    ax_d = fig.add_subplot(gs[2, 1])
    _plot_rank_barh(
        ax_d,
        data["dws_machines"],
        summary["dws"]["per_machine"],
        lambda m: format_dws_name(m, with_ip=False),
        f"อันดับยอดสแกน DWS ทั้งหมด ({len(data['dws_machines'])} เครื่อง)",
        ("#E65100", "#FFA726"),
    )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    chart_path = REPORTS_DIR / f"CoGY_Chart_{ts}.png"
    plt.savefig(chart_path, bbox_inches="tight", pad_inches=0.25, facecolor="#F5F6F8")
    plt.close(fig)
    return chart_path
