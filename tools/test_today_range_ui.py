"""Click both real preset buttons using isolated config and fixed calendar dates."""

import configparser
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot_main
from core.ini_store import write_ini
from tools.profile_ui_responsiveness import ProfileApp


def main():
    current = datetime(2026, 12, 31, 8)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return current

    with (
        tempfile.TemporaryDirectory() as tmp,
        mock.patch.object(bot_main, "CONFIG_FILE", str(Path(tmp) / "config.ini")),
        mock.patch.object(
            bot_main,
            "save_config",
            side_effect=lambda cfg: write_ini(str(Path(tmp) / "config.ini"), cfg),
        ),
        mock.patch.object(bot_main.App, "bots_autostart"),
        mock.patch.object(bot_main, "datetime", FrozenDatetime),
    ):
        app = ProfileApp()
        app.geometry("1280x900+10000+10000")
        try:
            for current in (
                datetime(2026, 12, 31, 8),
                datetime(2027, 1, 1, 18),
                datetime(2028, 2, 28, 18),
                datetime(2028, 2, 29, 8),
                datetime(2028, 3, 1, 18),
            ):
                for offset, label in ((0, "วันนี้"), (-1, "เมื่อวาน")):
                    scheduler_button = (
                        app.btn_today_scheduler
                        if offset == 0
                        else app.btn_yesterday_scheduler
                    )
                    summary_button = (
                        app.btn_today_summary
                        if offset == 0
                        else app.btn_yesterday_summary
                    )
                    assert (
                        scheduler_button.cget("text")
                        == summary_button.cget("text")
                        == label
                    )
                    app.show_page("home")
                    app.update()
                    summary_before = (
                        app.ds_scope_start_date.get_date(),
                        app.ds_scope_end_date.get_date(),
                        app.ds_scope_start_hour.get(),
                        app.ds_scope_end_hour.get(),
                    )
                    scheduler_button.invoke()
                    app.update()
                    expected_start = current.date() + timedelta(days=offset)
                    expected = (expected_start, expected_start + timedelta(days=1))
                    assert (
                        app.start_date.get_date(),
                        app.end_date.get_date(),
                    ) == expected
                    assert (app.start_hour_var.get(), app.end_hour_var.get()) == (
                        "12:00",
                        "12:00",
                    )
                    assert app._dates_user_selected and app._manual_dates == expected
                    assert summary_before == (
                        app.ds_scope_start_date.get_date(),
                        app.ds_scope_end_date.get_date(),
                        app.ds_scope_start_hour.get(),
                        app.ds_scope_end_hour.get(),
                    )
                    saved = configparser.RawConfigParser()
                    saved.read(Path(tmp) / "config.ini", encoding="utf-8")
                    assert (
                        saved.get("TIME", "start_hour")
                        == saved.get("TIME", "end_hour")
                        == "12"
                    )
                    scheduler_before = (
                        app.start_date.get_date(),
                        app.end_date.get_date(),
                    )
                    app.show_page("daily_summary")
                    app.update()
                    summary_button.invoke()
                    app.update()
                    assert (
                        app.ds_scope_start_date.get_date(),
                        app.ds_scope_end_date.get_date(),
                    ) == expected
                    assert (
                        app.ds_scope_start_hour.get(),
                        app.ds_scope_end_hour.get(),
                    ) == ("12:00", "12:00")
                    assert (
                        app.start_date.get_date(),
                        app.end_date.get_date(),
                    ) == scheduler_before
                    for button in (scheduler_button, summary_button):
                        assert (
                            button.winfo_x() + button.winfo_width()
                            <= button.master.winfo_width()
                        )
                        assert (
                            button.master.winfo_x() + button.master.winfo_width()
                            <= button.master.master.winfo_width()
                        )
                    assert (
                        not app.running
                        and not app.scheduler_running
                        and not app.ds_busy
                    )
            app.runtime_locked = True
            app.start_hour_var.set("16:00")
            app.set_today_range("scheduler", -1)
            assert app.start_hour_var.get() == "16:00"
            print(
                "All four date preset buttons: labels, year/leap boundaries, noon hours, page isolation, INI, layout and runtime lock passed"
            )
        finally:
            app.worker_running = False
            app.destroy()


if __name__ == "__main__":
    main()
