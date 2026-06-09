import os
import sys
import time
import re
import gc
import configparser
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Dict

import pythoncom
import win32com.client as win32

import win32gui
import win32con
import win32process

LogFunc = Optional[Callable[[str], None]]
RunFunc = Optional[Callable[[], bool]]

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
    with open(resource_path("config.ini"), "w", encoding="utf-8") as f:
        config.write(f)


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


def update_excel_date(wb, start_hour: int, log: LogFunc = None) -> None:
    try:
        ws = wb.Worksheets(1)
        date_str = get_business_date(start_hour)
        ws.Range("A2").Value = date_str
        if log:
            log(f"[DATE SET] A2 = {date_str}")
    except Exception as e:
        if log:
            log(f"[ERROR] Update date failed: {e}")
        else:
            print(e)


def delete_columns_by_start(wb, start_hour: int, target_sheets: List[str], log: LogFunc = None) -> int:
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
            ws = wb.Worksheets(sheet)
            for col in range(delete_until - 1, 2, -1):
                ws.Columns(col).Delete(Shift=-4159)
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
    old = config["EXPORT"] if "EXPORT" in config else {}
    fallback = {
        "AUTO_SHEET": "Autoformat", "AUTO_RANGE": "A1:AK39", "AUTO_FILE": "AUTOREALTIME.png",
        "DWSREALTIME_SHEET": "DWSREALTIME", "DWSREALTIME_RANGE": "A1:AE16", "DWSREALTIME_FILE": "DWSREALTIME.png",
        "AUTO_PDA_SHEET": "AUTO PDA", "AUTO_PDA_RANGE": "A1:AC30", "AUTO_PDA_FILE": "AUTO_PDA.png",
        "DWS_PDA_SHEET": "DWS PDA", "DWS_PDA_RANGE": "A1:AC15", "DWS_PDA_FILE": "DWS_PDA.png",
        "REALTIME_DB_SHEET": "Sheet1", "REALTIME_DB_RANGE": "A1:Z50", "REALTIME_DB_FILE": "REALTIME_DB.png",
    }

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
            "sheet": old.get(sheet_key, fallback[sheet_key]) if hasattr(old, "get") else fallback[sheet_key],
            "range": old.get(range_key, fallback[range_key]) if hasattr(old, "get") else fallback[range_key],
            "file": old.get(file_key, fallback[file_key]) if hasattr(old, "get") else fallback[file_key],
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


def wait_excel(excel, is_running: RunFunc, write: Callable[[str], None], timeout: int = 120) -> None:
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
            if excel.CalculateState == 0:
                break
        except Exception:
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


def run_create(*args, save_dir: Optional[str] = None, log: LogFunc = None, is_running: RunFunc = None) -> None:
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

    excel = None
    opened: Dict[str, object] = {}

    try:
        for i in range(3):
            try:
                excel = win32.DispatchEx("Excel.Application")
                excel.DisplayAlerts = False

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

        def export_range_as_image(wb, item: ExportItem, rng: str) -> None:
            ws = wb.Worksheets(item.sheet)
            target = ws.Range(rng)

            _move_excel_offscreen(excel)
            ws.Activate()
            target.Select()
            excel.ScreenUpdating = True

            try:
                excel.ActiveWindow.Zoom = 100
                excel.ActiveWindow.ScrollRow = max(1, target.Row)
                excel.ActiveWindow.ScrollColumn = max(1, target.Column)
            except Exception:
                pass

            try:
                ws.Cells.Font.Name = "Microsoft YaHei"
            except Exception:
                pass

            # Make sure formulas and UI drawing have settled.
            try:
                excel.CalculateFull()
                ws.Calculate()
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

            # Try multiple CopyPicture modes. Some sheets/ranges work better
            # with screen appearance, some with printer appearance.
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
                chart = None

                try:
                    write(f"[EXPORT] {item.name}: {item.sheet}!{rng} attempt {attempt} ({mode_name})")

                    _move_excel_offscreen(excel)
                    ws.Activate()
                    target.Select()
                    _pump_excel_messages(0.4)

                    target.CopyPicture(Appearance=appearance, Format=fmt)
                    _pump_excel_messages(0.8)

                    # Put the chart near the selected range, not at 0,0.
                    # Some Excel builds export blank charts when the temporary
                    # chart is off-screen or created before clipboard render.
                    chart = ws.ChartObjects().Add(
                        target.Left,
                        target.Top,
                        max(float(target.Width) + 8, 120),
                        max(float(target.Height) + 8, 80),
                    )
                    chart.Activate()
                    _pump_excel_messages(0.3)

                    chart.Chart.Paste()
                    _pump_excel_messages(0.8)

                    try:
                        shape_count = chart.Chart.Shapes.Count
                    except Exception:
                        shape_count = 1

                    if shape_count < 1:
                        raise Exception("Paste produced 0 chart shapes")

                    # Keep chart border/background from affecting the exported range.
                    try:
                        chart.Chart.ChartArea.Border.LineStyle = 0
                    except Exception:
                        pass

                    ok = chart.Chart.Export(tmp_path, "PNG")
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
                            chart.Delete()
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

            wb = excel.Workbooks.Open(
                wb_item.path,
                UpdateLinks=0,
                ReadOnly=False,
                IgnoreReadOnlyRecommended=True,
            )
            _move_excel_offscreen(excel)
            opened[wb_item.key] = wb
            hide_excel_from_taskbar()
            update_excel_date(wb, start_hour, log=write)

            delete_sheets = [x.sheet for x in group if x.delete_by_start]
            deleted_cols = delete_columns_by_start(wb, start_hour, delete_sheets, log=write) if delete_sheets else 0

            wb.Saved = True
            wb.RefreshAll()

            try:
                excel.CalculateUntilAsyncQueriesDone()
            except Exception:
                pass

            wait_excel(excel, keep_running, write, timeout=180)
            _pump_excel_messages(1.0)

            for item in group:
                if not keep_running():
                    return

                rng = shift_range_left(item.cell_range, deleted_cols) if item.delete_by_start else item.cell_range
                if not item.delete_by_start:
                    write(f"[SKIP DELETE] {item.name}")

                export_range_as_image(wb, item, rng)

            time.sleep(0.5)
            wb.Close(False)
            opened[wb_item.key] = None

    finally:
        for wb in list(opened.values()):
            try:
                if wb:
                    wb.Close(False)
            except Exception:
                pass

        try:
            if excel:
                time.sleep(0.5)
                excel.Quit()
        except Exception:
            pass

        gc.collect()
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    # New usage: python Createphoto.py "C:/output"
    # Old usage still accepted: python Createphoto.py auto.xlsx dws.xlsx "C:/output"
    if len(sys.argv) == 2:
        run_create(sys.argv[1])
    else:
        run_create(*sys.argv[1:4])
