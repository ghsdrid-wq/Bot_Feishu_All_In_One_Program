"""Durable final-hour/summary ordering; uncertain sends require operator review."""

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

_lock = threading.RLock()


class DeliveryLedger:
    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.remove(temp)

    def hourly_delivered(self, window, destination):
        if window.cutoff != window.end:
            raise ValueError("Cannot release a summary before the cycle end")
        with _lock:
            data = self.read()
            data.setdefault(
                window.key,
                {
                    "start": window.start.isoformat(),
                    "end": window.end.isoformat(),
                    "destination": destination,
                    "phase": "ready",
                    "attempts": 0,
                },
            )
            if data[window.key]["phase"] == "awaiting_hourly":
                data[window.key].update(phase="ready", destination=destination)
            self.save(data)

    def remember_cycle(self, window, destination):
        with _lock:
            data = self.read()
            data.setdefault(
                window.key,
                {
                    "start": window.start.isoformat(),
                    "end": window.end.isoformat(),
                    "destination": destination,
                    "phase": "awaiting_hourly",
                    "attempts": 0,
                },
            )
            self.save(data)

    def release_overdue(self, now, grace=timedelta(minutes=45), max_age=timedelta(hours=6)):
        """Release cycles whose final hourly run never completed (Auto stopped, PC off, crash).

        The summary normally follows the final hourly delivery.  If that delivery
        has not happened ``grace`` after the cycle end, send the summary anyway so
        a cycle is never silently skipped.  Cycles that ended more than ``max_age``
        ago are left alone so stale entries never trigger a burst of old summaries.
        Returns the released cycles.
        """
        released = []
        with _lock:
            data = self.read()
            for item in data.values():
                end = datetime.fromisoformat(item["end"])
                if item["phase"] == "awaiting_hourly" and end + grace <= now <= end + max_age:
                    item["phase"] = "ready"
                    released.append(dict(item))
            if released:
                self.save(data)
        return released

    def recover_cycle(self, now):
        for item in sorted(self.read().values(), key=lambda x: x["start"]):
            start, end = (
                datetime.fromisoformat(item["start"]),
                datetime.fromisoformat(item["end"]),
            )
            if item["phase"] == "awaiting_hourly" and start <= now <= end + timedelta(
                hours=1
            ):
                return start.date(), end.date()
        return None

    def align_recurring_start(self, start, end):
        """Do not count an extended final interval again in the next automatic cycle."""
        previous_ends = [
            datetime.fromisoformat(item["end"])
            for item in self.read().values()
            if item["phase"] != "awaiting_hourly"
            and start < datetime.fromisoformat(item["end"]) < end
        ]
        return max([start, *previous_ends]), end

    def claim(self, now):
        with _lock:
            data = self.read()
            for key, item in sorted(data.items(), key=lambda x: x[1]["end"]):
                if item["phase"] != "ready" or item["attempts"] >= 3:
                    continue
                if now < datetime.fromisoformat(item["end"]):
                    continue
                if item.get("last_attempt") and now - datetime.fromisoformat(
                    item["last_attempt"]
                ) < timedelta(minutes=10):
                    continue
                item.update(
                    phase="sending",
                    attempts=item["attempts"] + 1,
                    last_attempt=now.isoformat(),
                )
                self.save(data)
                return key, dict(item)
            return None

    def finish(self, key, successful, uncertain=False):
        with _lock:
            data = self.read()
            item = data[key]
            if item["phase"] == "sent" and not successful:
                return
            item["phase"] = (
                "sent" if successful else "uncertain" if uncertain else "ready"
            )
            self.save(data)
