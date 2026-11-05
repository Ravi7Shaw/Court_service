from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.rules import (
    local_occurrence_start,
    local_wall_time_exists,
    price_minor,
    validate_interval,
)
from app.errors import ApiError
from app.models import RecurringSeries, Reservation
from app.services.common import (
    MAX_ACTIVE_BOOKINGS,
    db_now,
    is_exclusion_violation,
    live_booking_count,
    load_court,
    lock_member,
)

MAX_OCCURRENCES = 3


@dataclass(frozen=True)
class SeriesResult:
    series: RecurringSeries
    reservations: list[Reservation]


def create_series(
    session: Session,
    member_id: uuid.UUID,
    court_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    occurrences: int,
) -> SeriesResult:
    """Create every weekly occurrence in one transaction, or none at all.

    Each occurrence keeps the same local start time seven days after the last.
    """
    if not 1 <= occurrences <= MAX_OCCURRENCES:
        raise ApiError("invalid_interval", 422, f"a series has 1 to {MAX_OCCURRENCES} occurrences")
    ctx = load_court(session, court_id)
    if ctx.venue.payments_enabled:
        raise ApiError("payments_not_supported", 501, "online payment is not available yet")
    hours = validate_interval(starts_at, ends_at, ctx.tz, ctx.window)
    lock_member(session, member_id)
    now = db_now(session)
    if starts_at <= now:
        raise ApiError("invalid_interval", 422, "a series must start in the future")
    if live_booking_count(session, member_id, now) + occurrences > MAX_ACTIVE_BOOKINGS:
        raise ApiError("booking_limit_reached", 409, "a member can hold at most three active bookings")

    series = RecurringSeries(member_id=member_id, court_id=court_id, occurrences=occurrences)
    session.add(series)
    session.flush()

    first_local = starts_at.astimezone(ctx.tz)
    reservations: list[Reservation] = []
    for week in range(occurrences):
        local_start = local_occurrence_start(first_local, week)
        if not local_wall_time_exists(local_start, ctx.tz):
            raise ApiError("invalid_interval", 422, "an occurrence falls on a local time that does not exist")
        start = local_start.astimezone(UTC)
        end = start + (ends_at - starts_at)
        validate_interval(start, end, ctx.tz, ctx.window)
        price = price_minor(
            start, hours, ctx.tz, ctx.peaks, ctx.venue.peak_rate_minor, ctx.venue.offpeak_rate_minor
        )
        reservation = Reservation(
            court_id=court_id,
            member_id=member_id,
            series_id=series.id,
            status="confirmed",
            starts_at=start,
            ends_at=end,
            price_minor=price,
            currency=ctx.venue.currency,
            confirmed_at=now,
        )
        session.add(reservation)
        reservations.append(reservation)

    try:
        session.flush()
    except IntegrityError as exc:
        if is_exclusion_violation(exc):
            raise ApiError("slot_unavailable", 409, "one of the occurrences clashes with a booking") from exc
        raise
    return SeriesResult(series, reservations)
