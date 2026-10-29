from pydantic import BaseModel, ConfigDict, EmailStr, Field

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
