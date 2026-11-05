"""Create one demo venue, two courts, a peak window every day, and one demo member.

Safe to run repeatedly: it does nothing when the venue already exists.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.config import Settings
from app.db import make_engine, make_session_factory
from app.models import Court, Member, PeakWindow, Venue


def main() -> None:
    settings = Settings.from_env()
    engine = make_engine(settings.database_url, 2, 0)
    factory = make_session_factory(engine)
    with factory() as session, session.begin():
        if session.scalars(select(Venue).where(Venue.name == "Riverside Courts")).first() is not None:
            print("seed: already present")
            return
        venue = Venue(
            id=uuid.uuid4(),
            name="Riverside Courts",
            timezone="Asia/Kolkata",
            currency="INR",
            peak_rate_minor=80_000,
            offpeak_rate_minor=50_000,
            opening_hour=6,
            closing_hour=22,
            hold_ttl_seconds=300,
            payments_enabled=False,
        )
        session.add(venue)
        session.flush()
        for day in range(1, 8):
            session.add(PeakWindow(venue_id=venue.id, day_of_week=day, start_hour=17, end_hour=21))
        for name in ("Court 1", "Court 2"):
            session.add(Court(id=uuid.uuid4(), venue_id=venue.id, name=name, active=True))
        member = Member(id=uuid.uuid4())
        session.add(member)
        print(f"seed: venue {venue.id}")
        print(f"seed: demo member {member.id}")
    engine.dispose()


if __name__ == "__main__":
    main()
