from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.db import get_db
from app.deps import get_current_user, require_role
from app.models import (
    Appointment,
    AppointmentStatus,
    AvailabilityRule,
    Doctor,
    Role,
    TimeOff,
    User,
)
from app.schemas import (
    AvailabilityRuleOut,
    AvailabilitySet,
    DoctorPublic,
    SlotOut,
    TimeOffCreate,
    TimeOffOut,
)
from app.slots import Interval, Window, generate_slots

router = APIRouter(prefix="/doctors", tags=["doctors"])

CLINIC_TZ = ZoneInfo(settings.clinic_timezone)
MAX_RANGE_DAYS = 31


def get_current_doctor(
    user: User = Depends(require_role(Role.DOCTOR)), db: Session = Depends(get_db)
) -> Doctor:
    doctor = db.scalar(select(Doctor).where(Doctor.user_id == user.id))
    if doctor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Doctor profile not found")
    return doctor


# ---- Public (any logged-in user) -------------------------------------------


@router.get("", response_model=list[DoctorPublic])
def list_doctors(
    db: Session = Depends(get_db), _user: User = Depends(get_current_user)
):
    doctors = db.scalars(
        select(Doctor).options(joinedload(Doctor.user)).order_by(Doctor.id)
    ).all()
    return [
        DoctorPublic(
            id=d.id,
            full_name=d.user.full_name,
            specialty=d.specialty,
            slot_minutes=d.slot_minutes,
        )
        for d in doctors
    ]


@router.get("/{doctor_id}/slots", response_model=list[SlotOut])
def get_slots(
    doctor_id: int,
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    if date_to < date_from:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "'to' must not be before 'from'")
    if (date_to - date_from).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Range too large (max {MAX_RANGE_DAYS} days)"
        )

    doctor = db.get(Doctor, doctor_id)
    if doctor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Doctor not found")

    range_start = datetime.combine(date_from, time.min, tzinfo=CLINIC_TZ).astimezone(
        timezone.utc
    )
    range_end = datetime.combine(
        date_to + timedelta(days=1), time.min, tzinfo=CLINIC_TZ
    ).astimezone(timezone.utc)

    rules = db.scalars(
        select(AvailabilityRule).where(AvailabilityRule.doctor_id == doctor_id)
    ).all()
    time_off = db.scalars(
        select(TimeOff).where(
            TimeOff.doctor_id == doctor_id,
            TimeOff.starts_at < range_end,
            TimeOff.ends_at > range_start,
        )
    ).all()
    booked = db.scalars(
        select(Appointment).where(
            Appointment.doctor_id == doctor_id,
            Appointment.status != AppointmentStatus.CANCELLED,
            Appointment.starts_at < range_end,
            Appointment.ends_at > range_start,
        )
    ).all()

    blocked = [Interval(t.starts_at, t.ends_at) for t in time_off] + [
        Interval(a.starts_at, a.ends_at) for a in booked
    ]
    slots = generate_slots(
        windows=[Window(r.weekday, r.start_time, r.end_time) for r in rules],
        blocked=blocked,
        slot_minutes=doctor.slot_minutes,
        start_date=date_from,
        end_date=date_to,
        tz=CLINIC_TZ,
    )
    return [SlotOut(starts_at=s.start, ends_at=s.end) for s in slots]


# ---- Doctor-only: own availability and time off -----------------------------


@router.get("/me/availability", response_model=list[AvailabilityRuleOut])
def get_my_availability(
    db: Session = Depends(get_db), doctor: Doctor = Depends(get_current_doctor)
):
    return db.scalars(
        select(AvailabilityRule)
        .where(AvailabilityRule.doctor_id == doctor.id)
        .order_by(AvailabilityRule.weekday, AvailabilityRule.start_time)
    ).all()


@router.put("/me/availability", response_model=list[AvailabilityRuleOut])
def set_my_availability(
    data: AvailabilitySet,
    db: Session = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
):
    """Replace the whole weekly schedule in one go."""
    db.execute(delete(AvailabilityRule).where(AvailabilityRule.doctor_id == doctor.id))
    rules = [
        AvailabilityRule(doctor_id=doctor.id, **r.model_dump()) for r in data.rules
    ]
    db.add_all(rules)
    db.commit()
    for r in rules:
        db.refresh(r)
    return sorted(rules, key=lambda r: (r.weekday, r.start_time))


@router.get("/me/time-off", response_model=list[TimeOffOut])
def list_my_time_off(
    db: Session = Depends(get_db), doctor: Doctor = Depends(get_current_doctor)
):
    return db.scalars(
        select(TimeOff).where(TimeOff.doctor_id == doctor.id).order_by(TimeOff.starts_at)
    ).all()


@router.post(
    "/me/time-off", response_model=TimeOffOut, status_code=status.HTTP_201_CREATED
)
def add_my_time_off(
    data: TimeOffCreate,
    db: Session = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
):
    item = TimeOff(
        doctor_id=doctor.id,
        starts_at=data.starts_at,
        ends_at=data.ends_at,
        reason=data.reason,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/me/time-off/{time_off_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_time_off(
    time_off_id: int,
    db: Session = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
):
    item = db.scalar(
        select(TimeOff).where(TimeOff.id == time_off_id, TimeOff.doctor_id == doctor.id)
    )
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Time off not found")
    db.delete(item)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
