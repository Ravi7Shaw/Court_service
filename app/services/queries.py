from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.domain.rules import HOUR, price_minor
from app.errors import ApiError
from app.models import Court, Reservation, Venue
from app.services.common import db_now, load_court


@dataclass(frozen=True)
class SlotView:
    starts_at: datetime
    ends_at: datetime
    price_minor: int
    currency: str
    available: bool


@dataclass(frozen=True)
class CourtUtilization:
    court_id: uuid.UUID
    court_name: str
    booked_hours: float
    available_hours: int
    utilization: float | None


def availability(
    session: Session, court_id: uuid.UUID, range_from: datetime, range_to: datetime
) -> list[SlotView]:
    """A snapshot of hourly slots. Reads never lock. A stale view can only cause a 409 on hold."""
    if range_to <= range_from or range_to - range_from > timedelta(days=7):
        raise ApiError("invalid_interval", 422, "the range must be between 1 second and 7 days")
    ctx = load_court(session, court_id)
    now = db_now(session)

    starts: list[datetime] = []
    day = range_from.astimezone(ctx.tz).date() - timedelta(days=1)
    last = range_to.astimezone(ctx.tz).date() + timedelta(days=1)
    while day <= last:
        for hour in range(ctx.window.opening_hour, ctx.window.closing_hour):
            start = datetime.combine(day, time(hour), tzinfo=ctx.tz).astimezone(UTC)
            if range_from <= start < range_to:
                starts.append(start)
        day += timedelta(days=1)

    busy = session.scalars(
        select(Reservation).where(
            Reservation.court_id == court_id,
            Reservation.starts_at < range_to,
            Reservation.ends_at > range_from,
            or_(
                Reservation.status == "confirmed",
                and_(Reservation.status == "held", Reservation.hold_expires_at > now),
            ),
        )
    ).all()

    views: list[SlotView] = []
    for start in starts:
        end = start + HOUR
        taken = any(b.starts_at < end and b.ends_at > start for b in busy)
        views.append(
            SlotView(
                starts_at=start,
                ends_at=end,
                price_minor=price_minor(
                    start, 1, ctx.tz, ctx.peaks, ctx.venue.peak_rate_minor, ctx.venue.offpeak_rate_minor
                ),
                currency=ctx.venue.currency,
                available=not taken and start > now,
            )
        )
    return views


def weekly_utilization(session: Session, venue_id: uuid.UUID, week_start: date) -> list[CourtUtilization]:
    """Scheduled utilization per active court for one Monday-to-Monday week in the venue's time zone.

    Available hours are the venue's opening hours for seven days. Held, expired and
    cancelled bookings are excluded, and attendance is not measured.
    """
    if week_start.weekday() != 0:
        raise ApiError("invalid_interval", 422, "week_start must be a Monday")
    venue = session.get(Venue, venue_id)
    if venue is None:
        raise ApiError("not_found", 404, "venue not found")
    tz = ZoneInfo(venue.timezone)
    week_from = datetime.combine(week_start, time(0), tzinfo=tz).astimezone(UTC)
    week_to = datetime.combine(week_start + timedelta(days=7), time(0), tzinfo=tz).astimezone(UTC)
    available_hours = 7 * (venue.closing_hour - venue.opening_hour)

    courts = session.scalars(
        select(Court).where(Court.venue_id == venue_id, Court.active.is_(True)).order_by(Court.name)
    ).all()
    result: list[CourtUtilization] = []
    for court in courts:
        rows = session.scalars(
            select(Reservation).where(
                Reservation.court_id == court.id,
                Reservation.status == "confirmed",
                Reservation.starts_at < week_to,
                Reservation.ends_at > week_from,
            )
        ).all()
        booked = sum((min(r.ends_at, week_to) - max(r.starts_at, week_from) for r in rows), timedelta())
        booked_hours = round(booked.total_seconds() / 3600, 2)
        result.append(
            CourtUtilization(
                court_id=court.id,
                court_name=court.name,
                booked_hours=booked_hours,
                available_hours=available_hours,
                utilization=round(booked_hours / available_hours, 4) if available_hours else None,
            )
        )
    return result
