"""The database itself refuses bad data, whatever the application does."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import Venue_


def _insert(
    session: Session, court: uuid.UUID, member: uuid.UUID, start: datetime, status: str, confirmed: bool
) -> None:
    session.execute(
        text(
            """
            INSERT INTO reservations
              (id, court_id, member_id, status, starts_at, ends_at, price_minor, currency,
               hold_expires_at, confirmed_at)
            VALUES (:id, :court, :member, :status, :start, :end, 50000, 'INR',
                    :hold, :confirmed)
            """
        ),
        {
            "id": uuid.uuid4(),
            "court": court,
            "member": member,
            "status": status,
            "start": start,
            "end": start + timedelta(hours=1),
            "hold": None if confirmed else start,
            "confirmed": start if confirmed else None,
        },
    )


def test_database_rejects_two_live_bookings_on_one_court(
    venue: Venue_, factory: sessionmaker[Session]
) -> None:
    start = datetime(2030, 1, 10, 10, 0, tzinfo=UTC)
    member = uuid.uuid4()
    with factory() as session, session.begin():
        session.execute(text("INSERT INTO members (id) VALUES (:id)"), {"id": member})
        _insert(session, venue.court_a, member, start, "confirmed", confirmed=True)
    with pytest.raises(IntegrityError) as caught, factory() as session, session.begin():
        _insert(session, venue.court_a, member, start + timedelta(minutes=30), "confirmed", confirmed=True)
    assert caught.value.orig is not None
    assert getattr(caught.value.orig, "sqlstate", None) == "23P01"


def test_adjacent_bookings_do_not_overlap(venue: Venue_, factory: sessionmaker[Session]) -> None:
    start = datetime(2030, 1, 10, 10, 0, tzinfo=UTC)
    member = uuid.uuid4()
    with factory() as session, session.begin():
        session.execute(text("INSERT INTO members (id) VALUES (:id)"), {"id": member})
        _insert(session, venue.court_a, member, start, "confirmed", confirmed=True)
        _insert(session, venue.court_a, member, start + timedelta(hours=1), "confirmed", confirmed=True)


def test_cancelled_rows_free_the_slot(venue: Venue_, factory: sessionmaker[Session]) -> None:
    start = datetime(2030, 1, 10, 10, 0, tzinfo=UTC)
    member = uuid.uuid4()
    with factory() as session, session.begin():
        session.execute(text("INSERT INTO members (id) VALUES (:id)"), {"id": member})
        session.execute(
            text(
                """
                INSERT INTO reservations (id, court_id, member_id, status, starts_at, ends_at,
                  price_minor, currency, confirmed_at, cancelled_at, refund_minor)
                VALUES (:id, :court, :member, 'cancelled', :start, :end, 50000, 'INR',
                        :start, :start, 50000)
                """
            ),
            {
                "id": uuid.uuid4(),
                "court": venue.court_a,
                "member": member,
                "start": start,
                "end": start + timedelta(hours=1),
            },
        )
        _insert(session, venue.court_a, member, start, "confirmed", confirmed=True)


def test_status_fields_must_agree_with_status(venue: Venue_, factory: sessionmaker[Session]) -> None:
    member = uuid.uuid4()
    start = datetime(2030, 1, 10, 10, 0, tzinfo=UTC)
    with pytest.raises(IntegrityError), factory() as session, session.begin():
        session.execute(text("INSERT INTO members (id) VALUES (:id)"), {"id": member})
        # Confirmed without a confirmed_at timestamp must be refused.
        session.execute(
            text(
                """
                INSERT INTO reservations (id, court_id, member_id, status, starts_at, ends_at,
                  price_minor, currency)
                VALUES (:id, :court, :member, 'confirmed', :start, :end, 50000, 'INR')
                """
            ),
            {
                "id": uuid.uuid4(),
                "court": venue.court_a,
                "member": member,
                "start": start,
                "end": start + timedelta(hours=1),
            },
        )


def test_schema_has_the_no_overlap_constraint(engine: Engine) -> None:
    with engine.connect() as conn:
        found = conn.execute(
            text("SELECT contype FROM pg_constraint WHERE conname = 'reservations_no_overlap'")
        ).scalar_one()
    assert found == "x"  # 'x' is an exclusion constraint in pg_constraint
