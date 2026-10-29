from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, require_role
from app.models import Appointment
from app.models import AppointmentStatus as S
from app.models import AuditLog, Doctor, Role, User
from app.scheduling import CLINIC_TZ, get_open_slots, local_day_bounds
from app.schemas import AppointmentCreate, AppointmentOut, StatusUpdate

router = APIRouter(prefix="/appointments", tags=["appointments"])

ALLOWED_TRANSITIONS: dict[S, set[S]] = {
    S.BOOKED: {S.CONFIRMED, S.CANCELLED, S.NO_SHOW},
    S.CONFIRMED: {S.CHECKED_IN, S.CANCELLED, S.NO_SHOW},
    S.CHECKED_IN: {S.COMPLETED},
    S.COMPLETED: set(),
    S.CANCELLED: set(),
    S.NO_SHOW: set(),
}


# ---- helpers ----------------------------------------------------------------


def _doctor_id_of(db: Session, user: User) -> int | None:
    return db.scalar(select(Doctor.id).where(Doctor.user_id == user.id))


def _snapshot(appt: Appointment) -> dict:
    return {
        "doctor_id": appt.doctor_id,
        "patient_id": appt.patient_id,
        "starts_at": appt.starts_at.isoformat(),
        "ends_at": appt.ends_at.isoformat(),
        "status": appt.status.value,
    }


def _audit(db: Session, user: User, appt: Appointment, action: str, old, new) -> None:
    db.add(
        AuditLog(
            actor_id=user.id,
            appointment_id=appt.id,
            action=action,
            old_data=old,
            new_data=new,
        )
    )


def _visible_or_404(db: Session, user: User, appointment_id: int) -> Appointment:
    """Return the appointment if this user may see it, else 404 (no leaking)."""
    appt = db.get(Appointment, appointment_id)
    allowed = False
    if appt is not None:
        if user.role == Role.ADMIN:
            allowed = True
        elif user.role == Role.PATIENT:
            allowed = appt.patient_id == user.id
        elif user.role == Role.DOCTOR:
            allowed = appt.doctor_id == _doctor_id_of(db, user)
    if not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")
    return appt


def _change_status(
    db: Session, user: User, appt: Appointment, new_status: S
) -> Appointment:
    old = appt.status

    # 1) who may do this
    if user.role == Role.PATIENT and new_status != S.CANCELLED:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Patients can only cancel their appointments"
        )
    # 2) is the transition legal
    if new_status not in ALLOWED_TRANSITIONS[old]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot change status from '{old.value}' to '{new_status.value}'",
        )
    # 3) patients cannot cancel something that already started
    if user.role == Role.PATIENT and appt.starts_at <= datetime.now(timezone.utc):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Past appointments cannot be cancelled"
        )

    appt.status = new_status
    _audit(
        db,
        user,
        appt,
        "appointment.status_changed",
        {"status": old.value},
        {"status": new_status.value},
    )
    db.commit()
    db.refresh(appt)
    return appt


# ---- create -----------------------------------------------------------------


@router.post("", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED)
def book_appointment(
    data: AppointmentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.PATIENT, Role.ADMIN)),
):
    if user.role == Role.ADMIN:
        if data.patient_id is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "patient_id is required when an admin books",
            )
        patient = db.get(User, data.patient_id)
        if patient is None or patient.role != Role.PATIENT:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
        patient_id = patient.id
    else:
        if data.patient_id not in (None, user.id):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Patients can only book for themselves"
            )
        patient_id = user.id

    doctor = db.get(Doctor, data.doctor_id)
    if doctor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Doctor not found")

    # The requested time must be exactly one of the doctor's open slots.
    starts_at = data.starts_at.astimezone(timezone.utc)
    local_day = starts_at.astimezone(CLINIC_TZ).date()
    slot = next(
        (s for s in get_open_slots(db, doctor, local_day, local_day) if s.start == starts_at),
        None,
    )
    if slot is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That time is not an available slot"
        )

    appt = Appointment(
        doctor_id=doctor.id,
        patient_id=patient_id,
        starts_at=slot.start,
        ends_at=slot.end,
        status=S.BOOKED,
        notes=data.notes,
    )
    db.add(appt)
    try:
        # The database overlap guard has the final say if two requests race.
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That slot was just taken")

    _audit(db, user, appt, "appointment.created", None, _snapshot(appt))
    db.commit()
    db.refresh(appt)
    return appt


# ---- read (static paths first, so "mine" is not parsed as an id) ------------


@router.get("/mine", response_model=list[AppointmentOut])
def my_appointments(
    db: Session = Depends(get_db), user: User = Depends(require_role(Role.PATIENT))
):
    return db.scalars(
        select(Appointment)
        .where(Appointment.patient_id == user.id)
        .order_by(Appointment.starts_at.desc())
    ).all()


@router.get("/schedule", response_model=list[AppointmentOut])
def schedule(
    day: date = Query(alias="date"),
    doctor_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.DOCTOR, Role.ADMIN)),
):
    """A doctor's own day, or (admin) any doctor's day / all doctors."""
    if user.role == Role.DOCTOR:
        own = _doctor_id_of(db, user)
        if own is None or (doctor_id is not None and doctor_id != own):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your schedule")
        doctor_id = own

    start, end = local_day_bounds(day, day)
    stmt = (
        select(Appointment)
        .where(Appointment.starts_at >= start, Appointment.starts_at < end)
        .order_by(Appointment.starts_at)
    )
    if doctor_id is not None:
        stmt = stmt.where(Appointment.doctor_id == doctor_id)
    return db.scalars(stmt).all()


@router.get("/{appointment_id}", response_model=AppointmentOut)
def get_appointment(
    appointment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _visible_or_404(db, user, appointment_id)


# ---- status changes ---------------------------------------------------------


@router.patch("/{appointment_id}/status", response_model=AppointmentOut)
def update_status(
    appointment_id: int,
    data: StatusUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    appt = _visible_or_404(db, user, appointment_id)
    return _change_status(db, user, appt, data.status)


@router.post("/{appointment_id}/cancel", response_model=AppointmentOut)
def cancel_appointment(
    appointment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    appt = _visible_or_404(db, user, appointment_id)
    return _change_status(db, user, appt, S.CANCELLED)
