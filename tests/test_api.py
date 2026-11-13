"""Contract tests through HTTP, against a real PostgreSQL database."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import RecurringSeries, Reservation
from tests.conftest import IST, STAFF_KEY, Venue_, make_venue, member_headers, slot_on


def hold(
    client: TestClient,
    member: uuid.UUID,
    court: uuid.UUID,
    start: datetime,
    end: datetime,
    key: str | None = None,
) -> httpx.Response:
    headers = member_headers(member)
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(
        "/holds",
        headers=headers,
        json={"court_id": str(court), "starts_at": start.isoformat(), "ends_at": end.isoformat()},
    )


def test_hold_confirm_and_cancel_with_full_refund(client: TestClient, venue: Venue_) -> None:
    member = uuid.uuid4()
    start, end = slot_on(5, 10)
    created = hold(client, member, venue.court_a, start, end)
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "held"
    assert body["price_minor"] == 50_000  # 10:00 is off-peak

    confirmed = client.post(f"/holds/{body['id']}/confirm", headers=member_headers(member))
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"

    again = client.post(f"/holds/{body['id']}/confirm", headers=member_headers(member))
    assert again.status_code == 200
    assert again.json()["id"] == body["id"]

    cancelled = client.post(f"/bookings/{body['id']}/cancel", headers=member_headers(member))
    assert cancelled.status_code == 200
    assert cancelled.json()["refund"] == "full"
    assert cancelled.json()["refund_minor"] == 50_000

    repeat = client.post(f"/bookings/{body['id']}/cancel", headers=member_headers(member))
    assert repeat.json()["refund_minor"] == 50_000


def test_cancelling_inside_twenty_four_hours_refunds_nothing(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    open_all_day = make_venue(factory, name="Always open", opening=0, closing=24)
    member = uuid.uuid4()
    now_ist = datetime.now(IST)
    start = now_ist.replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
    if start.date() != (start + timedelta(hours=1)).date():
        start = start - timedelta(hours=2)
    created = hold(client, member, open_all_day.court_a, start, start + timedelta(hours=1))
    assert created.status_code == 201
    booking = created.json()["id"]
    client.post(f"/holds/{booking}/confirm", headers=member_headers(member))
    cancelled = client.post(f"/bookings/{booking}/cancel", headers=member_headers(member))
    assert cancelled.json()["refund"] == "none"
    assert cancelled.json()["refund_minor"] == 0


def test_slot_already_taken_gives_409(client: TestClient, venue: Venue_) -> None:
    start, end = slot_on(6, 11)
    assert hold(client, uuid.uuid4(), venue.court_a, start, end).status_code == 201
    second = hold(client, uuid.uuid4(), venue.court_a, start, end)
    assert second.status_code == 409
    assert second.json()["code"] == "slot_unavailable"


def test_the_other_court_at_the_same_time_is_free(client: TestClient, venue: Venue_) -> None:
    start, end = slot_on(6, 12)
    assert hold(client, uuid.uuid4(), venue.court_a, start, end).status_code == 201
    assert hold(client, uuid.uuid4(), venue.court_b, start, end).status_code == 201


def test_three_active_bookings_is_the_cap(client: TestClient, venue: Venue_) -> None:
    member = uuid.uuid4()
    for hour in (9, 10, 11):
        start, end = slot_on(7, hour)
        assert hold(client, member, venue.court_a, start, end).status_code == 201
    start, end = slot_on(7, 12)
    fourth = hold(client, member, venue.court_a, start, end)
    assert fourth.status_code == 409
    assert fourth.json()["code"] == "booking_limit_reached"


def test_expired_hold_returns_410_and_frees_the_slot(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    quick = make_venue(factory, name="Quick hold", hold_ttl=1)
    first, second = uuid.uuid4(), uuid.uuid4()
    start, end = slot_on(8, 9)
    held = hold(client, first, quick.court_a, start, end).json()
    time.sleep(1.5)
    assert hold(client, second, quick.court_a, start, end).status_code == 201
    late = client.post(f"/holds/{held['id']}/confirm", headers=member_headers(first))
    assert late.status_code == 410
    assert late.json()["code"] == "hold_expired"


def test_confirming_someone_elses_hold_is_not_found(client: TestClient, venue: Venue_) -> None:
    owner, stranger = uuid.uuid4(), uuid.uuid4()
    start, end = slot_on(8, 14)
    held = hold(client, owner, venue.court_a, start, end).json()
    response = client.post(f"/holds/{held['id']}/confirm", headers=member_headers(stranger))
    assert response.status_code == 404


def test_availability_shows_held_slots_and_prices(client: TestClient, venue: Venue_) -> None:
    start, end = slot_on(9, 18)
    assert hold(client, uuid.uuid4(), venue.court_a, start, end).status_code == 201
    day_start = start.replace(hour=6)
    day_end = start.replace(hour=22)
    response = client.get(
        f"/courts/{venue.court_a}/availability",
        params={"from": day_start.isoformat(), "to": day_end.isoformat()},
    )
    assert response.status_code == 200
    slots = response.json()["slots"]
    prices = {slot["price_minor"] for slot in slots}
    assert 80_000 in prices  # 17:00 to 21:00 is peak
    assert 50_000 in prices
    held = [s for s in slots if datetime.fromisoformat(s["starts_at"]) == start]
    assert len(held) == 1
    assert held[0]["available"] is False
    assert len([s for s in slots if not s["available"]]) == 1


def test_idempotent_hold_replay_returns_the_same_booking(
    client: TestClient, venue: Venue_, factory: sessionmaker[Session]
) -> None:
    member = uuid.uuid4()
    start, end = slot_on(10, 8)
    first = hold(client, member, venue.court_a, start, end, key="same-key")
    replay = hold(client, member, venue.court_a, start, end, key="same-key")
    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Reservation)) == 1


def test_staff_utilization_needs_the_staff_key(client: TestClient, venue: Venue_) -> None:
    no_key = client.get(f"/staff/utilization/weekly?venue_id={venue.venue_id}&week_start=2026-11-02")
    assert no_key.status_code == 403
    wrong = client.get(
        f"/staff/utilization/weekly?venue_id={venue.venue_id}&week_start=2026-11-02",
        headers={"X-Staff-Key": "nope"},
    )
    assert wrong.status_code == 403


def test_staff_utilization_counts_confirmed_hours(client: TestClient, venue: Venue_) -> None:
    member = uuid.uuid4()
    start, end = slot_on(11, 10)
    created = hold(client, member, venue.court_a, start, end).json()
    client.post(f"/holds/{created['id']}/confirm", headers=member_headers(member))
    local = start.astimezone(IST).date()
    week_start = local - timedelta(days=local.weekday())
    response = client.get(
        f"/staff/utilization/weekly?venue_id={venue.venue_id}&week_start={week_start.isoformat()}",
        headers={"X-Staff-Key": STAFF_KEY},
    )
    assert response.status_code == 200
    court_a = next(c for c in response.json()["courts"] if c["court_name"] == "Court A")
    assert court_a["booked_hours"] == 1.0
    assert court_a["available_hours"] == 7 * 16
    assert court_a["utilization"] == round(1 / (7 * 16), 4)


def test_recurring_series_is_all_or_nothing(
    client: TestClient, venue: Venue_, factory: sessionmaker[Session]
) -> None:
    member, other = uuid.uuid4(), uuid.uuid4()
    first_start, first_end = slot_on(12, 15)
    blocker_start, blocker_end = slot_on(12 + 14, 15)  # the third weekly occurrence
    assert hold(client, other, venue.court_a, blocker_start, blocker_end).status_code == 201

    response = client.post(
        "/recurring-bookings",
        headers=member_headers(member),
        json={
            "court_id": str(venue.court_a),
            "starts_at": first_start.isoformat(),
            "ends_at": first_end.isoformat(),
            "occurrences": 3,
        },
    )
    assert response.status_code == 409
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(RecurringSeries)) == 0
        mine = session.scalar(
            select(func.count()).select_from(Reservation).where(Reservation.member_id == member)
        )
        assert mine == 0


def test_recurring_series_books_every_week(client: TestClient, venue: Venue_) -> None:
    member = uuid.uuid4()
    start, end = slot_on(20, 9)
    response = client.post(
        "/recurring-bookings",
        headers=member_headers(member),
        json={
            "court_id": str(venue.court_a),
            "starts_at": start.isoformat(),
            "ends_at": end.isoformat(),
            "occurrences": 2,
        },
    )
    assert response.status_code == 201
    weeks = response.json()["occurrences"]
    assert len(weeks) == 2
    assert all(w["status"] == "confirmed" for w in weeks)
    assert weeks[1]["starts_at"] != weeks[0]["starts_at"]


def test_readiness_checks_the_database(client: TestClient) -> None:
    assert client.get("/readyz").json() == {"status": "ready"}
    assert client.get("/healthz").json() == {"status": "ok"}
