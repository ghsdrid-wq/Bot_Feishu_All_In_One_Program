from __future__ import annotations

import json
import argparse
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot_main  # noqa: E402


class ProfileApp(bot_main.App):
    """Run the real desktop UI without opening network listeners."""

    def load_dynamic_plans(self) -> None:
        self.dynamic_plans = ["DWSA", "DWSB"]

    def start_controller_api(self) -> None:
        return None

    def dashboard_autostart_web(self) -> None:
        return None

    def refresh_status(self) -> None:
        return None


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--visible", action="store_true")
    args = parser.parse_args()
    started = time.perf_counter()
    app = ProfileApp()
    startup_ms = (time.perf_counter() - started) * 1000
    if not args.visible:
        app.withdraw()

    interval_s = 1 / 60
    tick_delays: list[float] = []
    wheel_handler_times: list[float] = []
    wheel_moves: list[float] = []
    scroll_positions: list[float] = []
    previous_tick = time.perf_counter()
    app.show_page("settings")
    app.update_idletasks()
    settings_page = app.pages["settings"]
    wheel_delta = -120

    def tick() -> None:
        nonlocal previous_tick
        now = time.perf_counter()
        tick_delays.append(max(0.0, now - previous_tick - interval_s) * 1000)
        previous_tick = now
        app.after(16, tick)

    def scroll_settings() -> None:
        nonlocal wheel_delta
        first, last = settings_page._parent_canvas.yview()
        if last >= 0.995:
            wheel_delta = 120
        elif first <= 0.005:
            wheel_delta = -120
        event = SimpleNamespace(
            widget=settings_page._parent_canvas,
            delta=wheel_delta,
        )
        before = time.perf_counter()
        settings_page._mouse_wheel_all(event)
        wheel_handler_times.append((time.perf_counter() - before) * 1000)
        wheel_moves.append(abs(settings_page._parent_canvas.yview()[0] - first))
        scroll_positions.append(settings_page._parent_canvas.yview()[0])
        app.after(30, scroll_settings)

    def finish() -> None:
        result = {
            "startup_ms": round(startup_ms, 2),
            "tick_samples": len(tick_delays),
            "tick_delay_ms_p50": round(statistics.median(tick_delays), 2),
            "tick_delay_ms_p95": round(percentile(tick_delays, 0.95), 2),
            "tick_delay_ms_max": round(max(tick_delays, default=0.0), 2),
            "wheel_handler_ms_p50": round(statistics.median(wheel_handler_times), 2),
            "wheel_handler_ms_p95": round(percentile(wheel_handler_times, 0.95), 2),
            "wheel_handler_ms_max": round(max(wheel_handler_times, default=0.0), 2),
            "wheel_move_fraction_p50": round(statistics.median(wheel_moves), 4),
            "scroll_position_min": round(min(scroll_positions, default=0.0), 4),
            "scroll_position_max": round(max(scroll_positions, default=0.0), 4),
            "tick_over_33ms": sum(value > 33 for value in tick_delays),
        }
        print(json.dumps(result, ensure_ascii=False))
        app.destroy()

    app.after(16, tick)
    app.after(120, scroll_settings)
    app.after(4000, finish)
    app.mainloop()


if __name__ == "__main__":
    main()
