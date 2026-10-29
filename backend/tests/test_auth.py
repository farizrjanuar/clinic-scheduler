from sqlalchemy.orm import Session

from app.models import Role, User
from app.security import hash_password

PASSWORD = "Password123"


def register(client, email="pat@clinicdemo.com", password=PASSWORD):
    return client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "Pat Test"},
    )


def login(client, email, password=PASSWORD):
    return client.post("/auth/login", data={"username": email, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


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


def test_register_and_login(client):
    r = register(client)
    assert r.status_code == 201
    assert r.json()["role"] == "patient"
    assert "password" not in r.text

    r = login(client, "pat@clinicdemo.com")
    assert r.status_code == 200
    token = r.json()["access_token"]

    me = client.get("/auth/me", headers=bearer(token))
    assert me.status_code == 200
    assert me.json()["email"] == "pat@clinicdemo.com"


def test_duplicate_email(client):
    assert register(client).status_code == 201
    assert register(client).status_code == 409


def test_short_password_rejected(client):
    assert register(client, password="short").status_code == 422


def test_wrong_password(client):
    register(client)
    assert login(client, "pat@clinicdemo.com", "WrongPassword1").status_code == 401


def test_me_requires_token(client):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers=bearer("garbage")).status_code == 401


def test_patient_cannot_create_doctor(client):
    register(client)
    token = login(client, "pat@clinicdemo.com").json()["access_token"]
    r = client.post(
        "/admin/doctors",
        headers=bearer(token),
        json={
            "email": "doc@clinicdemo.com",
            "password": PASSWORD,
            "full_name": "Dr Test",
            "specialty": "General",
        },
    )
    assert r.status_code == 403


def test_admin_creates_doctor_who_can_log_in(client, engine):
    make_admin(engine)
    admin_token = login(client, "admin@clinicdemo.com").json()["access_token"]
    r = client.post(
        "/admin/doctors",
        headers=bearer(admin_token),
        json={
            "email": "doc@clinicdemo.com",
            "password": PASSWORD,
            "full_name": "Dr Test",
            "specialty": "General",
            "slot_minutes": 20,
        },
    )
    assert r.status_code == 201
    assert r.json()["specialty"] == "General"
    assert r.json()["user"]["role"] == "doctor"

    r = login(client, "doc@clinicdemo.com")
    assert r.status_code == 200
