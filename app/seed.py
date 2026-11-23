"""Idempotent bootstrap: demo venue/courts and the first administrator."""
from __future__ import annotations
import os
import uuid
from sqlalchemy import select
from app.config import Settings
from app.db import make_engine, make_session_factory
from app.models import Court, Member, PeakWindow, User, Venue
from app.security import hash_password


def main() -> None:
    settings = Settings.from_env()
    admin_email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "admin@courtly.example").strip().lower()
    admin_password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
    if not admin_password:
        if settings.environment == "production":
            raise RuntimeError("BOOTSTRAP_ADMIN_PASSWORD is required in production")
        admin_password = "LocalOnly-ChangeMe-123!"
        print("WARNING: using local-only demo admin password; change it immediately.")
    engine = make_engine(settings.database_url, 2, 0)
    factory = make_session_factory(engine)
    with factory() as session, session.begin():
        venue = session.scalar(select(Venue).where(Venue.name == "Riverside Courts"))
        if venue is None:
            venue = Venue(id=uuid.uuid4(), name="Riverside Courts", timezone="Asia/Kolkata",
                currency="INR", peak_rate_minor=80000, offpeak_rate_minor=50000,
                opening_hour=6, closing_hour=22, hold_ttl_seconds=300, payments_enabled=True,
                latitude=12.9716, longitude=77.5946)
            session.add(venue)
            session.flush()
            for day in range(1, 8):
                session.add(PeakWindow(venue_id=venue.id, day_of_week=day, start_hour=17, end_hour=21))
            for name in ("Court 1", "Court 2", "Court 3"):
                session.add(Court(id=uuid.uuid4(), venue_id=venue.id, name=name, active=True))
            print(f"seed: venue {venue.id}")
        elif not session.scalar(select(Court.id).where(Court.venue_id == venue.id)):
            for name in ("Court 1", "Court 2", "Court 3"):
                session.add(Court(id=uuid.uuid4(), venue_id=venue.id, name=name, active=True))
        user = session.scalar(select(User).where(User.email == admin_email))
        if user is None:
            member = Member(id=uuid.uuid4())
            session.add(member)
            session.flush()
            user = User(member_id=member.id, email=admin_email, password_hash=hash_password(admin_password),
                        role="admin", is_verified=True, is_active=True)
            session.add(user)
            print(f"bootstrap admin created: {admin_email}")
        else:
            print(f"bootstrap admin already exists: {admin_email}")
    engine.dispose()

if __name__ == "__main__":
    main()
