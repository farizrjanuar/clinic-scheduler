from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Iterable

UTC = timezone.utc


@dataclass(frozen=True)
class Window:
    """A recurring weekly working window, in the clinic's local time."""

    weekday: int  # 0 = Monday ... 6 = Sunday
    start: time
    end: time


@dataclass(frozen=True)
class Interval:
    """A time range with timezone-aware datetimes."""

    start: datetime
    end: datetime


def _overlaps(start: datetime, end: datetime, blocked: list[Interval]) -> bool:
    return any(start < b.end and end > b.start for b in blocked)


def generate_slots(
    *,
    windows: Iterable[Window],
    blocked: Iterable[Interval],
    slot_minutes: int,
    start_date: date,
    end_date: date,
    tz: tzinfo,
    now: datetime | None = None,
) -> list[Interval]:
    """Open slots between start_date and end_date (inclusive, clinic-local dates).

    Working windows are interpreted in `tz`; results are returned in UTC.
    Slots that overlap a blocked interval (time off or a non-cancelled
    appointment) or that start in the past are excluded.
    """
    if slot_minutes <= 0:
        raise ValueError("slot_minutes must be positive")

    now = now or datetime.now(UTC)
    step = timedelta(minutes=slot_minutes)
    windows = list(windows)
    blocked = list(blocked)

    slots: list[Interval] = []
    day = start_date
    while day <= end_date:
        for w in windows:
            if w.weekday != day.weekday():
                continue
            cursor = datetime.combine(day, w.start, tzinfo=tz).astimezone(UTC)
            window_end = datetime.combine(day, w.end, tzinfo=tz).astimezone(UTC)
            while cursor + step <= window_end:
                slot = Interval(cursor, cursor + step)
                if cursor > now and not _overlaps(slot.start, slot.end, blocked):
                    slots.append(slot)
                cursor += step
        day += timedelta(days=1)

    slots.sort(key=lambda s: s.start)
    return slots
