from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    Appointment,
    AppointmentStatus,
    AvailabilityRule,
    Doctor,
    TimeOff,
)
from app.slots import Interval, Window, generate_slots

CLINIC_TZ = ZoneInfo(settings.clinic_timezone)


def local_day_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """UTC instants covering clinic-local dates date_from..date_to inclusive."""
    start = datetime.combine(date_from, time.min, tzinfo=CLINIC_TZ).astimezone(
        timezone.utc
    )
    end = datetime.combine(
        date_to + timedelta(days=1), time.min, tzinfo=CLINIC_TZ
    ).astimezone(timezone.utc)
    return start, end


def get_open_slots(
    db: Session, doctor: Doctor, date_from: date, date_to: date
) -> list[Interval]:
    range_start, range_end = local_day_bounds(date_from, date_to)

    rules = db.scalars(
        select(AvailabilityRule).where(AvailabilityRule.doctor_id == doctor.id)
    ).all()
    time_off = db.scalars(
        select(TimeOff).where(
            TimeOff.doctor_id == doctor.id,
            TimeOff.starts_at < range_end,
            TimeOff.ends_at > range_start,
        )
    ).all()
    booked = db.scalars(
        select(Appointment).where(
            Appointment.doctor_id == doctor.id,
            Appointment.status != AppointmentStatus.CANCELLED,
            Appointment.starts_at < range_end,
            Appointment.ends_at > range_start,
        )
    ).all()

    blocked = [Interval(t.starts_at, t.ends_at) for t in time_off] + [
        Interval(a.starts_at, a.ends_at) for a in booked
    ]
    return generate_slots(
        windows=[Window(r.weekday, r.start_time, r.end_time) for r in rules],
        blocked=blocked,
        slot_minutes=doctor.slot_minutes,
        start_date=date_from,
        end_date=date_to,
        tz=CLINIC_TZ,
    )
