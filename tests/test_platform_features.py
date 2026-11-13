from __future__ import annotations


from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import User
from tests.conftest import Venue_, slot_on


def register_and_login(client: TestClient, email: str = "player@gmail.com") -> tuple[str, str]:
    registered = client.post("/api/auth/register", json={"email": email, "password": "StrongPassword-2026!"})
    assert registered.status_code == 201, registered.text
    token = registered.json()["debug_verification_token"]
    verified = client.post("/api/auth/verify-email", json={"token": token})
    assert verified.status_code == 200, verified.text
    logged_in = client.post("/api/auth/login", json={"email": email, "password": "StrongPassword-2026!"})
    assert logged_in.status_code == 200, logged_in.text
    return logged_in.json()["access_token"], registered.json()["id"]


def test_registration_verification_payment_cancel_and_refund(
    client: TestClient, venue: Venue_
) -> None:
    access_token, _ = register_and_login(client)
    headers = {"Authorization": f"Bearer {access_token}"}
    start, end = slot_on(5, 10)
    created = client.post("/holds", headers=headers, json={
        "court_id": str(venue.court_a), "starts_at": start.isoformat(), "ends_at": end.isoformat()
    })
    assert created.status_code == 201, created.text
    reservation_id = created.json()["id"]

    paid = client.post(f"/api/payments/{reservation_id}/simulate", headers=headers, json={})
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "paid"

    cancelled = client.post(f"/bookings/{reservation_id}/cancel", headers=headers, json={})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["refund_minor"] > 0

    refunded = client.post(f"/api/payments/{reservation_id}/refund/simulate", headers=headers, json={})
    assert refunded.status_code == 200, refunded.text
    assert refunded.json()["status"] == "refunded"

    notifications = client.get("/api/notifications", headers=headers)
    assert notifications.status_code == 200
    kinds = {item["kind"] for item in notifications.json()}
    assert {"payment", "cancellation", "refund"}.issubset(kinds)


def test_location_requires_consent_and_can_be_deleted(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    access_token, _ = register_and_login(client, "location@gmail.com")
    headers = {"Authorization": f"Bearer {access_token}"}
    denied = client.post("/api/me/location", headers=headers,
                         json={"latitude": 12.9716, "longitude": 77.5946, "consent": False})
    assert denied.status_code == 400
    saved = client.post("/api/me/location", headers=headers,
                        json={"latitude": 12.9716, "longitude": 77.5946, "consent": True})
    assert saved.status_code == 200
    deleted = client.delete("/api/me/location", headers=headers)
    assert deleted.status_code == 200
    nearby = client.get("/api/courts/nearby", headers=headers)
    assert nearby.status_code == 404


def test_admin_can_create_court_and_member_cannot(
    client: TestClient, factory: sessionmaker[Session], venue: Venue_
) -> None:
    access_token, _ = register_and_login(client, "admin-test@gmail.com")
    with factory() as session, session.begin():
        user = session.scalar(select(User).where(User.email == "admin-test@gmail.com"))
        assert user is not None
        user.role = "admin"
    admin_headers = {"Authorization": f"Bearer {access_token}"}
    created = client.post("/api/admin/courts", headers=admin_headers,
                          json={"venue_id": str(venue.venue_id), "name": "Court D"})
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "Court D"

    member_token, _ = register_and_login(client, "member-test@gmail.com")
    denied = client.post("/api/admin/courts", headers={"Authorization": f"Bearer {member_token}"},
                         json={"venue_id": str(venue.venue_id), "name": "Court E"})
    assert denied.status_code == 403
