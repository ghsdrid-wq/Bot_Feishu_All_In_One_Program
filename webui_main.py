"""เปิด Auto Report แบบ Web UI

    python webui_main.py               เปิดหน้าต่างโปรแกรม (pywebview) — เครื่องอื่นในวง LAN เปิดได้ที่ http://<ip>:6300
    python webui_main.py --browser     ไม่เปิดหน้าต่าง ใช้เบราว์เซอร์แทน (เช่นเครื่องที่ไม่มี WebView2)
    python webui_main.py --server      เปิดแค่เซิร์ฟเวอร์ (ไม่มีหน้าต่าง/เบราว์เซอร์)
    python webui_main.py --local       ฟังเฉพาะเครื่องนี้ (127.0.0.1)

ใช้ config.ini ข้างไฟล์นี้ (ไฟล์เดียวกับโปรแกรมเดิม) งานทั้งหมดทำใน webui.engine.Engine ไม่มี Tk
หน้าเว็บต้อง build ก่อน: cd frontend && npm install && npm run build  (ได้ไฟล์ใน webui/static)
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

ROOT = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
sys.path.insert(0, ROOT)

PORT = 6300
_window = None


def wait_port(port: int, timeout: float = 20) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def pick(kind: str) -> str | None:
    """เลือกไฟล์/โฟลเดอร์ด้วยหน้าต่างของ Windows (เฉพาะตอนเปิดในหน้าต่างโปรแกรม)"""
    if _window is None:
        return None
    import webview

    dialog = webview.FOLDER_DIALOG if kind == "folder" else webview.OPEN_DIALOG
    types = () if kind == "folder" else ("Excel files (*.xlsx;*.xlsm;*.xlsb;*.xls)", "All files (*.*)")
    result = _window.create_file_dialog(dialog, file_types=types)
    return result[0] if result else None


def main() -> int:
    global _window
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--browser", action="store_true")
    ap.add_argument("--server", action="store_true", help="เปิดแค่เซิร์ฟเวอร์ ไม่เปิดหน้าต่าง/เบราว์เซอร์")
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--no-autostart", action="store_true", help="ไม่เปิด controller API / bot / Auto เอง (ทดสอบ)")
    args = ap.parse_args()

    # บันทึกขั้นตอนตอนเริ่ม + จุดที่ค้าง (exe แบบไม่มี console มองไม่เห็น error) -> logs/webui_startup.log
    import faulthandler

    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    startup_log = open(os.path.join(ROOT, "logs", "webui_startup.log"), "a", encoding="utf-8", buffering=1)

    def step(msg: str) -> None:
        startup_log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

    step(f"start args={sys.argv[1:]}")
    # exe แบบไม่มี console ที่เปิดจาก Scheduled Task/shortcut ไม่มี stdout/stderr (เป็น None)
    # uvicorn เขียน log ลง stream ไม่ได้แล้วเธรดเซิร์ฟเวอร์ล้มเงียบ ๆ (เจอบนเครื่องรัน 18/11/2026)
    if sys.stdout is None:
        sys.stdout = startup_log
    if sys.stderr is None:
        sys.stderr = startup_log
    faulthandler.dump_traceback_later(40, repeat=True, file=startup_log)
    try:
        from webui.api import create_app

        step("imported webui.api")
        app = create_app(picker=pick, autostart=not args.no_autostart)
        step("engine ready")
    except Exception:
        import traceback

        step("startup failed:\n" + traceback.format_exc())
        raise
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1" if args.local else "0.0.0.0", port=args.port,
                                           log_level="warning", log_config=None))

    def serve():
        try:
            server.run()
        except BaseException:
            import traceback

            step("web server crashed:\n" + traceback.format_exc())

    threading.Thread(target=serve, daemon=True).start()
    if not wait_port(args.port):
        step(f"เปิดเซิร์ฟเวอร์พอร์ต {args.port} ไม่สำเร็จ (มีโปรแกรมอื่นใช้พอร์ตนี้อยู่หรือเปล่า)")
        os._exit(1)
    faulthandler.cancel_dump_traceback_later()
    step(f"server listening on {args.port}")
    url = f"http://127.0.0.1:{args.port}/"

    if args.browser or args.server:
        if args.browser:
            webbrowser.open(url)
        print(f"Web UI: {url}  (กด Ctrl+C เพื่อปิด)")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        import webview

        _window = webview.create_window("Auto Report — KKN_GW", url, width=1400, height=900, min_size=(1100, 700))
        webview.start()  # ปิดหน้าต่าง = ปิดโปรแกรม
    server.should_exit = True
    step("closed by user")
    # ปิดทันที: เธรดของบอท/ตัวตั้งเวลาเป็น daemon แต่ตอน Python ปิดตัวอาจค้างรอ native thread (เจอตอนทดสอบ)
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
