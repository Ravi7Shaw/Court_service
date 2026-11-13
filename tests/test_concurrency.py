"""The guarantees under real concurrency: many threads, separate connections, one database.

These call the use cases directly, through the same transaction runner the HTTP layer uses,
so the races are real database races and not artefacts of a test client.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import run_tx
from app.errors import ApiError
from app.models import Reservation
from app.services import holds
from tests.conftest import Venue_, slot_on


@dataclass(frozen=True)
class Outcome:
    ok: bool
    code: str | None
    reservation_id: uuid.UUID | None


def _attempt(job: Callable[[], Reservation]) -> Outcome:
    try:
        reservation = job()
    except ApiError as exc:
        return Outcome(False, exc.code, None)
    return Outcome(True, None, reservation.id)


def test_fifty_members_race_for_one_slot_and_exactly_one_wins(
    venue: Venue_, factory: sessionmaker[Session]
) -> None:
    start, end = slot_on(14, 9)
    members = [uuid.uuid4() for _ in range(50)]

    def run(member: uuid.UUID) -> Outcome:
        return _attempt(
            lambda: run_tx(
                factory,
                lambda s: holds.create_hold(s, member, venue.court_a, start, end, None).reservation,
            )
        )

    with ThreadPoolExecutor(max_workers=50) as pool:
        outcomes = list(pool.map(run, members))

    winners = [o for o in outcomes if o.ok]
    losers = [o for o in outcomes if not o.ok]
    assert len(winners) == 1
    assert {o.code for o in losers} == {"slot_unavailable"}
    with factory() as session:
        live = session.scalar(
            select(func.count()).select_from(Reservation).where(Reservation.status == "held")
        )
    assert live == 1


def test_one_member_cannot_exceed_three_bookings_under_concurrency(
    venue: Venue_, factory: sessionmaker[Session]
) -> None:
    member = uuid.uuid4()
    slots = [slot_on(15 + day, hour) for day in range(4) for hour in (8, 9, 10)][:12]

    def job(index: int) -> Outcome:
        start, end = slots[index]
        return _attempt(
            lambda: run_tx(
                factory,
                lambda s: holds.create_hold(s, member, venue.court_a, start, end, None).reservation,
            )
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(job, range(12)))

    assert sum(o.ok for o in outcomes) == 3
    assert {o.code for o in outcomes if not o.ok} == {"booking_limit_reached"}


def test_retried_confirms_produce_one_booking(venue: Venue_, factory: sessionmaker[Session]) -> None:
    member = uuid.uuid4()
    start, end = slot_on(16, 11)
    hold_id = run_tx(
        factory, lambda s: holds.create_hold(s, member, venue.court_a, start, end, None).reservation.id
    )

    def confirm(_: int) -> str:
        result = run_tx(factory, lambda s: holds.confirm_hold(s, member, hold_id))
        return result.reservation.status

    with ThreadPoolExecutor(max_workers=20) as pool:
        statuses = list(pool.map(confirm, range(20)))

    assert set(statuses) == {"confirmed"}
    with factory() as session:
        rows = session.scalar(
            select(func.count()).select_from(Reservation).where(Reservation.court_id == venue.court_a)
        )
    assert rows == 1


def test_concurrent_duplicates_with_one_idempotency_key_share_one_booking(
    venue: Venue_, factory: sessionmaker[Session]
) -> None:
    member = uuid.uuid4()
    start, end = slot_on(17, 12)

    def create(_: int) -> uuid.UUID:
        result = run_tx(
            factory,
            lambda s: holds.create_hold(s, member, venue.court_a, start, end, "retry-me"),
        )
        return result.reservation.id

    with ThreadPoolExecutor(max_workers=20) as pool:
        ids = list(pool.map(create, range(20)))

    assert len(set(ids)) == 1
    with factory() as session:
        rows = session.scalar(select(func.count()).select_from(Reservation))
    assert rows == 1


def test_concurrent_cancels_store_one_refund_decision(venue: Venue_, factory: sessionmaker[Session]) -> None:
    member = uuid.uuid4()
    start, end = slot_on(18, 13)
    hold_id = run_tx(
        factory, lambda s: holds.create_hold(s, member, venue.court_a, start, end, None).reservation.id
    )
    run_tx(factory, lambda s: holds.confirm_hold(s, member, hold_id))

    def cancel(_: int) -> int | None:
        return run_tx(factory, lambda s: holds.cancel_booking(s, member, hold_id)).refund_minor

    with ThreadPoolExecutor(max_workers=10) as pool:
        refunds = list(pool.map(cancel, range(10)))

    assert len(set(refunds)) == 1
    assert refunds[0] == 50_000


def test_expiry_and_confirm_never_both_win(venue: Venue_, factory: sessionmaker[Session]) -> None:
    """A confirm racing a rival's hold on an expired slot: the row lock decides one outcome."""
    member = uuid.uuid4()
    start, end = slot_on(19, 14)
    hold_id = run_tx(
        factory, lambda s: holds.create_hold(s, member, venue.court_a, start, end, None).reservation.id
    )
    with factory() as session, session.begin():
        row = session.get(Reservation, hold_id)
        assert row is not None
        row.hold_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    def confirm(_: int) -> str:
        try:
            return run_tx(factory, lambda s: holds.confirm_hold(s, member, hold_id)).reservation.status
        except ApiError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = set(pool.map(confirm, range(8)))
    assert results <= {"expired", "confirmed"}
    assert "confirmed" not in results
