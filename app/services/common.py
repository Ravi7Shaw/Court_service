from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db import sqlstate_of
from app.domain.rules import OpeningWindow
from app.domain.rules import PeakWindow as PeakRule
from app.errors import ApiError
from app.models import Court, IdempotencyKey, Member, PeakWindow, Reservation, Venue

MAX_ACTIVE_BOOKINGS = 3
LIVE_PENDING = ("held", "payment_pending")


@dataclass(frozen=True)
class CourtContext:
    court: Court
    venue: Venue
    tz: ZoneInfo
    window: OpeningWindow
    peaks: list[PeakRule]


def db_now(session: Session) -> datetime:
    """The database clock. All expiry and cap decisions use this, never the app host's clock."""
    value: datetime = session.execute(select(func.clock_timestamp())).scalar_one()
    return value


def load_court(session: Session, court_id: uuid.UUID) -> CourtContext:
    court = session.get(Court, court_id)
    if court is None or not court.active:
        raise ApiError("not_found", 404, "court not found")
    venue = session.get(Venue, court.venue_id)
    if venue is None:
        raise ApiError("not_found", 404, "court not found")
    return CourtContext(
        court=court,
        venue=venue,
        tz=ZoneInfo(venue.timezone),
        window=OpeningWindow(venue.opening_hour, venue.closing_hour),
        peaks=load_peaks(session, venue.id),
    )


def load_peaks(session: Session, venue_id: uuid.UUID) -> list[PeakRule]:
    rows = session.scalars(select(PeakWindow).where(PeakWindow.venue_id == venue_id)).all()
    return [PeakRule(r.day_of_week, r.start_hour, r.end_hour) for r in rows]


def ensure_member(session: Session, member_id: uuid.UUID) -> None:
    """Create the member row on first use. Does not lock it, so it is safe before the key claim."""
    session.execute(pg_insert(Member).values(id=member_id).on_conflict_do_nothing(index_elements=["id"]))


def lock_member(session: Session, member_id: uuid.UUID) -> None:
    """Serialise every change that adds bookings for one member (the three-booking cap)."""
    ensure_member(session, member_id)
    session.execute(select(Member.id).where(Member.id == member_id).with_for_update()).one()


def live_booking_count(session: Session, member_id: uuid.UUID, now: datetime) -> int:
    stmt = (
        select(func.count())
        .select_from(Reservation)
        .where(
            Reservation.member_id == member_id,
            or_(
                and_(Reservation.status == "held", Reservation.hold_expires_at > now),
                and_(Reservation.status == "confirmed", Reservation.starts_at > now),
            ),
        )
    )
    return int(session.execute(stmt).scalar_one())


def claim_idempotency_key(session: Session, member_id: uuid.UUID, operation: str, key: str) -> bool:
    """True if this request owns the key. A concurrent duplicate blocks here until the first commits."""
    result = session.execute(
        pg_insert(IdempotencyKey)
        .values(member_id=member_id, operation=operation, key=key, reservation_id=None)
        .on_conflict_do_nothing(index_elements=["member_id", "operation", "key"])
        .returning(IdempotencyKey.key)
    )
    return result.first() is not None


def attach_reservation_to_key(
    session: Session, member_id: uuid.UUID, operation: str, key: str, reservation_id: uuid.UUID
) -> None:
    session.execute(
        update(IdempotencyKey)
        .where(
            IdempotencyKey.member_id == member_id,
            IdempotencyKey.operation == operation,
            IdempotencyKey.key == key,
        )
        .values(reservation_id=reservation_id)
    )


def stored_reservation_for_key(
    session: Session, member_id: uuid.UUID, operation: str, key: str
) -> uuid.UUID | None:
    value: uuid.UUID | None = session.execute(
        select(IdempotencyKey.reservation_id).where(
            IdempotencyKey.member_id == member_id,
            IdempotencyKey.operation == operation,
            IdempotencyKey.key == key,
        )
    ).scalar_one()
    return value


def is_exclusion_violation(exc: BaseException) -> bool:
    return sqlstate_of(exc) == "23P01"
