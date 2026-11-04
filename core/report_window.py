"""Explicit report cycles and half-open hourly intervals."""

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class ReportWindow:
    start: datetime
    end: datetime
    cutoff: datetime

    def __post_init__(self):
        if self.start.minute or self.start.second or self.end.minute or self.end.second:
            raise ValueError("Cycle boundaries must be whole hours")
        if not self.start < self.end or self.end - self.start > timedelta(days=7):
            raise ValueError("Cycle duration must be positive and at most seven days")
        if not self.start < self.cutoff <= self.end:
            raise ValueError("Cutoff must lie within the cycle")

    @property
    def key(self):
        return f"{self.start.isoformat()}__{self.end.isoformat()}"

    @property
    def slots(self):
        count = int((self.cutoff - self.start).total_seconds() // 3600)
        return tuple(self.start + timedelta(hours=i) for i in range(count))

    @classmethod
    def capture(cls, start, end, now):
        return cls(start, end, min(end, now))
