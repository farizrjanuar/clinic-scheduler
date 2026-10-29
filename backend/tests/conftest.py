import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401  (registers tables)
from app.config import settings
from app.db import Base, get_db
from app.main import app

TEST_DB_URL = settings.database_url.rsplit("/", 1)[0] + "/clinic_test"

# Same guard as the Alembic migration: no overlapping non-cancelled bookings.
OVERLAP_GUARD_SQL = """
ALTER TABLE appointments
ADD CONSTRAINT no_overlapping_appointments
EXCLUDE USING gist (
    doctor_id WITH =,
    tstzrange(starts_at, ends_at) WITH &&
)
WHERE (status <> 'cancelled')
"""


@pytest.fixture(scope="session")
def engine():
    admin = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = 'clinic_test'")
        ).scalar()
        if not exists:
            conn.execute(text("CREATE DATABASE clinic_test"))
    admin.dispose()

    eng = create_engine(TEST_DB_URL)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        conn.execute(text(OVERLAP_GUARD_SQL))
    yield eng
    eng.dispose()


@pytest.fixture()
def client(engine):
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE audit_log, appointments, time_off, "
                "availability_rules, doctors, users RESTART IDENTITY CASCADE"
            )
        )
    TestSession = sessionmaker(bind=engine, autoflush=False)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
