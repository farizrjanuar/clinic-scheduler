from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_role
from app.models import Doctor, Role, User
from app.schemas import DoctorCreate, DoctorOut
from app.security import hash_password

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/doctors", response_model=DoctorOut, status_code=status.HTTP_201_CREATED)
def create_doctor(
    data: DoctorCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role(Role.ADMIN)),
):
    email = data.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(
        email=email,
        password_hash=hash_password(data.password),
        full_name=data.full_name,
        role=Role.DOCTOR,
    )
    doctor = Doctor(user=user, specialty=data.specialty, slot_minutes=data.slot_minutes)
    db.add(doctor)
    db.commit()
    db.refresh(doctor)
    return doctor
