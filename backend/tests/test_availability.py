from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import Appointment, AppointmentStatus, Doctor, Role, User
from app.security import hash_password

PASSWORD = "Password123"
TZ = ZoneInfo("Asia/Jakarta")
MORNING = {
    "rules": [{"weekday": 0, "start_time": "09:00:00", "end_time": "11:00:00"}]
}


def next_monday():
    d = date.today() + timedelta(days=7)
    return d + timedelta(days=(0 - d.weekday()) % 7)


def at(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=TZ)


def make_doctor(engine, email="doc@clinicdemo.com", slot_minutes=30):
    with Session(engine) as s:
        doctor = Doctor(
            user=User(
                email=email,
                password_hash=hash_password(PASSWORD),
                full_name="Dr Test",
                role=Role.DOCTOR,
            ),
            specialty="General",
            slot_minutes=slot_minutes,
        )
        s.add(doctor)
        s.commit()
        return doctor.id


def headers_for(client, email, password=PASSWORD):
    r = client.post("/auth/login", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def patient_headers(client):
    client.post(
        "/auth/register",
        json={"email": "pat@clinicdemo.com", "password": PASSWORD, "full_name": "Pat"},
    )
    return headers_for(client, "pat@clinicdemo.com")


def doctor_with_hours(client, engine):
    doctor_id = make_doctor(engine)
    doc = headers_for(client, "doc@clinicdemo.com")
    r = client.put("/doctors/me/availability", json=MORNING, headers=doc)
    assert r.status_code == 200, r.text
    return doctor_id, doc


def slots(client, headers, doctor_id, day):
    return client.get(
        f"/doctors/{doctor_id}/slots?from={day}&to={day}", headers=headers
    )


def test_patient_sees_slots_from_doctor_hours(client, engine):
    doctor_id, _ = doctor_with_hours(client, engine)
    r = slots(client, patient_headers(client), doctor_id, next_monday())
    assert r.status_code == 200
    assert len(r.json()) == 4


def test_patient_cannot_set_availability(client):
    r = client.put(
        "/doctors/me/availability", json=MORNING, headers=patient_headers(client)
    )
    assert r.status_code == 403


def test_overlapping_rules_rejected(client, engine):
    make_doctor(engine)
    doc = headers_for(client, "doc@clinicdemo.com")
    body = {
        "rules": [
            {"weekday": 0, "start_time": "09:00:00", "end_time": "11:00:00"},
            {"weekday": 0, "start_time": "10:00:00", "end_time": "12:00:00"},
        ]
    }
    assert client.put("/doctors/me/availability", json=body, headers=doc).status_code == 422


def test_time_off_blocks_slots_and_can_be_deleted(client, engine):
    doctor_id, doc = doctor_with_hours(client, engine)
    pat = patient_headers(client)
    day = next_monday()

    r = client.post(
        "/doctors/me/time-off",
        json={
            "starts_at": at(day, 9).isoformat(),
            "ends_at": at(day, 10).isoformat(),
            "reason": "Meeting",
        },
        headers=doc,
    )
    assert r.status_code == 201
    assert len(slots(client, pat, doctor_id, day).json()) == 2

    assert client.delete(f"/doctors/me/time-off/{r.json()['id']}", headers=doc).status_code == 204
    assert len(slots(client, pat, doctor_id, day).json()) == 4


def test_booked_blocks_slot_but_cancelled_does_not(client, engine):
    doctor_id, _ = doctor_with_hours(client, engine)
    pat = patient_headers(client)
    patient_id = client.get("/auth/me", headers=pat).json()["id"]
    day = next_monday()

    with Session(engine) as s:
        s.add(
            Appointment(
                doctor_id=doctor_id,
                patient_id=patient_id,
                starts_at=at(day, 9),
                ends_at=at(day, 9, 30),
                status=AppointmentStatus.BOOKED,
            )
        )
        s.add(
            Appointment(
                doctor_id=doctor_id,
                patient_id=patient_id,
                starts_at=at(day, 10),
                ends_at=at(day, 10, 30),
                status=AppointmentStatus.CANCELLED,
            )
        )
        s.commit()

    assert len(slots(client, pat, doctor_id, day).json()) == 3


def test_range_limit_and_unknown_doctor(client, engine):
    doctor_id, _ = doctor_with_hours(client, engine)
    pat = patient_headers(client)
    today = date.today()
    far = today + timedelta(days=40)
    r = client.get(f"/doctors/{doctor_id}/slots?from={today}&to={far}", headers=pat)
    assert r.status_code == 400
    assert slots(client, pat, 9999, today).status_code == 404


def test_list_doctors_hides_email(client, engine):
    make_doctor(engine)
    r = client.get("/doctors", headers=patient_headers(client))
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["full_name"] == "Dr Test"
    assert "email" not in r.json()[0]
