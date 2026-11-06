import os
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

# Nightly window for the scheduled indexing job, so Whisper/Docling don't compete with users.
DEFAULT_START = "22:00"
DEFAULT_END = "05:00"
DEFAULT_TZ = "America/Santo_Domingo"


def _parse_hhmm(value: str) -> dtime:
    try:
        hour, minute = value.strip().split(":")
        return dtime(int(hour), int(minute))
    except ValueError:
        raise ValueError(f"Invalid time '{value}', expected HH:MM") from None


class IndexingWindow:
    """Daily [start, end) interval in a timezone. Crosses midnight when end <= start."""

    def __init__(self, start=DEFAULT_START, end=DEFAULT_END, tz=DEFAULT_TZ):
        self.start = _parse_hhmm(start)
        self.end = _parse_hhmm(end)
        self.tz = ZoneInfo(tz)

    @classmethod
    def from_env(cls):
        return cls(
            os.getenv("INDEXING_WINDOW_START", DEFAULT_START),
            os.getenv("INDEXING_WINDOW_END", DEFAULT_END),
            os.getenv("INDEXING_TZ", DEFAULT_TZ),
        )

    def _local(self, now=None) -> datetime:
        return (now or datetime.now(self.tz)).astimezone(self.tz)

    def is_open(self, now=None) -> bool:
        t = self._local(now).time()
        if self.start == self.end:
            return True  # equal bounds = no restriction
        if self.start < self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end

    def night_id(self, now=None) -> str:
        """Date the current window opened on (a window past midnight belongs to the previous day)."""
        local = self._local(now)
        if self.start > self.end and local.time() < self.end:
            local -= timedelta(days=1)
        return local.date().isoformat()


class NightlyScheduler:
    """Runs a job at most once per window: call `tick()` periodically."""

    def __init__(self, window: IndexingWindow, job):
        self.window = window
        self.job = job  # called with a `should_continue` callable
        self.last_night = None

    def tick(self, now=None) -> bool:
        """Runs the job when the window is open and tonight's run hasn't happened. Returns True if it ran."""
        if not self.window.is_open(now):
            return False
        night = self.window.night_id(now)
        if night == self.last_night:
            return False
        self.last_night = night
        self.job(self.window.is_open)
        return True
