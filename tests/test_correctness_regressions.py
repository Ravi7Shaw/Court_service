from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.models import Member, Reservation
from tests.conftest import Venue_, make_venue, member_headers, slot_on
from tests.test_api import hold


def test_idempotency_key_reuse_with_different_payload_is_rejected(
    client: TestClient, venue: Venue_
) -> None:
    member = uuid.uuid4()
    start, end = slot_on(10, 8)
    first = hold(client, member, venue.court_a, start, end, key="payload-fingerprint-test")
    other_start, other_end = slot_on(10, 9)
    replay = hold(client, member, venue.court_a, other_start, other_end, key="payload-fingerprint-test")

    assert first.status_code == 201
    assert replay.status_code == 422
    assert replay.json()["code"] == "idempotency_key_reused"


def test_expired_hold_no_longer_blocks_recurring_booking(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    quick = make_venue(factory, name="Quick recurring hold", hold_ttl=1)
    start, end = slot_on(12, 10)
    first = hold(client, uuid.uuid4(), quick.court_a, start, end)
    assert first.status_code == 201

    time.sleep(1.2)
    response = client.post(
        "/recurring-bookings",
        headers=member_headers(uuid.uuid4()),
        json={
            "court_id": str(quick.court_a),
            "starts_at": start.isoformat(),
            "ends_at": end.isoformat(),
            "occurrences": 1,
        },
    )
    assert response.status_code == 201


def test_expired_payment_attempt_no_longer_blocks_new_hold(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    quick = make_venue(factory, name="Expired payment attempt")
    member = uuid.uuid4()
    start, end = slot_on(12, 11)

    with factory() as session, session.begin():
        session.add(Member(id=member))
        session.add(
            Reservation(
                court_id=quick.court_a,
                member_id=member,
                status="payment_pending",
                starts_at=start,
                ends_at=end,
                price_minor=50_000,
                currency="INR",
                hold_expires_at=datetime.now(UTC) + timedelta(hours=1),
                payment_hard_deadline=datetime.now(UTC) - timedelta(seconds=1),
            )
        )

    created = hold(client, uuid.uuid4(), quick.court_a, start, end)
    assert created.status_code == 201


def test_payment_pending_reservations_count_toward_member_cap_and_availability(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    quick = make_venue(factory, name="Live payment attempts")
    member = uuid.uuid4()
    slot_ranges = [slot_on(13, hour) for hour in (8, 9, 10)]

    with factory() as session, session.begin():
        session.add(Member(id=member))
        for start, end in slot_ranges:
            session.add(
                Reservation(
                    court_id=quick.court_a,
                    member_id=member,
                    status="payment_pending",
                    starts_at=start,
                    ends_at=end,
                    price_minor=50_000,
                    currency="INR",
                    hold_expires_at=datetime.now(UTC) + timedelta(hours=2),
                    payment_hard_deadline=datetime.now(UTC) + timedelta(hours=1),
                )
            )

    next_start, next_end = slot_on(13, 11)
    fourth = hold(client, member, quick.court_a, next_start, next_end)
    assert fourth.status_code == 409
    assert fourth.json()["code"] == "booking_limit_reached"

    busy_start, busy_end = slot_ranges[0]
    availability = client.get(
        f"/courts/{quick.court_a}/availability",
        params={"from": busy_start.isoformat(), "to": busy_end.isoformat()},
    )
    assert availability.status_code == 200
    matching = [
        slot for slot in availability.json()["slots"]
        if datetime.fromisoformat(slot["starts_at"]) == busy_start
    ]
    assert len(matching) == 1
    assert matching[0]["available"] is False


def test_availability_rejects_timezone_naive_timestamps(client: TestClient, venue: Venue_) -> None:
    start, end = slot_on(7, 9)
    response = client.get(
        f"/courts/{venue.court_a}/availability",
        params={
            "from": start.replace(tzinfo=None).isoformat(),
            "to": end.replace(tzinfo=None).isoformat(),
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_interval"
