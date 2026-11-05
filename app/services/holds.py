from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.rules import hold_is_live, price_minor, refund_minor, validate_interval
from app.errors import ApiError
from app.models import Reservation
from app.services.common import (
    MAX_ACTIVE_BOOKINGS,
    attach_reservation_to_key,
    claim_idempotency_key,
    db_now,
    ensure_member,
    is_exclusion_violation,
    live_booking_count,
    load_court,
    lock_member,
    stored_reservation_for_key,
)

HOLD_OPERATION = "POST /holds"


@dataclass(frozen=True)
class HoldResult:
    reservation: Reservation
    created: bool


@dataclass(frozen=True)
class ConfirmResult:
    reservation: Reservation
    expired_now: bool


def create_hold(
    session: Session,
    member_id: uuid.UUID,
    court_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    idempotency_key: str | None,
) -> HoldResult:
    ctx = load_court(session, court_id)
    if ctx.venue.payments_enabled:
        raise ApiError("payments_not_supported", 501, "online payment is not available yet")
    hours = validate_interval(starts_at, ends_at, ctx.tz, ctx.window)

    # Lock order: idempotency key, then member, then overlapping rows, then the insert.
    # The member row must exist before the key row, which references it.
    ensure_member(session, member_id)
    if idempotency_key is not None and not claim_idempotency_key(
        session, member_id, HOLD_OPERATION, idempotency_key
    ):
        existing = stored_reservation_for_key(session, member_id, HOLD_OPERATION, idempotency_key)
        reservation = session.get(Reservation, existing) if existing is not None else None
        if reservation is None:
            raise ApiError("retry_later", 503, "the first request with this key is still running")
        return HoldResult(reservation, created=False)

    lock_member(session, member_id)
    overlapping = session.scalars(
        select(Reservation)
        .where(
            Reservation.court_id == court_id,
            Reservation.status.in_(("held", "payment_pending")),
            Reservation.starts_at < ends_at,
            Reservation.ends_at > starts_at,
        )
        .order_by(Reservation.id)
        .with_for_update()
    ).all()
    now = db_now(session)
    if starts_at <= now:
        raise ApiError("invalid_interval", 422, "a booking must start in the future")

    # Expire stale holds before the insert, so the constraint never sees a dead hold as live.
    for stale in overlapping:
        if stale.status == "held" and not hold_is_live(now, stale.hold_expires_at):
            stale.status = "expired"

    if live_booking_count(session, member_id, now) >= MAX_ACTIVE_BOOKINGS:
        raise ApiError("booking_limit_reached", 409, "a member can hold at most three active bookings")

    price = price_minor(
        starts_at, hours, ctx.tz, ctx.peaks, ctx.venue.peak_rate_minor, ctx.venue.offpeak_rate_minor
    )
    reservation = Reservation(
        court_id=court_id,
        member_id=member_id,
        status="held",
        starts_at=starts_at,
        ends_at=ends_at,
        price_minor=price,
        currency=ctx.venue.currency,
        hold_expires_at=min(now + timedelta(seconds=ctx.venue.hold_ttl_seconds), starts_at),
    )
    session.add(reservation)
    try:
        session.flush()
    except IntegrityError as exc:
        if is_exclusion_violation(exc):
            raise ApiError("slot_unavailable", 409, "that slot is already taken") from exc
        raise
    if idempotency_key is not None:
        attach_reservation_to_key(session, member_id, HOLD_OPERATION, idempotency_key, reservation.id)
    return HoldResult(reservation, created=True)


def _owned_reservation(session: Session, member_id: uuid.UUID, reservation_id: uuid.UUID) -> Reservation:
    reservation = session.execute(
        select(Reservation).where(Reservation.id == reservation_id).with_for_update()
    ).scalar_one_or_none()
    if reservation is None or reservation.member_id != member_id:
        raise ApiError("not_found", 404, "booking not found")
    return reservation


def confirm_hold(session: Session, member_id: uuid.UUID, hold_id: uuid.UUID) -> ConfirmResult:
    """Confirm a live hold. Retrying a confirm returns the same booking, never a second one.

    An expired hold is marked expired and returned, so the caller can commit that
    change and then answer 410.
    """
    reservation = _owned_reservation(session, member_id, hold_id)
    if reservation.status == "confirmed":
        return ConfirmResult(reservation, expired_now=False)
    if reservation.status == "expired":
        return ConfirmResult(reservation, expired_now=True)
    if reservation.status != "held":
        raise ApiError("not_confirmable", 409, f"a {reservation.status} booking cannot be confirmed")
    now = db_now(session)
    if not hold_is_live(now, reservation.hold_expires_at):
        reservation.status = "expired"
        return ConfirmResult(reservation, expired_now=True)
    if reservation.starts_at <= now:
        raise ApiError("already_started", 409, "the slot has already started")
    reservation.status = "confirmed"
    reservation.confirmed_at = now
    return ConfirmResult(reservation, expired_now=False)


def cancel_booking(session: Session, member_id: uuid.UUID, booking_id: uuid.UUID) -> Reservation:
    """Cancel a confirmed booking and store the refund decision. A repeat returns the stored decision."""
    reservation = _owned_reservation(session, member_id, booking_id)
    if reservation.status == "cancelled":
        return reservation
    if reservation.status != "confirmed":
        raise ApiError("not_cancellable", 409, f"a {reservation.status} booking cannot be cancelled")
    now = db_now(session)
    if reservation.starts_at <= now:
        raise ApiError("already_started", 409, "the slot has already started")
    reservation.status = "cancelled"
    reservation.cancelled_at = now
    reservation.refund_minor = refund_minor(now, reservation.starts_at, reservation.price_minor)
    return reservation
