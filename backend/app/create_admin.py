import argparse

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Role, User
from app.security import hash_password


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an admin user")
    parser.add_argument("email")
    parser.add_argument("password")
    parser.add_argument("--name", default="Admin")
    args = parser.parse_args()

    email = args.email.lower()
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == email)):
            raise SystemExit("A user with that email already exists")
        db.add(
            User(
                email=email,
                password_hash=hash_password(args.password),
                full_name=args.name,
                role=Role.ADMIN,
            )
        )
        db.commit()
    print(f"Created admin {email}")


if __name__ == "__main__":
    main()
