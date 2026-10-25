"""Exercise the real Tk calculation-mode switch with an isolated INI file."""

import configparser
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bot_main
from core.ini_store import write_ini
from tools.profile_ui_responsiveness import ProfileApp


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.ini"
        with (
            mock.patch.object(bot_main, "CONFIG_FILE", str(path)),
            mock.patch.object(
                bot_main,
                "save_config",
                side_effect=lambda config: write_ini(str(path), config),
            ),
        ):
            app = ProfileApp()
            app.geometry("1280x900+10000+10000")
            try:
                app.show_page("workbooks")
                app.update()
                switch = next(
                    widget
                    for widget in descendants(app)
                    if isinstance(widget, bot_main.ctk.CTkSwitch)
                    and widget.cget("text") == "คำนวณรายงานด้วยโปรแกรม"
                )
                assert app.report_calculation_var.get()
                switch.toggle()
                app.update()
                saved = configparser.RawConfigParser()
                saved.read(path, encoding="utf-8")
                assert saved.getboolean("REPORT_CALCULATION", "enabled") is False
                switch.toggle()
                app.update()
                saved.read(path, encoding="utf-8")
                assert saved.getboolean("REPORT_CALCULATION", "enabled") is True
                assert switch.winfo_reqwidth() <= switch.master.winfo_width()
                app.show_page("sources")
                app.update()
                assert "sources" in app.nav_buttons
                assert len([r for r in app.controller_client_rows if "source_entry" in r]) == 8
                app.auto_cache_entry.delete(0, "end")
                app.auto_cache_entry.insert(0, str(Path(tmp) / "auto-cache"))
                app.save_all()
                saved.read(path, encoding="utf-8")
                assert saved.get("REPORT_CALCULATION", "auto_cache") == str(Path(tmp) / "auto-cache")
                assert app.autopack_dir_entry.winfo_width() > 10
                print("Source page navigation, eight DWS sources and cache persistence: passed")
                print("Real Tk switch off/on and INI persistence: passed")
            finally:
                app.worker_running = False
                app.destroy()


if __name__ == "__main__":
    main()
