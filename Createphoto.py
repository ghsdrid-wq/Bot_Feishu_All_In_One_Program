import os
import sys
import time
import re
import gc
import configparser
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, cast

import pythoncom
import win32com.client as win32

import win32gui
import win32con
import win32process

LogFunc = Callable[[str], None]
RunFunc = Callable[[], bool]


def get_excel_pid(excel: Any) -> Optional[int]:
    """คืน PID ของ Excel instance ที่บอทสร้างเอง (ผ่าน Hwnd)
    ไว้ใช้ปิดแบบเจาะจงตัวเดียว ไม่แตะ Excel ที่ผู้ใช้เปิดอยู่"""
    try:
        hwnd = excel.Hwnd
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        return int(pid) or None
    except Exception:
        return None


def kill_excel_pid_if_alive(pid: Optional[int], log: Optional[LogFunc] = None) -> None:
    """Force-close เฉพาะ Excel PID ของบอทเอง ถ้ายังค้างอยู่หลัง Quit()
    (ปลอดภัย — ไม่ปิด Excel ตัวอื่นของผู้ใช้). ถ้า process ปิดไปแล้ว taskkill
    จะคืน non-zero เฉยๆ ไม่ทำอะไร"""
    if not pid:
        return
    try:
        import subprocess
        result = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            text=True,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        if result.returncode == 0 and log:
            log(f"[CLEANUP] Force-closed leftover Excel PID {pid}")
    except Exception as e:
        if log:
            log(f"[CLEANUP] taskkill Excel PID {pid} failed: {e}")


def hide_excel_from_taskbar():
    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)

            if "Excel" in title:
                # ซ่อนจาก taskbar
                ex_style = win32gui.GetWindowLong(
                    hwnd,
                    win32con.GWL_EXSTYLE
                )

                win32gui.SetWindowLong(
                    hwnd,
                    win32con.GWL_EXSTYLE,
                    ex_style | win32con.WS_EX_TOOLWINDOW
                )

                win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)

    win32gui.EnumWindows(callback, None)

def resource_path(file: str) -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), file)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), file)


def load_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(resource_path("config.ini"), encoding="utf-8")
    return config


def save_config(config: configparser.ConfigParser) -> None:
    config_path = resource_path("config.ini")
    tmp_path = f"{config_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        config.write(f)
    os.replace(tmp_path, config_path)


@dataclass
class WorkbookItem:
    key: str
    path: str
    display_name: str = ""
    enabled: bool = True


@dataclass
class ExportItem:
    name: str
    workbook: str
    sheet: str
    cell_range: str
    filename: str
    delete_by_start: bool
    enabled: bool = True
    send_enabled: bool = True


def as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    value = str(value).strip().lower()
    if value == "":
        return default
    return value in {"1", "true", "yes", "y", "on"}


def safe_key(text: str, prefix: str = "WB") -> str:
    base = os.path.splitext(os.path.basename(text))[0] if text else prefix
    key = re.sub(r"[^A-Za-z0-9_]+", "_", base).strip("_").upper()
    return key or prefix


def unique_key(existing: List[str], candidate: str) -> str:
    candidate = safe_key(candidate)
    if candidate not in existing:
        return candidate
    i = 2
    while f"{candidate}_{i}" in existing:
        i += 1
    return f"{candidate}_{i}"


def get_time_config() -> configparser.SectionProxy:
    config = load_config()
    if "TIME" not in config:
        config["TIME"] = {"start_hour": "15", "end_hour": "12", "run_minute": "5"}
        save_config(config)
    return config["TIME"]


def get_business_date(start_hour: int) -> str:
    now = datetime.now()
    target_date = now - timedelta(days=1) if now.hour < start_hour else now
    return target_date.strftime("%Y-%m-%d")


def update_excel_date(wb, start_hour: int, log: Optional[LogFunc] = None) -> None:
    try:
        ws = excel_call(lambda: wb.Worksheets(1), log, "open first worksheet", timeout=120)
        date_str = get_business_date(start_hour)
        excel_call(lambda: setattr(ws.Range("A2"), "Value", date_str), log, "set A2 date", timeout=120)
        if log:
            log(f"[DATE SET] A2 = {date_str}")
    except Exception as e:
        if log:
            log(f"[ERROR] Update date failed: {e}")
        else:
            print(e)


def delete_columns_by_start(wb, start_hour: int, target_sheets: List[str], log: Optional[LogFunc] = None) -> int:
    """Original delete logic preserved: delete C until mapped column, backward."""
    try:
        hour_to_col = {
            12: 3, 13: 4, 14: 5, 15: 6, 16: 7, 17: 8,
            18: 9, 19: 10, 20: 11, 21: 12, 22: 13, 23: 14,
            0: 15, 1: 16, 2: 17, 3: 18, 4: 19, 5: 20,
            6: 21, 7: 22, 8: 23, 9: 24, 10: 25, 11: 26,
        }
        delete_until = hour_to_col.get(start_hour)
        if not delete_until:
            return 0

        deleted_count = delete_until - 3
        for sheet in target_sheets:
            ws = excel_call(lambda s=sheet: wb.Worksheets(s), log, f"open worksheet {sheet}", timeout=120)
            for col in range(delete_until - 1, 2, -1):
                excel_call(lambda c=col: ws.Columns(c).Delete(Shift=-4159), log, f"delete column {sheet}:{col}", timeout=180)
            if log:
                log(f"[DELETE] {sheet} {deleted_count} cols")
        return deleted_count
    except Exception as e:
        if log:
            log(f"[ERROR] delete_columns: {e}")
        return 0


def shift_range_left(rng: str, shift_cols: int) -> str:
    if shift_cols <= 0:
        return rng

    def col_to_num(col: str) -> int:
        num = 0
        for c in col:
            num = num * 26 + (ord(c.upper()) - ord("A") + 1)
        return num

    def num_to_col(num: int) -> str:
        if num < 1:
            num = 1
        col = ""
        while num:
            num, rem = divmod(num - 1, 26)
            col = chr(rem + ord("A")) + col
        return col

    m = re.match(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", rng.upper().strip())
    if not m:
        return rng
    c1, r1, c2, r2 = m.groups()
    new_c2 = num_to_col(col_to_num(c2) - shift_cols)
    return f"{c1}{r1}:{new_c2}{r2}"


def migrate_old_export_config(config: configparser.ConfigParser) -> None:
    """Convert old AUTO/DWS config into Workbook Manager config once.
    This is a config mapping migration only; Excel processing logic stays the same.
    """
    if "WORKBOOKS" in config and config["WORKBOOKS"].get("items"):
        if "EXPORTS" not in config:
            config["EXPORTS"] = {"items": ""}
        return

    if "PATH" not in config:
        config["PATH"] = {}

    wb_keys: List[str] = []
    auto_path = config["PATH"].get("auto_file", "")
    dws_path = config["PATH"].get("dws_file", "")

    if auto_path:
        wb_keys.append("AUTO")
        config["WORKBOOK:AUTO"] = {
            "path": auto_path,
            "display_name": os.path.basename(auto_path) or "AUTOPACKING",
            "enabled": "true",
        }
    if dws_path:
        wb_keys.append("DWS")
        config["WORKBOOK:DWS"] = {
            "path": dws_path,
            "display_name": os.path.basename(dws_path) or "DWS & PDA",
            "enabled": "true",
        }

    config["WORKBOOKS"] = {"items": ",".join(wb_keys)}

    if "EXPORTS" in config and config["EXPORTS"].get("items"):
        return

    defaults = [
        ("AUTO", "AUTO", "AUTO_SHEET", "AUTO_RANGE", "AUTO_FILE", True),
        ("DWSREALTIME", "DWS", "DWSREALTIME_SHEET", "DWSREALTIME_RANGE", "DWSREALTIME_FILE", True),
        ("AUTO_PDA", "DWS", "AUTO_PDA_SHEET", "AUTO_PDA_RANGE", "AUTO_PDA_FILE", True),
        ("DWS_PDA", "DWS", "DWS_PDA_SHEET", "DWS_PDA_RANGE", "DWS_PDA_FILE", True),
        ("REALTIME_DB", "DWS", "REALTIME_DB_SHEET", "REALTIME_DB_RANGE", "REALTIME_DB_FILE", False),
    ]
    old = config["EXPORT"] if "EXPORT" in config else None
    fallback = {
        "AUTO_SHEET": "Autoformat", "AUTO_RANGE": "A1:AK39", "AUTO_FILE": "AUTOREALTIME.png",
        "DWSREALTIME_SHEET": "DWSREALTIME", "DWSREALTIME_RANGE": "A1:AE16", "DWSREALTIME_FILE": "DWSREALTIME.png",
        "AUTO_PDA_SHEET": "AUTO PDA", "AUTO_PDA_RANGE": "A1:AC30", "AUTO_PDA_FILE": "AUTO_PDA.png",
        "DWS_PDA_SHEET": "DWS PDA", "DWS_PDA_RANGE": "A1:AC15", "DWS_PDA_FILE": "DWS_PDA.png",
        "REALTIME_DB_SHEET": "Sheet1", "REALTIME_DB_RANGE": "A1:Z50", "REALTIME_DB_FILE": "REALTIME_DB.png",
    }

    def old_value(key: str) -> str:
        if old is None:
            return fallback[key]
        return old[key] if key in old else fallback[key]

    export_names = []
    for name, workbook_key, sheet_key, range_key, file_key, delete_flag in defaults:
        if workbook_key not in wb_keys:
            continue
        export_names.append(name)
        section = f"EXPORT:{name}"
        config[section] = {
            "enabled": "true",
            "send_enabled": "true",
            "workbook": workbook_key,
            "sheet": old_value(sheet_key),
            "range": old_value(range_key),
            "file": old_value(file_key),
            "delete_by_start": "true" if delete_flag else "false",
        }
    config["EXPORTS"] = {"items": ",".join(export_names)}


def get_workbooks(config: Optional[configparser.ConfigParser] = None, only_enabled: bool = True) -> List[WorkbookItem]:
    config = config or load_config()
    migrate_old_export_config(config)
    names = [x.strip() for x in config["WORKBOOKS"].get("items", "").split(",") if x.strip()]
    result: List[WorkbookItem] = []
    for key in names:
        section = f"WORKBOOK:{key}"
        if section not in config:
            continue
        sec = config[section]
        item = WorkbookItem(
            key=key,
            path=sec.get("path", "").strip(),
            display_name=sec.get("display_name", "").strip() or key,
            enabled=as_bool(sec.get("enabled", "true"), True),
        )
        if only_enabled and not item.enabled:
            continue
        if item.path:
            result.append(item)
    return result


def get_export_items(config: Optional[configparser.ConfigParser] = None, only_enabled: bool = True) -> List[ExportItem]:
    config = config or load_config()
    migrate_old_export_config(config)
    names = [x.strip() for x in config["EXPORTS"].get("items", "").split(",") if x.strip()]
    items: List[ExportItem] = []
    for name in names:
        section = f"EXPORT:{name}"
        if section not in config:
            continue
        sec = config[section]
        item = ExportItem(
            name=name,
            workbook=sec.get("workbook", "").strip(),
            sheet=sec.get("sheet", "").strip(),
            cell_range=sec.get("range", "").strip(),
            filename=sec.get("file", "").strip(),
            delete_by_start=as_bool(sec.get("delete_by_start", "false")),
            enabled=as_bool(sec.get("enabled", "true"), True),
            send_enabled=as_bool(sec.get("send_enabled", "true"), True),
        )
        if only_enabled and not item.enabled:
            continue
        if item.workbook and item.sheet and item.cell_range and item.filename:
            items.append(item)
    return items


def wait_excel(excel, is_running: Optional[RunFunc], write: LogFunc, timeout: int = 120) -> None:
    start = time.time()
    last_log = time.time()
    while True:
        if time.time() - start > timeout:
            raise Exception("Excel timeout")
        if is_running and not is_running():
            return
        if time.time() - last_log > 5:
            write("Waiting Excel...")
            last_log = time.time()
        try:
            if excel_call(lambda: excel.CalculateState, write, "check calculation state", timeout=30, delay=0.5) == 0:
                break
        except Exception as error:
            if _is_excel_busy_error(error) or "Excel busy timeout during check calculation state" in str(error):
                continue
            else:
                break
        time.sleep(0.5)


def _resolve_save_dir(*args, save_dir: Optional[str] = None) -> str:
    # Backward compatible with run_create(auto_file, dws_file, save_dir, ...)
    if save_dir:
        return save_dir
    if len(args) == 1:
        return args[0]
    if len(args) >= 3:
        return args[2]
    raise Exception("Missing output folder")


def _pump_excel_messages(seconds: float = 0.5) -> None:
    """Give Excel/Windows clipboard time to finish rendering CopyPicture."""
    end = time.time() + max(0, seconds)
    while time.time() < end:
        try:
            pythoncom.PumpWaitingMessages()
        except Exception:
            pass
        time.sleep(0.05)


def _is_excel_busy_error(error: Exception) -> bool:
    text = str(error).lower()
    if "call was rejected by callee" in text:
        return True
    if "rejected by callee" in text:
        return True
    if "-2147418111" in text or "-2147417846" in text:
        return True
    return False


def excel_call(action: Callable[[], Any], write: Optional[LogFunc], label: str, timeout: int = 180, delay: float = 0.7) -> Any:
    start = time.time()
    last_log = 0.0
    attempt = 0
    while True:
        if time.time() - start > timeout:
            raise Exception(f"Excel busy timeout during {label}")
        attempt += 1
        try:
            return action()
        except Exception as error:
            if not _is_excel_busy_error(error):
                raise
            now = time.time()
            if write and now - last_log >= 5:
                write(f"[WAIT EXCEL] {label} busy retry {attempt}")
                last_log = now
            _pump_excel_messages(delay)




def _move_excel_offscreen(excel) -> None:
    """Keep Excel renderable for CopyPicture, but hide it from the user's screen.

    Excel CopyPicture may export blank images when Application.Visible=False or
    when the window is minimized.  The safer compromise is: Visible=True,
    WindowState=Normal, then move the Excel window far outside the visible
    desktop. Excel still has a real window to render from, but the user does
    not see it popping up.
    """
    try:
        excel.Visible = True
    except Exception:
        pass
    try:
        excel.WindowState = 2  # xlNormal
    except Exception:
        pass
    try:
        excel.Left = -32000
        excel.Top = -32000
        excel.Width = 800
        excel.Height = 600
    except Exception:
        pass
    _pump_excel_messages(0.2)

def _is_probably_blank_image(path: str) -> bool:
    """Return True when Excel exported a mostly-white/blank image.

    This uses Pillow only when available. If Pillow is not installed, the export
    is accepted and the log still shows file size for manual checking.
    """
    if not os.path.exists(path):
        return True

    try:
        # Very small Excel chart exports are commonly blank.
        if os.path.getsize(path) < 10 * 1024:
            return True
    except Exception:
        pass

    try:
        from PIL import Image, ImageStat

        with Image.open(path) as img:
            img = img.convert("RGB")
            # Sample instead of scanning a huge screenshot.
            img.thumbnail((96, 96))
            stat = ImageStat.Stat(img)
            mean = sum(stat.mean) / 3
            variance = sum(stat.var) / 3

            # Blank Excel exports are usually near-white with almost no variance.
            return mean > 246 and variance < 45
    except Exception:
        # Pillow unavailable or failed. Do not block the run.
        return False


def _safe_delete(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def run_create(*args, save_dir: Optional[str] = None, log: Optional[LogFunc] = None, is_running: Optional[RunFunc] = None) -> None:
    """Workbook Manager version compatible with bot_main_war_room_v7.

    Important fix:
    - Excel stays renderable for CopyPicture but is moved off-screen, so it
      works in the background without popping up over the user.
    - Every export is retried and checked for blank output before it is accepted.
    - The public function signature is unchanged, so bot_main can keep calling:
      run_create(out, log=..., is_running=...)
    """
    def write(msg: str) -> None:
        log(msg) if log else print(msg)

    def keep_running() -> bool:
        return True if is_running is None else bool(is_running())

    out_dir = _resolve_save_dir(*args, save_dir=save_dir)
    os.makedirs(out_dir, exist_ok=True)

    config = load_config()
    migrate_old_export_config(config)
    save_config(config)

    workbooks = get_workbooks(config, only_enabled=True)
    items = get_export_items(config, only_enabled=True)
    if not workbooks:
        raise Exception("No Excel files in Workbook Manager")
    if not items:
        raise Exception("No enabled export sheets")

    start_hour = int(get_time_config().get("start_hour", 15))
    pythoncom.CoInitialize()

    excel: Any = None
    excel_pid: Optional[int] = None
    opened: Dict[str, Optional[Any]] = {}

    try:
        for i in range(3):
            try:
                excel = win32.DispatchEx("Excel.Application")
                excel.DisplayAlerts = False
                excel_pid = get_excel_pid(excel)

                # Do not use excel.Visible=False or minimized mode here.
                # CopyPicture can export a fully white image without a real
                # renderable Excel window. We keep Excel renderable, then move
                # it off-screen so it behaves like a background process.
                excel.Visible = True
                excel.ScreenUpdating = True
                excel.EnableEvents = False
                _move_excel_offscreen(excel)
                break
            except Exception as e:
                write(f"Excel start failed ({i + 1}/3): {e}")
                time.sleep(2)

        if not excel:
            raise Exception("Excel failed to start")

        excel_app = cast(Any, excel)

        def export_range_as_image(wb, item: ExportItem, rng: str) -> None:
            workbook = cast(Any, wb)
            ws = cast(Any, excel_call(lambda: workbook.Worksheets(item.sheet), write, f"open export sheet {item.sheet}", timeout=180))
            target = cast(Any, excel_call(lambda: ws.Range(rng), write, f"select export range {item.sheet}!{rng}", timeout=180))

            _move_excel_offscreen(excel_app)
            excel_call(lambda: ws.Activate(), write, f"activate sheet {item.sheet}", timeout=120)
            excel_call(lambda: target.Select(), write, f"select range {item.sheet}!{rng}", timeout=120)
            excel_app.ScreenUpdating = True

            try:
                active_window = cast(Any, excel_call(lambda: excel_app.ActiveWindow, write, "get active window", timeout=60))
                excel_call(lambda: setattr(active_window, "Zoom", 100), write, "set zoom", timeout=60)
                target_row = excel_call(lambda: target.Row, write, "get range row", timeout=60)
                target_col = excel_call(lambda: target.Column, write, "get range column", timeout=60)
                excel_call(lambda: setattr(active_window, "ScrollRow", max(1, target_row)), write, "set scroll row", timeout=60)
                excel_call(lambda: setattr(active_window, "ScrollColumn", max(1, target_col)), write, "set scroll column", timeout=60)
            except Exception:
                pass

            try:
                excel_call(lambda: setattr(ws.Cells.Font, "Name", "Microsoft YaHei"), write, f"set font {item.sheet}", timeout=60)
            except Exception:
                pass

            try:
                excel_call(lambda: excel_app.CalculateFull(), write, "calculate full", timeout=240)
                excel_call(lambda: ws.Calculate(), write, f"calculate sheet {item.sheet}", timeout=180)
            except Exception:
                pass

            _pump_excel_messages(1.0)

            path = os.path.join(out_dir, item.filename)
            base, ext = os.path.splitext(path)
            if not ext:
                ext = ".png"
                path = base + ext

            tmp_path = base + ".__tmp_export" + ext
            _safe_delete(path)
            _safe_delete(tmp_path)

            copy_modes = [
                (1, 2, "screen-picture"),
                (2, 2, "printer-picture"),
                (1, 1, "screen-bitmap"),
                (2, 1, "printer-bitmap"),
            ]

            last_error = None

            for attempt in range(1, 7):
                if not keep_running():
                    return

                appearance, fmt, mode_name = copy_modes[(attempt - 1) % len(copy_modes)]
                chart: Any = None

                try:
                    write(f"[EXPORT] {item.name}: {item.sheet}!{rng} attempt {attempt} ({mode_name})")

                    _move_excel_offscreen(excel_app)
                    excel_call(lambda: ws.Activate(), write, f"activate sheet {item.sheet}", timeout=120)
                    excel_call(lambda: target.Select(), write, f"select range {item.sheet}!{rng}", timeout=120)
                    _pump_excel_messages(0.4)

                    excel_call(lambda a=appearance, f=fmt: target.CopyPicture(Appearance=a, Format=f), write, f"copy picture {item.name}", timeout=180)
                    _pump_excel_messages(0.8)

                    target_left = excel_call(lambda: target.Left, write, "get target left", timeout=60)
                    target_top = excel_call(lambda: target.Top, write, "get target top", timeout=60)
                    target_width = excel_call(lambda: target.Width, write, "get target width", timeout=60)
                    target_height = excel_call(lambda: target.Height, write, "get target height", timeout=60)
                    chart_objects = cast(Any, excel_call(lambda: ws.ChartObjects(), write, f"get chart objects {item.sheet}", timeout=120))
                    chart = cast(Any, excel_call(
                        lambda: chart_objects.Add(
                            target_left,
                            target_top,
                            max(float(target_width) + 8, 120),
                            max(float(target_height) + 8, 80),
                        ),
                        write,
                        f"create chart {item.name}",
                        timeout=180,
                    ))
                    excel_call(lambda: chart.Activate(), write, f"activate chart {item.name}", timeout=120)
                    _pump_excel_messages(0.3)

                    excel_call(lambda: chart.Chart.Paste(), write, f"paste chart {item.name}", timeout=180)
                    _pump_excel_messages(0.8)

                    try:
                        shape_count = excel_call(lambda: chart.Chart.Shapes.Count, write, f"count chart shapes {item.name}", timeout=60)
                    except Exception:
                        shape_count = 1

                    if shape_count < 1:
                        raise Exception("Paste produced 0 chart shapes")

                    try:
                        excel_call(lambda: setattr(chart.Chart.ChartArea.Border, "LineStyle", 0), write, f"clear chart border {item.name}", timeout=60)
                    except Exception:
                        pass

                    ok = excel_call(lambda: chart.Chart.Export(tmp_path, "PNG"), write, f"export chart {item.name}", timeout=180)
                    _pump_excel_messages(0.4)

                    if ok is False or not os.path.exists(tmp_path):
                        raise Exception("Chart.Export returned no file")

                    if _is_probably_blank_image(tmp_path):
                        raise Exception(f"blank/white image detected ({os.path.getsize(tmp_path)} bytes)")

                    os.replace(tmp_path, path)
                    write(f"[OK] {path}")
                    return

                except Exception as e:
                    last_error = e
                    _safe_delete(tmp_path)
                    write(f"[WARN] Export retry {attempt}/6 failed: {item.name} -> {e}")
                    _pump_excel_messages(0.8)

                finally:
                    try:
                        if chart is not None:
                            target_chart = chart
                            excel_call(lambda: target_chart.Delete(), write, f"delete temp chart {item.name}", timeout=60)
                    except Exception:
                        pass

            raise Exception(f"Export failed after retries: {item.name} {item.sheet}!{rng} | last error: {last_error}")

        for wb_item in workbooks:
            group = [x for x in items if x.workbook == wb_item.key]
            if not group:
                continue
            if not keep_running():
                return
            if not wb_item.path or not os.path.exists(wb_item.path):
                raise Exception(f"Workbook not found: {wb_item.display_name} -> {wb_item.path}")

            write(f"[OPEN] {wb_item.display_name}")

            wb = cast(Any, excel_call(
                lambda: excel_app.Workbooks.Open(
                    wb_item.path,
                    UpdateLinks=0,
                    ReadOnly=False,
                    IgnoreReadOnlyRecommended=True,
                ),
                write,
                f"open workbook {wb_item.display_name}",
                timeout=240,
            ))
            _move_excel_offscreen(excel_app)
            opened[wb_item.key] = wb
            hide_excel_from_taskbar()
            update_excel_date(wb, start_hour, log=write)

            excel_call(lambda: setattr(wb, "Saved", True), write, f"mark workbook saved {wb_item.display_name}", timeout=60)
            excel_call(lambda target=wb: getattr(target, "RefreshAll")(), write, f"refresh query {wb_item.display_name}", timeout=240)

            try:
                excel_call(lambda: excel_app.CalculateUntilAsyncQueriesDone(), write, f"wait async query {wb_item.display_name}", timeout=900)
            except Exception:
                pass

            wait_excel(excel_app, keep_running, write, timeout=900)
            _pump_excel_messages(1.0)

            delete_sheets = [x.sheet for x in group if x.delete_by_start]
            deleted_cols = delete_columns_by_start(wb, start_hour, delete_sheets, log=write) if delete_sheets else 0
            wait_excel(excel_app, keep_running, write, timeout=300)
            _pump_excel_messages(1.0)

            for item in group:
                if not keep_running():
                    return

                rng = shift_range_left(item.cell_range, deleted_cols) if item.delete_by_start else item.cell_range
                if not item.delete_by_start:
                    write(f"[SKIP DELETE] {item.name}")

                export_range_as_image(wb, item, rng)

            time.sleep(0.5)
            excel_call(lambda target=wb: getattr(target, "Close")(False), write, f"close workbook {wb_item.display_name}", timeout=180)
            opened[wb_item.key] = None

    finally:
        for wb in list(opened.values()):
            try:
                if wb:
                    excel_call(lambda target=wb: getattr(target, "Close")(False), write, "close workbook cleanup", timeout=120)
            except Exception:
                pass

        try:
            if excel:
                time.sleep(0.5)
                excel_call(lambda: excel.Quit(), write, "quit excel", timeout=120)
        except Exception:
            pass

        # ปล่อย COM reference ก่อน เพื่อให้ Quit() ปิดได้สมบูรณ์
        excel = None
        gc.collect()
        pythoncom.CoUninitialize()

        # Safety net: ถ้า Excel instance ของบอทยังค้าง (Quit ไม่หมด/hang)
        # ค่อย force-close เฉพาะ PID นั้น — ไม่แตะ Excel ตัวอื่นของผู้ใช้
        time.sleep(0.5)
        kill_excel_pid_if_alive(excel_pid, write)


if __name__ == "__main__":
    # New usage: python Createphoto.py "C:/output"
    # Old usage still accepted: python Createphoto.py auto.xlsx dws.xlsx "C:/output"
    if len(sys.argv) == 2:
        run_create(sys.argv[1])
    else:
        run_create(*sys.argv[1:4])
