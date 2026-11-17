"""สร้างรูปตาราง (PNG) และไฟล์ Excel ของยอดพัสดุ VIP รายชั่วโมง"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from PIL import Image, ImageDraw, ImageFont

from . import hub_status

# คอลัมน์สถานะท้ายตาราง: (หัวคอลัมน์ในรูป, กลุ่มสถานะ, สีตัวเลข)
STATUS_COLS = [("ออกแล้ว", hub_status.SENT, (30, 132, 73)),
               ("คงค้าง", hub_status.STUCK, (200, 16, 46)),
               ("หลุดสแกน", hub_status.MISSED, (214, 120, 0))]


@dataclass
class ReportData:
    title: str
    period_start: datetime
    period_end: datetime
    until: datetime
    brands: list[str]
    hours: list[datetime]                      # ต้นชั่วโมงแต่ละช่อง ตั้งแต่ต้นรอบถึงก่อน until
    grid: dict[str, list[int]]                 # แบรนด์ -> ยอดต่อชั่วโมง
    awbs: list[dict] = field(default_factory=list)  # รายการเลขสำหรับ Excel
    pending: int = 0
    status_checked_at: datetime | None = None

    def group_count(self, brand: str, group: str) -> int:
        """จำนวนเลขของแบรนด์ในกลุ่มสถานะ (สแกนออกแล้ว / ค้างคลัง / หลุดสแกนส่ง / ไม่พบข้อมูล)"""
        return sum(1 for a in self.awbs if a["brand"] == brand and a.get("group") == group)

    def group_total(self, group: str) -> int:
        return sum(1 for a in self.awbs if a.get("group") == group)

    @property
    def closing(self) -> bool:
        return self.until >= self.period_end

    def total(self, brand: str) -> int:
        return sum(self.grid[brand])

    def last_hour(self, brand: str) -> int:
        return self.grid[brand][-1] if self.hours else 0

    def hour_total(self, i: int) -> int:
        return sum(self.grid[b][i] for b in self.brands)

    @property
    def grand_total(self) -> int:
        return sum(self.total(b) for b in self.brands)

    @property
    def period_label(self) -> str:
        last = self.period_end - timedelta(minutes=1)
        return f"{self.period_start:%d/%m %H:%M} – {last:%d/%m %H:%M}"


# ---------------------------------------------------------------------------
# รูปตาราง
# ---------------------------------------------------------------------------
FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
RED = (200, 16, 46)
DARK = (38, 41, 48)
GREY_TXT = (120, 124, 132)
LINE = (226, 228, 232)
HEAD_BG = (246, 247, 249)
TOTAL_BG = (255, 240, 241)
WHITE = (255, 255, 255)


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    names = (["LeelaUIb.ttf", "leelawdb.ttf", "tahomabd.ttf"] if bold
             else ["LeelawUI.ttf", "leelawad.ttf", "tahoma.ttf"])
    for n in names:
        p = os.path.join(FONT_DIR, n)
        if os.path.isfile(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _heat(value: int, vmax: int) -> tuple[int, int, int]:
    """สีพื้นช่องตามยอด: 0 = ขาว, มากสุด = แดงอ่อนเข้ม"""
    if value <= 0 or vmax <= 0:
        return WHITE
    t = 0.18 + 0.62 * min(1.0, value / vmax)
    return tuple(int(255 - (255 - c) * t * 0.55) for c in RED)  # type: ignore[return-value]


def render_png(d: ReportData, path: str) -> str:
    scale = 2  # วาดใหญ่แล้วคมเวลาซูมในมือถือ
    s = lambda v: int(v * scale)
    f_title, f_sub = _font(True, s(22)), _font(False, s(13))
    f_head, f_cell, f_cell_b = _font(True, s(13)), _font(False, s(14)), _font(True, s(14))

    brand_w, hour_w, total_w, row_h, head_h = s(200), s(54), s(84), s(34), s(40)
    stat_w = s(86)
    show_status = d.status_checked_at is not None
    n_stat = len(STATUS_COLS) if show_status else 0
    pad, title_h = s(20), s(78)
    n = len(d.hours)
    width = pad * 2 + brand_w + hour_w * n + total_w + stat_w * n_stat
    height = pad + title_h + head_h + row_h * (len(d.brands) + 1) + s(36) + pad
    img = Image.new("RGB", (width, height), WHITE)
    g = ImageDraw.Draw(img)

    # หัวรูป
    g.rectangle([0, 0, width, s(6)], fill=RED)
    g.text((pad, pad), ("สรุปปิดรอบ · " if d.closing else "") + d.title, font=f_title, fill=RED)
    sub = f"รอบ {d.period_label}   ·   " + ("ยอดทั้งรอบ" if d.closing else f"ข้อมูลถึง {d.until:%d/%m/%Y %H:%M} น.")
    g.text((pad, pad + s(36)), sub, font=f_sub, fill=GREY_TXT)

    x0, y0 = pad, pad + title_h
    vmax = max([v for b in d.brands for v in d.grid[b]] + [1])

    def cell(x, y, w, text, font, fill=DARK, bg=None, align="center"):
        if bg:
            g.rectangle([x, y, x + w, y + row_h], fill=bg)
        tw = g.textlength(text, font=font)
        tx = x + s(12) if align == "left" else (x + w - tw - s(10) if align == "right" else x + (w - tw) / 2)
        g.text((tx, y + row_h / 2), text, font=font, fill=fill, anchor="lm")

    # แถวหัวตาราง
    g.rectangle([x0, y0, width - pad, y0 + head_h], fill=DARK)
    g.text((x0 + s(12), y0 + head_h / 2), "แบรนด์", font=f_head, fill=WHITE, anchor="lm")
    for i, h in enumerate(d.hours):
        x = x0 + brand_w + hour_w * i
        if i == n - 1 and not d.closing:
            g.rectangle([x, y0, x + hour_w, y0 + head_h], fill=RED)
        lbl = f"{h:%H}:00"
        g.text((x + hour_w / 2, y0 + head_h / 2), lbl, font=f_head, fill=WHITE, anchor="mm")
    xt = x0 + brand_w + hour_w * n
    g.rectangle([xt, y0, xt + total_w, y0 + head_h], fill=RED)
    g.text((xt + total_w / 2, y0 + head_h / 2), "เข้าคลัง", font=f_head, fill=WHITE, anchor="mm")
    xs = xt + total_w  # คอลัมน์สถานะต่อท้าย
    for j, (lbl, _grp, col) in enumerate(STATUS_COLS[:n_stat]):
        x = xs + stat_w * j
        g.rectangle([x, y0, x + stat_w, y0 + head_h], fill=col)
        g.text((x + stat_w / 2, y0 + head_h / 2), lbl, font=f_head, fill=WHITE, anchor="mm")

    # แถวแบรนด์
    y = y0 + head_h
    for r, b in enumerate(d.brands):
        zebra = HEAD_BG if r % 2 else WHITE
        g.rectangle([x0, y, width - pad, y + row_h], fill=zebra)
        label = b
        while g.textlength(label, font=f_cell_b) > brand_w - s(20) and len(label) > 4:
            label = label[:-2].rstrip() + "…"  # ชื่อลูกค้ายาวเกินช่อง
        cell(x0, y, brand_w, label, f_cell_b, fill=DARK if d.total(b) else GREY_TXT, align="left")
        for i, v in enumerate(d.grid[b]):
            x = x0 + brand_w + hour_w * i
            if v:
                g.rectangle([x + s(2), y + s(3), x + hour_w - s(2), y + row_h - s(3)], fill=_heat(v, vmax))
                cell(x, y, hour_w, f"{v:,}", f_cell_b)
            else:
                cell(x, y, hour_w, "·", f_cell, fill=(200, 202, 206))
        tot = d.total(b)
        cell(xt, y, total_w, f"{tot:,}", f_cell_b, fill=RED if tot else GREY_TXT, bg=TOTAL_BG)
        for j, (_lbl, grp, col) in enumerate(STATUS_COLS[:n_stat]):
            v = d.group_count(b, grp)
            cell(xs + stat_w * j, y, stat_w, f"{v:,}" if v else "·", f_cell_b if v else f_cell,
                 fill=col if v else (200, 202, 206), bg=zebra)
        g.line([x0, y + row_h, width - pad, y + row_h], fill=LINE, width=1)
        y += row_h

    # แถวรวม
    g.rectangle([x0, y, width - pad, y + row_h], fill=TOTAL_BG)
    g.line([x0, y, width - pad, y], fill=RED, width=s(1))
    cell(x0, y, brand_w, "รวมทุกแบรนด์", f_cell_b, fill=RED, align="left")
    for i in range(n):
        v = d.hour_total(i)
        cell(x0 + brand_w + hour_w * i, y, hour_w, f"{v:,}" if v else "·", f_cell_b, fill=DARK if v else GREY_TXT)
    cell(xt, y, total_w, f"{d.grand_total:,}", f_cell_b, fill=WHITE, bg=RED)
    for j, (_lbl, grp, col) in enumerate(STATUS_COLS[:n_stat]):
        v = d.group_total(grp)
        cell(xs + stat_w * j, y, stat_w, f"{v:,}", f_cell_b, fill=WHITE if v else GREY_TXT, bg=col if v else TOTAL_BG)
    y += row_h

    # Pillow ที่ไม่มี raqm วาดวรรณยุกต์ซ้อนสระบน (ที่, ชั่ว, ครั้ง) ทับกัน ข้อความในรูปจึงเลี่ยงคำแบบนั้น
    note = "ตัวเลข = จำนวนเลข AWB สแกนเข้า KKN_GW แยกตามช่วงเวลา (เลขซ้ำนับเป็น 1)   ·   หัวสีแดง = ช่วงล่าสุด"
    if show_status:
        note += f"   ·   ออกแล้ว / คงค้าง / หลุดสแกน = สถานะ ณ {d.status_checked_at:%d/%m %H:%M} น."
    g.text((pad, y + s(12)), note, font=f_sub, fill=GREY_TXT)

    img.save(path, optimize=True)
    return path


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------
def write_excel(d: ReportData, path: str) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    red, dark = "C8102E", "262930"
    hfill, tfill = PatternFill("solid", fgColor=dark), PatternFill("solid", fgColor="FFF0F1")
    rfill = PatternFill("solid", fgColor=red)
    white_b, bold = Font(bold=True, color="FFFFFF"), Font(bold=True)
    thin = Side(style="thin", color="E2E4E8")
    border = Border(top=thin, bottom=thin, left=thin, right=thin)
    center = Alignment(horizontal="center", vertical="center")
    show_status = d.status_checked_at is not None
    stat_cols = STATUS_COLS if show_status else []
    hexc = lambda rgb: "%02X%02X%02X" % rgb
    group_fill = {grp: PatternFill("solid", fgColor=hexc(col)) for _l, grp, col in STATUS_COLS}
    group_light = {hub_status.SENT: "E8F5EC", hub_status.STUCK: "FDE7EA", hub_status.MISSED: "FFF1DE"}

    # --- ชีตสรุป
    ws = wb.active
    ws.title = "สรุปรายชั่วโมง"
    ws["A1"] = ("สรุปปิดรอบ · " if d.closing else "") + d.title
    ws["A1"].font = Font(bold=True, size=14, color=red)
    sub = f"รอบ {d.period_label}  ·  " + ("ยอดทั้งรอบ" if d.closing else f"ข้อมูลถึง {d.until:%d/%m/%Y %H:%M} น.")
    if show_status:
        sub += f"  ·  สถานะ ณ {d.status_checked_at:%d/%m/%Y %H:%M} น."
    ws["A2"] = sub
    ws["A2"].font = Font(color="787C84")
    n_h = len(d.hours)
    head = ["แบรนด์"] + [f"{h:%H}:00" for h in d.hours] + ["เข้าคลัง"] + [lbl for lbl, _g, _c in stat_cols]
    tot_col = n_h + 2
    ws.append([])
    ws.append(head)
    hr = ws.max_row
    for c, _ in enumerate(head, 1):
        cell = ws.cell(hr, c)
        cell.font, cell.alignment, cell.border = white_b, center, border
        cell.fill = rfill if c == tot_col else hfill
    for j, (_l, grp, _c) in enumerate(stat_cols):
        ws.cell(hr, tot_col + 1 + j).fill = group_fill[grp]

    def style_row(r, total_row=False):
        for c in range(1, len(head) + 1):
            cell = ws.cell(r, c)
            cell.border = border
            if c > 1:
                cell.alignment = center
            if total_row:
                cell.font, cell.fill = Font(bold=True, color=red), tfill
            elif c > 1 and cell.value == 0:
                cell.font = Font(color="C8CACE")
        if not total_row:
            ws.cell(r, 1).font = bold
            ws.cell(r, tot_col).fill, ws.cell(r, tot_col).font = tfill, Font(bold=True, color=red)
        else:
            ws.cell(r, tot_col).fill, ws.cell(r, tot_col).font = rfill, white_b
        for j, (_l, grp, col) in enumerate(stat_cols):
            cell = ws.cell(r, tot_col + 1 + j)
            if cell.value:
                cell.font = Font(bold=True, color=hexc(col))

    for b in d.brands:
        ws.append([b] + d.grid[b] + [d.total(b)] + [d.group_count(b, g) for _l, g, _c in stat_cols])
        style_row(ws.max_row)
    ws.append(["รวมทุกแบรนด์"] + [d.hour_total(i) for i in range(n_h)] + [d.grand_total]
              + [d.group_total(g) for _l, g, _c in stat_cols])
    style_row(ws.max_row, total_row=True)
    ws.column_dimensions["A"].width = 18
    for c in range(2, len(head) + 1):
        ws.column_dimensions[get_column_letter(c)].width = 8 if c <= tot_col else 11
    ws.freeze_panes = ws.cell(hr + 1, 2)

    # --- ชีตรายการเลข
    cols = ["ลำดับ", "หมายเลขพัสดุ", "แบรนด์", "แหล่งที่มาคำสั่งซื้อ", "รหัสลูกค้า", "ชื่อลูกค้า",
            "เวลาสแกนเข้า KKN_GW"]
    widths = [7, 18, 14, 18, 17, 34, 20]
    if show_status:
        cols += ["สถานะ", "สถานะละเอียด", "เวลาสแกนออก", "ค้างมาแล้ว (ชม.)", "สถานะล่าสุด", "เวลาสถานะล่าสุด",
                 "สาขาสถานะล่าสุด"]
        widths += [14, 14, 20, 15, 22, 20, 22]

    def fmt_t(v):
        return v.strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, datetime) else (v or "")

    def awb_sheet(title, rows):
        sh = wb.create_sheet(title)
        sh.append(cols)
        for c in range(1, len(cols) + 1):
            cell = sh.cell(1, c)
            cell.font, cell.fill, cell.alignment = white_b, hfill, center
        for i, a in enumerate(rows, 1):
            line = [i, a["awb"], a["brand"], a["source"], a["customer_code"], a["customer_name"], a["first_scan"]]
            if show_status:
                line += [a.get("group", ""), a.get("status", ""), fmt_t(a.get("out_time")), a.get("stuck_hours"),
                         a.get("last_type", ""), fmt_t(a.get("last_time")), a.get("last_site", "")]
            sh.append(line)
            if show_status and a.get("group") in group_light:
                sh.cell(sh.max_row, 8).fill = PatternFill("solid", fgColor=group_light[a["group"]])
                sh.cell(sh.max_row, 8).font = bold
        for c, w in enumerate(widths, 1):
            sh.column_dimensions[get_column_letter(c)].width = w
        sh.freeze_panes = "C2"
        if rows:
            sh.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{sh.max_row}"
        return sh

    awb_sheet("รายการ AWB", d.awbs)
    if show_status:
        left = [a for a in d.awbs if a.get("group") in (hub_status.STUCK, hub_status.MISSED)]
        left.sort(key=lambda a: (a["group"] != hub_status.STUCK, -(a.get("stuck_hours") or 0)))
        awb_sheet("เลขที่ค้าง-หลุดสแกน", left)

    wb.save(path)
    return path
