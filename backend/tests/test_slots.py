from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from app.slots import Interval, Window, generate_slots

TZ = ZoneInfo("Asia/Jakarta")
UTC = timezone.utc
PAST = datetime(2026, 1, 1, tzinfo=UTC)
MONDAY = date(2026, 11, 2)
TUESDAY = date(2026, 11, 3)


def wib(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=TZ)


def run(windows, blocked=(), minutes=30, start=MONDAY, end=MONDAY, now=PAST):
    return generate_slots(
        windows=windows,
        blocked=blocked,
        slot_minutes=minutes,
        start_date=start,
        end_date=end,
        tz=TZ,
        now=now,
    )


def test_basic_slots_in_utc():
    slots = run([Window(0, time(9), time(11))])
    assert len(slots) == 4
    assert slots[0].start == wib(MONDAY, 9)
    assert slots[0].start.hour == 2  # 09:00 WIB is 02:00 UTC


def test_no_window_for_that_weekday():
    assert run([Window(0, time(9), time(11))], start=TUESDAY, end=TUESDAY) == []


def test_time_off_removes_overlapping_slots():
    off = Interval(wib(MONDAY, 9, 30), wib(MONDAY, 10, 15))
    slots = run([Window(0, time(9), time(11))], blocked=[off])
    assert [s.start for s in slots] == [wib(MONDAY, 9), wib(MONDAY, 10, 30)]


def test_booked_slot_removed():
    booked = Interval(wib(MONDAY, 9, 30), wib(MONDAY, 10))
    slots = run([Window(0, time(9), time(11))], blocked=[booked])
    assert len(slots) == 3
    assert wib(MONDAY, 9, 30) not in [s.start for s in slots]


def test_partial_trailing_slot_dropped():
    slots = run([Window(0, time(9), time(10, 45))])
    assert len(slots) == 3  # 09:00, 09:30, 10:00; 10:30-11:00 does not fit


def test_past_slots_excluded():
    now = wib(MONDAY, 9, 45).astimezone(UTC)
    slots = run([Window(0, time(9), time(11))], now=now)
    assert [s.start for s in slots] == [wib(MONDAY, 10), wib(MONDAY, 10, 30)]


def test_slot_starting_exactly_now_excluded():
    now = wib(MONDAY, 10).astimezone(UTC)
    slots = run([Window(0, time(9), time(11))], now=now)
    assert [s.start for s in slots] == [wib(MONDAY, 10, 30)]


def test_multiple_windows_and_days_sorted():
    windows = [
        Window(1, time(9), time(10)),
        Window(0, time(13), time(14)),
        Window(0, time(9), time(10)),
    ]
    slots = run(windows, start=MONDAY, end=TUESDAY)
    starts = [s.start for s in slots]
    assert len(slots) == 6
    assert starts == sorted(starts)


def test_invalid_slot_length():
    with pytest.raises(ValueError):
        run([Window(0, time(9), time(11))], minutes=0)
