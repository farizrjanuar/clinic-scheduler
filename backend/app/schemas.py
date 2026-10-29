from datetime import datetime, time

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    model_validator,
)

from app.models import Role


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=120)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    role: Role


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class DoctorCreate(UserCreate):
    specialty: str = Field(min_length=1, max_length=100)
    slot_minutes: int = Field(default=30, ge=5, le=240)


class DoctorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    specialty: str
    slot_minutes: int
    user: UserOut


class DoctorPublic(BaseModel):
    """What patients see: no email or account details."""

    id: int
    full_name: str
    specialty: str
    slot_minutes: int


class AvailabilityRuleIn(BaseModel):
    weekday: int = Field(ge=0, le=6)  # 0 = Monday
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def check_order(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class AvailabilityRuleOut(AvailabilityRuleIn):
    model_config = ConfigDict(from_attributes=True)

    id: int


class AvailabilitySet(BaseModel):
    rules: list[AvailabilityRuleIn] = Field(max_length=50)

    @model_validator(mode="after")
    def no_overlaps(self):
        last_end: dict[int, time] = {}
        for r in sorted(self.rules, key=lambda r: (r.weekday, r.start_time)):
            prev = last_end.get(r.weekday)
            if prev is not None and r.start_time < prev:
                raise ValueError(f"Overlapping availability on weekday {r.weekday}")
            last_end[r.weekday] = r.end_time
        return self


class TimeOffCreate(BaseModel):
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    reason: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def check_order(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class TimeOffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    starts_at: datetime
    ends_at: datetime
    reason: str | None


class SlotOut(BaseModel):
    starts_at: datetime
    ends_at: datetime
