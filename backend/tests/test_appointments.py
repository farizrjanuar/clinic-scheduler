from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.models import AuditLog, Doctor, Role, User
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


def headers_for(client, email, password=PASSWORD):
    r = client.post("/auth/login", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def make_doctor(engine, email="doc@clinicdemo.com"):
    with Session(engine) as s:
        doctor = Doctor(
            user=User(
                email=email,
                password_hash=hash_password(PASSWORD),
                full_name="Dr Test",
                role=Role.DOCTOR,
            ),
            specialty="General",
            slot_minutes=30,
        )
        s.add(doctor)
        s.commit()
        return doctor.id


def make_admin(engine):
    with Session(engine) as s:
        s.add(
            User(
                email="admin@clinicdemo.com",
                password_hash=hash_password(PASSWORD),
                full_name="Admin",
                role=Role.ADMIN,
            )
        )
        s.commit()


def register_patient(client, email="pat@clinicdemo.com"):
    r = client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": "Pat"},
    )
    assert r.status_code == 201, r.text
    return headers_for(client, email), r.json()["id"]


def doctor_with_hours(client, engine):
    doctor_id = make_doctor(engine)
    doc = headers_for(client, "doc@clinicdemo.com")
    assert client.put("/doctors/me/availability", json=MORNING, headers=doc).status_code == 200
    return doctor_id, doc


def book(client, headers, doctor_id, starts_at, **extra):
    return client.post(
        "/appointments",
        json={"doctor_id": doctor_id, "starts_at": starts_at.isoformat(), **extra},
        headers=headers,
    )


def open_slot_count(client, headers, doctor_id, day):
    r = client.get(f"/doctors/{doctor_id}/slots?from={day}&to={day}", headers=headers)
    return len(r.json())


def test_patient_books_open_slot(client, engine):
    doctor_id, _ = doctor_with_hours(client, engine)
    pat, pat_id = register_patient(client)
    day = next_monday()

    r = book(client, pat, doctor_id, at(day, 9), notes="Checkup")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "booked"
    assert body["patient_id"] == pat_id
    assert open_slot_count(client, pat, doctor_id, day) == 3


def test_cannot_book_off_grid_or_outside_hours(client, engine):
    doctor_id, _ = doctor_with_hours(client, engine)
    pat, _ = register_patient(client)
    day = next_monday()
    assert book(client, pat, doctor_id, at(day, 9, 10)).status_code == 409
    assert book(client, pat, doctor_id, at(day, 14)).status_code == 409


def test_cannot_book_taken_slot(client, engine):
    doctor_id, _ = doctor_with_hours(client, engine)
    a, _ = register_patient(client, "a@clinicdemo.com")
    b, _ = register_patient(client, "b@clinicdemo.com")
    day = next_monday()
    assert book(client, a, doctor_id, at(day, 9)).status_code == 201
    assert book(client, b, doctor_id, at(day, 9)).status_code == 409


def test_cancel_frees_the_slot(client, engine):
    doctor_id, _ = doctor_with_hours(client, engine)
    a, _ = register_patient(client, "a@clinicdemo.com")
    b, _ = register_patient(client, "b@clinicdemo.com")
    day = next_monday()

    appt_id = book(client, a, doctor_id, at(day, 9)).json()["id"]
    r = client.post(f"/appointments/{appt_id}/cancel", headers=a)
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"

    assert open_slot_count(client, a, doctor_id, day) == 4
    assert book(client, b, doctor_id, at(day, 9)).status_code == 201


def test_other_patient_cannot_see_or_cancel(client, engine):
    doctor_id, _ = doctor_with_hours(client, engine)
    a, _ = register_patient(client, "a@clinicdemo.com")
    b, _ = register_patient(client, "b@clinicdemo.com")
    appt_id = book(client, a, doctor_id, at(next_monday(), 9)).json()["id"]

    assert client.get(f"/appointments/{appt_id}", headers=b).status_code == 404
    assert client.post(f"/appointments/{appt_id}/cancel", headers=b).status_code == 404
    assert client.get(f"/appointments/{appt_id}", headers=a).status_code == 200


def test_doctor_drives_status_flow(client, engine):
    doctor_id, doc = doctor_with_hours(client, engine)
    pat, _ = register_patient(client)
    appt_id = book(client, pat, doctor_id, at(next_monday(), 9)).json()["id"]
    url = f"/appointments/{appt_id}/status"

    # cannot skip ahead
    assert client.patch(url, json={"status": "completed"}, headers=doc).status_code == 409

    for step in ("confirmed", "checked_in", "completed"):
        r = client.patch(url, json={"status": step}, headers=doc)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == step

    # terminal state
    assert client.patch(url, json={"status": "cancelled"}, headers=doc).status_code == 409


def test_patient_cannot_confirm(client, engine):
    doctor_id, _ = doctor_with_hours(client, engine)
    pat, _ = register_patient(client)
    appt_id = book(client, pat, doctor_id, at(next_monday(), 9)).json()["id"]
    r = client.patch(
        f"/appointments/{appt_id}/status", json={"status": "confirmed"}, headers=pat
    )
    assert r.status_code == 403


def test_my_appointments_and_schedule(client, engine):
    doctor_id, doc = doctor_with_hours(client, engine)
    pat, _ = register_patient(client)
    day = next_monday()
    book(client, pat, doctor_id, at(day, 9))
    book(client, pat, doctor_id, at(day, 10))

    mine = client.get("/appointments/mine", headers=pat)
    assert mine.status_code == 200
    assert len(mine.json()) == 2

    sched = client.get(f"/appointments/schedule?date={day}", headers=doc)
    assert sched.status_code == 200
    assert [a["status"] for a in sched.json()] == ["booked", "booked"]

    assert client.get(f"/appointments/schedule?date={day}", headers=pat).status_code == 403


def test_admin_books_for_patient(client, engine):
    doctor_id, _ = doctor_with_hours(client, engine)
    _, pat_id = register_patient(client)
    make_admin(engine)
    admin = headers_for(client, "admin@clinicdemo.com")
    day = next_monday()

    assert book(client, admin, doctor_id, at(day, 9)).status_code == 422
    r = book(client, admin, doctor_id, at(day, 9), patient_id=pat_id)
    assert r.status_code == 201
    assert r.json()["patient_id"] == pat_id


def test_audit_log_records_changes(client, engine):
    doctor_id, _ = doctor_with_hours(client, engine)
    pat, _ = register_patient(client)
    appt_id = book(client, pat, doctor_id, at(next_monday(), 9)).json()["id"]
    client.post(f"/appointments/{appt_id}/cancel", headers=pat)

    with Session(engine) as s:
        rows = s.scalars(
            select(AuditLog)
            .where(AuditLog.appointment_id == appt_id)
            .order_by(AuditLog.id)
        ).all()
        assert [r.action for r in rows] == [
            "appointment.created",
            "appointment.status_changed",
        ]
        assert rows[1].old_data == {"status": "booked"}
        assert rows[1].new_data == {"status": "cancelled"}


def test_concurrent_bookings_only_one_wins(client, engine):
    doctor_id, _ = doctor_with_hours(client, engine)
    day = next_monday()
    patients = [register_patient(client, f"p{i}@clinicdemo.com")[0] for i in range(8)]

    def attempt(headers):
        c = TestClient(app)  # a separate client per thread
        return book(c, headers, doctor_id, at(day, 9)).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(attempt, patients))

    assert sorted(codes) == [201] + [409] * 7
