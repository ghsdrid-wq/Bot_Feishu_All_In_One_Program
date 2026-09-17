"""server.py — FastAPI เสิร์ฟหน้า dashboard จาก store.db ตัวเดียวกับที่ ingest เขียน

รันเดี่ยว:
    python -m dashboard.server
รันเป็น thread ในโปรแกรมหลัก (แพทเทิร์นเดียวกับ controller_api พอร์ต 6100):
    threading.Thread(target=dashboard.server.serve_forever, daemon=True).start()

หน้านี้จะถูกเปิดจากมือถือนอกโรงงานผ่าน tunnel จึงต้องมี token เสมอ —
ข้อมูลมีชื่อพนักงานและเลขพัสดุ ปล่อยเปลือยไม่ได้
"""

from __future__ import annotations

import configparser
import hmac
import os
import secrets
import sys
import threading
from datetime import date, datetime, timedelta
from typing import Optional

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from dashboard import render
from metrics import aggregate, core

CONFIG_INI = os.path.join(core.PROJECT_ROOT, "config.ini")
COOKIE_NAME = "dws_dash"
DEFAULT_PORT = 6200


# =====================================================================
# Token — เก็บใน config.ini เพราะไฟล์นั้นถูก .gitignore ไว้แล้ว
# (metrics_config.yaml อยู่ใน git จึงห้ามใส่ความลับ)
# =====================================================================

def get_settings() -> dict:
    # RawConfigParser: ไม่ตีความ % เป็น interpolation — config.ini ของโปรแกรมหลัก
    # มีค่าที่คนกรอกเองได้ ถ้าวันไหนมี % ปนมา ConfigParser ธรรมดาจะ error ทั้งไฟล์
    parser = configparser.RawConfigParser()
    parser.read(CONFIG_INI, encoding="utf-8")
    if "DASHBOARD" not in parser:
        parser["DASHBOARD"] = {}
    section = parser["DASHBOARD"]

    token = (section.get("token") or "").strip()
    if not token:
        # สร้างครั้งแรกอัตโนมัติแล้วเขียนกลับ — จะได้ไม่มีใครเผลอรันแบบไม่มี token
        token = secrets.token_urlsafe(24)
        section["token"] = token
        with open(CONFIG_INI, "w", encoding="utf-8") as fh:
            parser.write(fh)

    return {
        "token": token,
        "port": int(section.get("port", DEFAULT_PORT)),
        "host": section.get("host", "0.0.0.0"),
        "refresh_seconds": int(section.get("refresh_seconds", 60)),
        "public_url": (section.get("public_url") or "").strip(),
    }


def _token_ok(given: Optional[str], expected: str) -> bool:
    # compare_digest กัน timing attack — ถูกกว่าการเทียบด้วย == เฉยๆ
    return bool(given) and hmac.compare_digest(given, expected)


# =====================================================================
# Cache — render หนึ่งครั้งต่อ (วัน, ธีม, เวลาที่ DB เปลี่ยน)
# =====================================================================

_cache: dict[tuple, str] = {}
_cache_lock = threading.Lock()


def _db_stamp(db_path: str) -> float:
    """ใช้ mtime ของ DB เป็นตัวบอกว่าข้อมูลเปลี่ยนหรือยัง
    WAL ทำให้ไฟล์หลักไม่ถูกแตะทุกครั้ง จึงดู -wal ด้วย"""
    stamps = []
    for path in (db_path, db_path + "-wal"):
        try:
            stamps.append(os.path.getmtime(path))
        except OSError:
            pass
    return max(stamps) if stamps else 0.0


def cached_html(business_date: str, theme: str, db_path: str,
                refresh_seconds: int, nav: dict) -> str:
    key = (business_date, theme, _db_stamp(db_path),
           nav["prev"], nav["next"], nav["prev_disabled"], nav["next_disabled"])
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit

    html = render.render_html(business_date, None, db_path, theme)
    html = _inject_web_bits(html, business_date, refresh_seconds, nav)

    with _cache_lock:
        if len(_cache) > 12:          # เก็บไม่กี่วันพอ ไม่ให้กินแรมเปล่า
            _cache.clear()
        _cache[key] = html
    return html


def _inject_web_bits(html: str, business_date: str,
                     refresh_seconds: int, nav: dict) -> str:
    """เติมของที่มีเฉพาะตอนเสิร์ฟผ่านเว็บ — ปุ่มเปลี่ยนวันกับ auto refresh
    (ไฟล์ HTML ที่ export ไว้ดูออฟไลน์ไม่ควรมีสองอย่างนี้)"""
    nav_html = f'''
  <nav class="datenav">
    <a href="/d/{nav['prev']}" class="{'off' if nav['prev_disabled'] else ''}"
       title="วันก่อนหน้า">‹</a>
    <span class="cur">{business_date}</span>
    <a href="/d/{nav['next']}" class="{'off' if nav['next_disabled'] else ''}"
       title="วันถัดไป">›</a>
    <a href="/d/{nav['today']}" class="today">วันนี้</a>
  </nav>'''

    style = '''
  <style>
    .datenav { display:flex; align-items:center; gap:4px; margin-left:14px; font-size:13px; }
    .datenav a {
      display:inline-flex; align-items:center; justify-content:center;
      min-width:26px; height:26px; padding:0 8px; border-radius:6px;
      border:1px solid var(--border); color:var(--ink-2); text-decoration:none;
    }
    .datenav a:hover { color:var(--ink); border-color:var(--rule); }
    .datenav a.off { opacity:.35; pointer-events:none; }
    .datenav a.today { font-size:12px; }
    .datenav .cur { font-variant-numeric:tabular-nums; color:var(--ink); font-weight:600; padding:0 4px; }
    @media (max-width:760px) { .datenav { margin-left:0; width:100%; } }
  </style>'''

    refresh = f'''
  <script>
    // โหลดหน้าใหม่ตามรอบ — งานวิจัย dashboard หน้างานแนะนำ 30-60 วินาที
    // (ถี่กว่านั้นตากระพริบ ห่างกว่า 2 นาทีข้อมูลเก่าเกินจะใช้ตัดสินใจ)
    setTimeout(function () {{
      var hash = location.hash;
      location.href = location.pathname + location.search + hash;
    }}, {refresh_seconds * 1000});
  </script>'''

    html = html.replace("</head>", style + "\n</head>", 1)
    html = html.replace('<span class="stamp">', nav_html + '\n    <span class="stamp">', 1)
    html = html.replace("</body>", refresh + "\n</body>", 1)
    return html


# =====================================================================
# App
# =====================================================================

def latest_business_date(db_path: str) -> str:
    try:
        conn = core.read_connect(db_path)
    except Exception:                       # DB ยังไม่ถูกสร้าง — ตกไปใช้วันนี้
        row = None
    else:
        try:
            row = conn.execute(
                "SELECT MAX(business_date) AS d FROM fact_hourly").fetchone()
        finally:
            conn.close()
    if row and row["d"]:
        return row["d"]
    start_hour = core.load_config()["business_day"]["start_hour"]
    return core.business_date_of(datetime.now(), start_hour).isoformat()


def _nav_for(business_date: str, db_path: str) -> dict:
    day = date.fromisoformat(business_date)
    today = latest_business_date(db_path)
    first = today
    try:
        conn = core.read_connect(db_path)
        try:
            first = core.available_dates(conn)["first"] or today
        finally:
            conn.close()
    except Exception:
        pass
    prev = (day - timedelta(days=1)).isoformat()
    return {
        "prev": prev,
        "next": (day + timedelta(days=1)).isoformat(),
        # ปิดปุ่มเมื่อเลยขอบข้อมูล — กันคนกดย้อนไปเรื่อยๆ แล้วเจอแต่หน้าว่าง
        "prev_disabled": prev < first,
        "next_disabled": business_date >= today,
        "today": today,
        "first": first,
    }


def create_app(db_path: str = core.DEFAULT_DB_PATH) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="DWS Dashboard", docs_url=None, redoc_url=None)
    # หน้าเต็มมีทั้ง 6 แท็บรวมกันราว 900 KB — เปิดจากมือถือผ่านเน็ตมือถือจะอืด
    # HTML ที่มีตารางตัวเลขซ้ำๆ แบบนี้บีบได้เกิน 10 เท่า
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    def authorized(request: Request, t: Optional[str]) -> bool:
        return (_token_ok(t, settings["token"])
                or _token_ok(request.cookies.get(COOKIE_NAME), settings["token"])
                or _token_ok(request.headers.get("x-dashboard-token"), settings["token"]))

    def deny() -> HTMLResponse:
        return HTMLResponse(
            "<meta charset='utf-8'>"
            "<body style=\"font-family:system-ui,-apple-system,'Segoe UI',sans-serif;"
            "padding:40px;color:#52514e\">"
            "<h2 style='color:#0b0b0b'>ต้องเปิดผ่านลิงก์ที่มีรหัสเข้าถึง</h2>"
            "<p>ขอลิงก์จากผู้ดูแลระบบ แล้วเปิดจากลิงก์นั้นโดยตรง</p></body>",
            status_code=401,
        )

    @app.get("/healthz")
    def healthz():
        """ไม่ต้องมี token — tunnel กับตัวมอนิเตอร์ใช้เช็กว่า service ยังอยู่"""
        stamp = _db_stamp(db_path)
        return {
            "ok": True,
            "db": os.path.basename(db_path),
            "data_updated": datetime.fromtimestamp(stamp).isoformat(timespec="seconds") if stamp else None,
            "latest_date": latest_business_date(db_path),
        }

    @app.get("/")
    def index(request: Request, t: Optional[str] = Query(None)):
        if not authorized(request, t):
            return deny()
        target = f"/d/{latest_business_date(db_path)}"
        response = RedirectResponse(target, status_code=302)
        _set_cookie(response, settings["token"])
        return response

    @app.get("/d/{business_date}", response_class=HTMLResponse)
    def day(business_date: str, request: Request,
            t: Optional[str] = Query(None),
            theme: str = Query("light")):
        if not authorized(request, t):
            return deny()
        try:
            date.fromisoformat(business_date)
        except ValueError:
            return HTMLResponse("รูปแบบวันที่ต้องเป็น YYYY-MM-DD", status_code=400)

        html = cached_html(business_date, "dark" if theme == "dark" else "light",
                           db_path, settings["refresh_seconds"],
                           _nav_for(business_date, db_path))
        response = HTMLResponse(html)
        _set_cookie(response, settings["token"])
        return response

    @app.get("/api/overview/{business_date}")
    def api_overview(business_date: str, request: Request,
                     t: Optional[str] = Query(None)):
        if not authorized(request, t):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        conn = core.read_connect(db_path)
        try:
            return aggregate.build_overview(conn, business_date)
        finally:
            conn.close()

    @app.get("/api/report/{business_date}/{tab}")
    def api_report(business_date: str, tab: str, request: Request,
                   t: Optional[str] = Query(None)):
        if not authorized(request, t):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        spec = next((x for x in aggregate.TABS if x["key"] == tab), None)
        if spec is None:
            return JSONResponse({"error": f"ไม่รู้จักแท็บ {tab}"}, status_code=404)
        conn = core.read_connect(db_path)
        try:
            return aggregate.build_report(conn, business_date, spec["sources"])
        finally:
            conn.close()

    return app


def _set_cookie(response, token: str) -> None:
    """ใส่ token ลง cookie หลังเข้าครั้งแรก — ลิงก์ในหน้า (เปลี่ยนวัน/แท็บ)
    จะได้ไม่ต้องพก ?t= ต่อท้ายทุกอัน และ token ไม่โผล่ใน URL ที่แชร์ต่อ"""
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=30 * 24 * 3600, httponly=True, samesite="lax",
    )


def serve_forever(db_path: str = core.DEFAULT_DB_PATH,
                  port: Optional[int] = None) -> None:
    """เรียกจาก thread ในโปรแกรมหลักได้เลย"""
    import uvicorn

    settings = get_settings()
    # log_config=None สำคัญ: ค่าเริ่มต้นของ uvicorn ใช้ logging.dictConfig ที่เขียนลง
    # stdout/stderr ซึ่งไม่มีอยู่เมื่อรันด้วย pythonw.exe (หน้าต่างเปล่า ไม่มี console)
    # -> โยน "Unable to configure formatter 'default'" แล้วเว็บไม่ขึ้นเลย
    # เจอตอนเปิดผ่าน GUI จริง ซึ่งเป็นทางที่ผู้ใช้ใช้จริง
    uvicorn.run(create_app(db_path), host=settings["host"],
                port=port or settings["port"], log_config=None, access_log=False)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="เสิร์ฟ dashboard ผ่าน HTTP")
    parser.add_argument("--db", default=core.DEFAULT_DB_PATH)
    parser.add_argument("--port", type=int)
    parser.add_argument("--print-url", action="store_true",
                        help="พิมพ์ลิงก์พร้อม token แล้วออก")
    parser.add_argument("--local", action="store_true",
                        help="ใช้ localhost แทน public_url (เปิดจากเครื่องตัวเอง)")
    args = parser.parse_args()

    core.use_utf8_console()
    settings = get_settings()
    port = args.port or settings["port"]
    base = f"http://localhost:{port}" if args.local else (
        settings["public_url"] or f"http://localhost:{port}")
    link = f"{base}/?t={settings['token']}"

    if args.print_url:
        print(link)
        return 0

    print(f"Dashboard: {link}")
    print(f"ตรวจสถานะ: {base}/healthz   (ไม่ต้องใช้ token)")
    serve_forever(args.db, port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
