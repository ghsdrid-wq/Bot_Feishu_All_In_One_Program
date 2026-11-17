"""Smoke test of VIP AWB: the page, its config round-trip and the BOT REPORT pipeline stage.

Uses an isolated config and a fake vip_awb.run: nothing is sent to JMS or Feishu.
    python tools/test_vip_awb_ui.py [screenshot-vip.png] [screenshot-report.png]
"""

import configparser
import logging
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot_main
import vip_awb
from core.ini_store import write_ini
from tools.profile_ui_responsiveness import ProfileApp


class FakeData:
    period_label = "17/11 12:00 – 18/11 11:59"
    until = datetime(2026, 11, 18, 2)
    grand_total = 43

    def group_total(self, group):
        return {"สแกนออกแล้ว": 36, "ค้างคลัง": 7}.get(group, 0)


def grab(app, path):
    from PIL import ImageGrab
    app.lift()
    app.update()
    time.sleep(0.5)
    x, y = app.winfo_rootx(), app.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height())).save(path)


def main():
    shots = sys.argv[1:]
    calls = []

    def fake_run(cfg, token, base_dir, now=None, **kw):
        calls.append((now, kw))
        logging.getLogger("vip_awb").info("VIP AWB ค้นแหล่งที่มาแล้ว 1/2 เลข (50%) — test progress")
        time.sleep(1.0)  # long enough for a pipeline reset to happen while the job is still running
        return {"data": FakeData(), "text": "", "png": None, "xlsx": None, "sent": kw.get("send", False),
                "skipped": ""}

    ini = None
    with (
        tempfile.TemporaryDirectory() as tmp,
        mock.patch.object(bot_main, "CONFIG_FILE", str(Path(tmp) / "config.ini")),
        mock.patch.object(bot_main, "save_config",
                          side_effect=lambda cfg: write_ini(str(Path(tmp) / "config.ini"), cfg)),
        mock.patch.object(bot_main.App, "bots_autostart"),
        mock.patch.object(vip_awb, "run", side_effect=fake_run),
    ):
        ini = Path(tmp) / "config.ini"
        app = ProfileApp()
        app.geometry("1280x900+0+0")
        try:
            logged = []
            real_write_log = app.write_log
            app.write_log = lambda msg, *a, **k: (logged.append(str(msg)), real_write_log(msg, *a, **k))
            # page + defaults
            assert "vip_awb" in app.get_nav_order(), app.get_nav_order()
            app.show_page("vip_awb")
            app.update()
            sec = app.config["VIP_AWB"]
            assert "ADIDAS = ADIDAS" in sec["brands"] and "enabled" not in sec
            assert list(vip_awb.load_settings(app.config).keywords)[:2] == ["UNIQLO", "ADIDAS"]
            if shots:
                grab(app, shots[0])

            # edit and save through the page
            app.vip_brands_box.delete("1.0", "end")
            app.vip_brands_box.insert("1.0", "ADIDAS = ADIDAS, VIP111111383\n\n  XIAOMI = XIAOMI \n")
            app.vip_entries["chat_id"].delete(0, "end")
            app.vip_entries["chat_id"].insert(0, "oc_test")
            app.save_config()
            saved = configparser.RawConfigParser()
            saved.read(ini, encoding="utf-8-sig")
            cfg = vip_awb.load_settings(saved)
            assert cfg.chat_id == "oc_test" and list(cfg.keywords) == ["ADIDAS", "XIAOMI"], cfg

            # stage card on BOT REPORT: present, unticked by default, tick is saved
            assert "vip_awb" in app.pipeline_widgets and app.step_vars["vip_awb"].get() is False
            assert app.selected_steps()["vip_awb"] is False
            app.step_vars["vip_awb"].set(True)
            app.save_config()
            saved.read(ini, encoding="utf-8-sig")
            assert saved.get("DWS_JMS", "step_vip_awb") == "true"
            assert app.selected_steps()["vip_awb"] is True
            app.show_page("home")
            app.update()
            if len(shots) > 1:
                grab(app, shots[1])

            # pipeline hook: Auto -> data until now (cycle end is tomorrow noon), sent once per hour (no force)
            fixed = datetime(2026, 11, 18, 2, 8)

            class FrozenDatetime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return fixed

            # the worker updates widgets through after(); Tk accepts that only while mainloop runs
            with mock.patch.object(bot_main, "datetime", FrozenDatetime):
                app.mode = "auto"
                app.after(0, lambda: app.start_vip_awb_from_pipeline(datetime(2026, 11, 18, 12)))
                app.after(2500, app.quit)
                app.mainloop()
                assert len(calls) == 1, calls
                now, kw = calls[-1]
                assert now == fixed and kw["send"] is True and kw["force"] is False and kw["chat_id"] == "oc_test", calls
                # Run Now over yesterday's range -> closing report of that range, resend allowed
                app.mode = "manual"
                app.after(0, lambda: app.start_vip_awb_from_pipeline("2026-11-17 12:00:00"))
                app.after(2500, app.quit)
                app.mainloop()
                now, kw = calls[-1]
                assert now == datetime(2026, 11, 17, 12) and kw["force"] is True, calls
            status = app.vip_status.cget("text")
            assert status.startswith("ส่งแล้ว"), status
            # progress lines of the vip_awb logger reach Live Log
            assert any("test progress" in m for m in logged), logged[-5:]
            # a pipeline reset while the background job runs keeps the stage on "run"
            app.vip_busy = True
            app.active_run_generation = None
            with mock.patch.object(app, "is_run_generation_active", return_value=True):
                app.schedule_pipeline_reset()
                app.after(1800, app.quit)
                app.mainloop()
            running = app.pipeline_widgets["vip_awb"]["status"].cget("text")
            app.vip_busy = False
            assert running not in ("READY", ""), running
            assert app.pipeline_widgets["vip_awb"]["status"].cget("text") != "", "stage state missing"
            print("VIP AWB UI test OK:", status, "| stage:", app.pipeline_widgets["vip_awb"]["status"].cget("text"))
        finally:
            app.destroy()


if __name__ == "__main__":
    main()
