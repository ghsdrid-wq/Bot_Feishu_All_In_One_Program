"""render.py — สร้างหน้า dashboard เป็น HTML แล้วแคปเป็น PNG ด้วย Playwright

ตัวนี้มาแทน Createphoto.py ที่เปิด Excel ผ่าน COM:
ไม่ต้องมี Excel ติดตั้ง, ไม่ค้าง, แก้หน้าตาด้วย CSS, เร็วกว่ามาก

    python -m dashboard.render --date 2026-09-16 --png out/dashboard.png
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jinja2 import Environment, FileSystemLoader, select_autoescape

from dashboard import view
from metrics import aggregate, core

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(HERE, "templates")
DEFAULT_WIDTH = 1440


def render_html(business_date: str, tab: Optional[str] = None,
                db_path: str = core.DEFAULT_DB_PATH,
                theme: str = "light", hide_empty: bool = False,
                tables_only: bool = False) -> str:
    """tab=None -> หน้าเต็มพร้อมแท็บทั้งหมด (สำหรับเว็บ)
    tab='AUTOPACK' -> เฉพาะแผงนั้น ไม่มีแถบแท็บ (สำหรับแคป PNG ส่ง Feishu)"""
    # อ่านอย่างเดียวพอ — การ render ไม่ได้เขียนอะไรลง DB
    conn = core.read_connect(db_path) if os.path.exists(db_path) else core.connect(db_path)
    try:
        model = view.build_page(conn, business_date, tab, hide_empty)
    finally:
        conn.close()

    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    html = env.get_template("page.html.j2").render(**model)
    attrs = ""
    if theme == "dark":
        attrs += ' data-theme="dark"'
    if tables_only:
        # ซ่อนด้วย CSS ไม่ใช่ไม่เรนเดอร์ — ใช้เทมเพลตเดียวกับหน้าเว็บ
        # ถ้าแยกเทมเพลตจะต้องตามแก้สองที่ทุกครั้งที่ตารางเปลี่ยน
        attrs += ' data-shot="tables"'
    if attrs:
        html = html.replace('<html lang="th">', '<html lang="th"' + attrs + '>')
    return html


def render_png(html: str, out_path: str, width: int = DEFAULT_WIDTH,
               scale: int = 2, theme: str = "light") -> str:
    """แคปหน้าเต็มเป็น PNG — full_page ทำให้ไม่ต้องกะความสูงเอง
    (ปัญหาเดิมของ Excel คือต้องระบุ range A1:AK39 ตายตัว พอข้อมูลยาวขึ้นก็ตก)"""
    from playwright.sync_api import sync_playwright

    tmp_html = os.path.splitext(out_path)[0] + ".html"
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(tmp_html, "w", encoding="utf-8") as fh:
        fh.write(html)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        # viewport เตี้ยไว้ก่อน แล้วให้ full_page ยืดตามเนื้อหา — ถ้าตั้งสูงไว้
        # หน้าที่เนื้อหาสั้น (เช่นแท็บภาพรวม) จะได้รูปที่มีพื้นที่ว่างท้ายรูปเป็นแถบยาว
        page = browser.new_page(
            viewport={"width": width, "height": 200},
            device_scale_factor=scale,
            color_scheme=theme,
        )
        page.goto("file:///" + os.path.abspath(tmp_html).replace("\\", "/"))
        page.wait_for_load_state("networkidle")
        page.screenshot(path=out_path, full_page=True)
        browser.close()
    return out_path


def render_all_tabs(business_date: str, out_dir: str,
                    db_path: str = core.DEFAULT_DB_PATH,
                    width: int = DEFAULT_WIDTH, theme: str = "light",
                    hide_empty: bool = False,
                    tables_only: bool = False) -> list[str]:
    """แคปทุกแท็บเป็น PNG คนละใบ — PNG กดแท็บไม่ได้ ถ้าจะส่งเข้า Feishu
    ต้องแยกเป็นหลายรูป (หรือส่งแค่ใบภาพรวมแล้วแนบลิงก์ไปหน้าเว็บ)"""
    tabs = ["overview"] + [tab["key"] for tab in aggregate.TABS]
    written = []
    for tab in tabs:
        # ภาพรวมไม่มีตาราง ถ้าตัดส่วนอื่นออกจะเหลือรูปเปล่า
        html = render_html(business_date, tab, db_path, theme, hide_empty,
                           tables_only and tab != "overview")
        path = os.path.join(out_dir, f"{business_date}_{tab}.png")
        render_png(html, path, width, theme=theme)
        written.append(path)
    return written


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="render dashboard เป็น HTML/PNG")
    parser.add_argument("--date", required=True, help="วันรอบงาน YYYY-MM-DD")
    parser.add_argument("--tab", help="แท็บเดียว: overview|AUTOPACK|DWS|PDA_DWS|PDA_AUTO|BAG")
    parser.add_argument("--db", default=core.DEFAULT_DB_PATH)
    parser.add_argument("--html", help="พาธไฟล์ HTML ที่จะเขียน")
    parser.add_argument("--png", help="พาธไฟล์ PNG ที่จะเขียน")
    parser.add_argument("--all-tabs", metavar="DIR", help="แคปทุกแท็บลงโฟลเดอร์นี้")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--theme", default="light", choices=["light", "dark"])
    parser.add_argument("--tables-only", action="store_true",
                        help="แคปเฉพาะตาราง ตัดการ์ดยอด/กราฟ/สรุปกะออก (รูปที่ส่งเข้าแชท)")
    parser.add_argument("--hide-empty", action="store_true",
                        help="ซ่อนแถวของจุดที่ไม่มียอดทั้งวัน (ใช้กับ PNG ที่กดติ๊กไม่ได้)")
    core.use_utf8_console()
    args = parser.parse_args(argv)

    if args.all_tabs:
        os.makedirs(args.all_tabs, exist_ok=True)
        for path in render_all_tabs(args.date, args.all_tabs, args.db, args.width,
                                    args.theme, args.hide_empty, args.tables_only):
            print(f"PNG  -> {path}  ({os.path.getsize(path) / 1024:,.0f} KB)")
        return 0

    html = render_html(args.date, args.tab, args.db, args.theme, args.hide_empty)

    if args.html:
        os.makedirs(os.path.dirname(os.path.abspath(args.html)) or ".", exist_ok=True)
        with open(args.html, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"HTML -> {args.html}")

    if args.png:
        render_png(html, args.png, args.width, theme=args.theme)
        print(f"PNG  -> {args.png}  ({os.path.getsize(args.png) / 1024:,.0f} KB)")

    if not args.html and not args.png:
        print(html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
