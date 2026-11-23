"""Nearby search: correctness, bounded work, index use, and the admin controls behind it.

Users are created directly with a verified row and a signed token, not via /api/auth/register,
because registration is rate limited per IP and the whole suite shares one test client IP.
"""
from __future__ import annotations

import math
import random
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import Court, Member, User, Venue
from app.security import create_access_token, hash_password
from app.services.location import DEFAULT_LIMIT, bounding_boxes, haversine_km, nearby_query
from tests.conftest import STAFF_KEY, TEST_URL

SETTINGS = Settings(TEST_URL, STAFF_KEY, 40, 20, admin_approval_required=False)
MG_ROAD = (12.9756, 77.6069)


def make_user(factory: sessionmaker[Session], email: str, role: str = "member") -> dict[str, str]:
    with factory() as session, session.begin():
        member = Member(id=uuid.uuid4())
        session.add(member)
        session.flush()
        user = User(member_id=member.id, email=email, password_hash=hash_password("StrongPassword-2026!"),
                    role=role, is_verified=True, is_active=True)
        session.add(user)
        session.flush()
        token = create_access_token(user.id, SETTINGS)
    return {"Authorization": f"Bearer {token}"}


def add_venue(factory: sessionmaker[Session], name: str, lat: float | None, lon: float | None,
              courts: tuple[str, ...] = ("Court 1",), active: bool = True) -> uuid.UUID:
    with factory() as session, session.begin():
        venue = Venue(id=uuid.uuid4(), name=name, timezone="Asia/Kolkata", currency="INR",
                      peak_rate_minor=80_000, offpeak_rate_minor=50_000, opening_hour=6, closing_hour=22,
                      hold_ttl_seconds=300, payments_enabled=False, latitude=lat, longitude=lon)
        session.add(venue)
        session.flush()
        for court_name in courts:
            session.add(Court(id=uuid.uuid4(), venue_id=venue.id, name=court_name, active=active))
        return venue.id


def share_location(client: TestClient, headers: dict[str, str], point: tuple[float, float]) -> None:
    saved = client.post("/api/me/location", headers=headers,
                        json={"latitude": point[0], "longitude": point[1], "consent": True})
    assert saved.status_code == 200, saved.text


def destination(lat: float, lon: float, bearing_deg: float, distance_km: float) -> tuple[float, float]:
    """Point reached by travelling ``distance_km`` on a great circle from (lat, lon)."""
    ang = distance_km / 6371.0
    phi1, lam1, theta = math.radians(lat), math.radians(lon), math.radians(bearing_deg)
    phi2 = math.asin(math.sin(phi1) * math.cos(ang) + math.cos(phi1) * math.sin(ang) * math.cos(theta))
    lam2 = lam1 + math.atan2(math.sin(theta) * math.sin(ang) * math.cos(phi1),
                             math.cos(ang) - math.sin(phi1) * math.sin(phi2))
    return math.degrees(phi2), (math.degrees(lam2) + 540) % 360 - 180


def in_any_box(lat: float, lon: float, boxes) -> bool:
    return any(b.lat_min <= lat <= b.lat_max and b.lon_min <= lon <= b.lon_max for b in boxes)


# ---------- pure geometry (no database) ----------

def test_haversine_matches_a_known_distance() -> None:
    # London to Paris is about 343.5 km.
    assert haversine_km(51.5074, -0.1278, 48.8566, 2.3522) == pytest.approx(343.5, abs=0.5)


@pytest.mark.parametrize("center", [
    MG_ROAD,
    (0.0, 179.95),      # crosses the antimeridian eastward
    (0.0, -179.95),     # crosses it westward
    (89.8, 10.0),       # near the north pole
    (-89.7, -40.0),     # near the south pole
])
def test_bounding_boxes_contain_every_point_of_the_search_circle(center: tuple[float, float]) -> None:
    radius = 25.0
    boxes = bounding_boxes(center[0], center[1], radius)
    rng = random.Random(7)
    for _ in range(400):
        lat, lon = destination(center[0], center[1], rng.uniform(0, 360), rng.uniform(0, radius))
        assert in_any_box(lat, lon, boxes), (lat, lon, boxes)


def test_bounding_box_splits_at_the_antimeridian_and_uses_full_longitude_at_a_pole() -> None:
    assert len(bounding_boxes(0.0, 179.95, 25.0)) == 2
    assert len(bounding_boxes(0.0, -179.95, 25.0)) == 2
    assert len(bounding_boxes(MG_ROAD[0], MG_ROAD[1], 25.0)) == 1
    polar = bounding_boxes(89.8, 10.0, 25.0)
    assert len(polar) == 1 and polar[0].lon_min == -180.0 and polar[0].lon_max == 180.0


def test_bounding_box_rejects_non_positive_radius() -> None:
    with pytest.raises(ValueError):
        bounding_boxes(0.0, 0.0, 0.0)


# ---------- database behaviour ----------

def test_nearby_returns_only_active_located_courts_in_radius_nearest_first(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    headers = make_user(factory, "near-search@gmail.com")
    share_location(client, headers, MG_ROAD)
    add_venue(factory, "Riverside", 12.9716, 77.5946, courts=("Court 1", "Court 2"))
    add_venue(factory, "Lakeside", 12.8000, 77.6000)                                   # ~19 km, inside 25 km
    add_venue(factory, "Far Away", 13.3000, 77.8000)                                   # ~45 km, outside
    add_venue(factory, "No Coordinates", None, None)                                   # never searchable
    add_venue(factory, "Closed", 12.9750, 77.6070, courts=("Court X",), active=False)  # inactive court

    response = client.get("/api/courts/nearby?radius_km=25", headers=headers)
    assert response.status_code == 200, response.text
    rows = response.json()

    # Riverside has two courts (about 1.4 km), Lakeside one (about 19 km). Far, uncoordinated and inactive are absent.
    assert [(row["venue"], row["name"]) for row in rows] == [
        ("Riverside", "Court 1"), ("Riverside", "Court 2"), ("Lakeside", "Court 1"),
    ]
    distances = [row["distance_km"] for row in rows]
    assert distances == sorted(distances)
    assert all(d <= 25 for d in distances)
    assert distances[0] == pytest.approx(haversine_km(*MG_ROAD, 12.9716, 77.5946), abs=0.01)


def test_sql_distance_agrees_with_the_python_reference(client: TestClient, factory: sessionmaker[Session]) -> None:
    headers = make_user(factory, "distance-check@gmail.com")
    share_location(client, headers, MG_ROAD)
    rng = random.Random(11)
    venues: list[tuple[float, float]] = []
    for index in range(12):
        point = destination(MG_ROAD[0], MG_ROAD[1], rng.uniform(0, 360), rng.uniform(0.5, 24.0))
        venues.append(point)
        add_venue(factory, f"V{index}", point[0], point[1], courts=(f"C{index}",))
    rows = client.get("/api/courts/nearby?radius_km=25", headers=headers).json()
    assert len(rows) == 12
    for row in rows:
        venue_index = int(row["venue"][1:])
        expected = haversine_km(MG_ROAD[0], MG_ROAD[1], *venues[venue_index])
        assert row["distance_km"] == pytest.approx(expected, abs=0.011)


def test_nearby_crosses_the_antimeridian(client: TestClient, factory: sessionmaker[Session]) -> None:
    headers = make_user(factory, "dateline@gmail.com")
    share_location(client, headers, (0.0, 179.95))
    add_venue(factory, "Across The Line", 0.0, -179.96)  # 0.09 degrees east over the 180 line, about 10 km
    add_venue(factory, "Same Hemisphere", 0.0, 179.00)   # about 100 km west: must not match
    rows = client.get("/api/courts/nearby?radius_km=25", headers=headers).json()
    assert [row["venue"] for row in rows] == ["Across The Line"]
    assert rows[0]["distance_km"] == pytest.approx(10.0, abs=0.05)


def test_nearby_result_size_is_capped(client: TestClient, factory: sessionmaker[Session]) -> None:
    headers = make_user(factory, "crowded@gmail.com")
    share_location(client, headers, MG_ROAD)
    many = tuple(f"Court {i:02d}" for i in range(DEFAULT_LIMIT + 10))
    add_venue(factory, "Busy Venue", 12.9720, 77.6000, courts=many)
    rows = client.get("/api/courts/nearby?radius_km=25", headers=headers).json()
    assert len(rows) == DEFAULT_LIMIT


def test_nearby_uses_the_coordinate_index_not_a_full_scan(factory: sessionmaker[Session]) -> None:
    """Planner check: with sequential scans discouraged, the box predicate must use the partial index."""
    add_venue(factory, "Indexed", 12.9716, 77.5946)
    statement = nearby_query(MG_ROAD[0], MG_ROAD[1], 25.0)
    compiled = statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    with factory() as session, session.begin():
        session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(row[0] for row in session.execute(text(f"EXPLAIN {compiled}")).all())
    assert "ix_venues_geo_box" in plan, plan


def test_nearby_requires_a_saved_location_and_a_valid_radius(client: TestClient,
                                                             factory: sessionmaker[Session]) -> None:
    headers = make_user(factory, "no-location@gmail.com")
    missing = client.get("/api/courts/nearby?radius_km=25", headers=headers)
    assert missing.status_code == 404 and missing.json()["code"] == "location_not_shared"
    share_location(client, headers, MG_ROAD)
    for bad in ("0", "101", "-3"):
        assert client.get(f"/api/courts/nearby?radius_km={bad}", headers=headers).status_code == 422


def test_admin_changes_reach_members_immediately_through_the_court_cache(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    admin = make_user(factory, "ops-admin@gmail.com", role="admin")
    member = make_user(factory, "rider@gmail.com")
    venue_id = add_venue(factory, "Cached", 12.9716, 77.5946, courts=("Court Z",))
    share_location(client, member, MG_ROAD)
    court_id = next(c["id"] for c in client.get("/api/courts").json() if c["name"] == "Court Z")
    assert any(c["id"] == court_id for c in client.get("/api/courts").json())  # warms the 30-second cache
    assert client.get("/api/courts/nearby?radius_km=25", headers=member).json()

    hidden = client.patch(f"/api/admin/courts/{court_id}", headers=admin, json={"active": False})
    assert hidden.status_code == 200, hidden.text
    assert not any(c["id"] == court_id for c in client.get("/api/courts").json())
    assert client.get("/api/courts/nearby?radius_km=25", headers=member).json() == []

    shown = client.patch(f"/api/admin/courts/{court_id}", headers=admin, json={"active": True})
    assert shown.status_code == 200
    assert any(c["id"] == court_id for c in client.get("/api/courts").json())
    assert venue_id is not None


def test_clearing_a_venue_location_removes_its_courts_from_nearby(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    admin = make_user(factory, "geo-admin@gmail.com", role="admin")
    member = make_user(factory, "walker@gmail.com")
    share_location(client, member, MG_ROAD)
    venue_id = add_venue(factory, "Movable", 12.9716, 77.5946)
    assert len(client.get("/api/courts/nearby?radius_km=25", headers=member).json()) == 1

    cleared = client.delete(f"/api/admin/venues/{venue_id}/coordinates", headers=admin)
    assert cleared.status_code == 200, cleared.text
    assert client.get("/api/courts/nearby?radius_km=25", headers=member).json() == []
    venue_row = next(v for v in client.get("/api/admin/venues", headers=admin).json() if v["id"] == str(venue_id))
    assert venue_row["location_set"] is False and venue_row["latitude"] is None


def test_admin_venue_listing_reports_coordinates_and_courts(client: TestClient,
                                                            factory: sessionmaker[Session]) -> None:
    admin = make_user(factory, "lister@gmail.com", role="admin")
    venue_id = add_venue(factory, "Listed", 12.9716, 77.5946, courts=("Court 1", "Court 2"))
    listed = client.get("/api/admin/venues", headers=admin)
    assert listed.status_code == 200, listed.text
    row = next(v for v in listed.json() if v["id"] == str(venue_id))
    assert row["location_set"] is True
    assert row["latitude"] == pytest.approx(12.9716) and row["longitude"] == pytest.approx(77.5946)
    assert [c["name"] for c in row["courts"]] == ["Court 1", "Court 2"]
    assert all(c["active"] for c in row["courts"])


def test_coordinate_bounds_are_validated_on_the_server(client: TestClient, factory: sessionmaker[Session]) -> None:
    admin = make_user(factory, "bounds-admin@gmail.com", role="admin")
    venue_id = add_venue(factory, "Bounded", None, None)
    for bad in ({"latitude": 91, "longitude": 0}, {"latitude": 0, "longitude": 181}):
        response = client.put(f"/api/admin/venues/{venue_id}/coordinates", headers=admin, json=bad)
        assert response.status_code == 422, response.text


def test_every_admin_location_route_refuses_members(client: TestClient, factory: sessionmaker[Session]) -> None:
    member = make_user(factory, "not-admin@gmail.com")
    venue_id = add_venue(factory, "Guarded", 12.9716, 77.5946)
    court_id = str(next(iter(_court_ids(factory, venue_id))))
    assert client.get("/api/admin/venues", headers=member).status_code == 403
    assert client.patch(f"/api/admin/courts/{court_id}", headers=member, json={"active": False}).status_code == 403
    assert client.put(f"/api/admin/venues/{venue_id}/coordinates", headers=member,
                      json={"latitude": 1, "longitude": 1}).status_code == 403
    assert client.delete(f"/api/admin/venues/{venue_id}/coordinates", headers=member).status_code == 403


def _court_ids(factory: sessionmaker[Session], venue_id: uuid.UUID) -> list[uuid.UUID]:
    with factory() as session:
        return list(session.scalars(select(Court.id).where(Court.venue_id == venue_id)))
