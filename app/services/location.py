"""Nearby-court search.

Design
------
The old implementation loaded every active court and venue on each request and
measured distance in Python. Cost grew with the whole catalogue, on every call,
on the same PostgreSQL node that serves bookings.

This module instead:

1. Narrows candidates with a latitude/longitude bounding box. The box is two
   range predicates that a B-tree index on ``venues(latitude, longitude)`` can
   serve. Boxes handle the poles and the antimeridian (180 degrees).
2. Computes the exact great-circle (haversine) distance in SQL, only for the
   rows the box returned, and applies the radius in the same statement.
3. Orders by distance and caps the result set, so response size is bounded.

Results are never cached per user: they depend on a precise location, and
caching them would retain that location outside the user's own row. Venue
coordinates are small and change rarely, so the existing 30-second court-list
cache already covers the shared metadata.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Court, Venue

EARTH_RADIUS_KM = 6371.0
DEFAULT_LIMIT = 50


@dataclass(frozen=True)
class LatLngBox:
    """An axis-aligned box in degrees. Boxes never wrap; the antimeridian is split into two."""

    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float


@dataclass(frozen=True)
class NearbyCourt:
    court_id: uuid.UUID
    court_name: str
    venue_name: str
    currency: str
    distance_km: float


def bounding_boxes(lat: float, lon: float, radius_km: float) -> list[LatLngBox]:
    """Return one or two boxes that together contain every point within ``radius_km``.

    Two boxes are returned when the search circle crosses the antimeridian. A
    single full-longitude box is returned when the circle contains a pole.
    The boxes are a superset of the circle; the exact distance check runs later.
    """
    if radius_km <= 0:
        raise ValueError("radius_km must be positive")
    angular = radius_km / EARTH_RADIUS_KM  # radians
    lat_r = math.radians(lat)
    lat_min_r, lat_max_r = lat_r - angular, lat_r + angular

    if lat_min_r <= -math.pi / 2 or lat_max_r >= math.pi / 2:
        # The circle reaches a pole: every longitude can match.
        return [LatLngBox(
            lat_min=max(-90.0, math.degrees(lat_min_r)),
            lat_max=min(90.0, math.degrees(lat_max_r)),
            lon_min=-180.0,
            lon_max=180.0,
        )]

    # Largest longitude half-width that still contains the circle.
    delta_lon = math.asin(min(1.0, math.sin(angular) / math.cos(lat_r)))
    lon_min_r, lon_max_r = math.radians(lon) - delta_lon, math.radians(lon) + delta_lon
    box_lat = (math.degrees(lat_min_r), math.degrees(lat_max_r))

    if lon_min_r < -math.pi:  # wraps past -180: split into two boxes
        return [
            LatLngBox(box_lat[0], box_lat[1], math.degrees(lon_min_r + 2 * math.pi), 180.0),
            LatLngBox(box_lat[0], box_lat[1], -180.0, math.degrees(lon_max_r)),
        ]
    if lon_max_r > math.pi:  # wraps past +180: split into two boxes
        return [
            LatLngBox(box_lat[0], box_lat[1], math.degrees(lon_min_r), 180.0),
            LatLngBox(box_lat[0], box_lat[1], -180.0, math.degrees(lon_max_r - 2 * math.pi)),
        ]
    return [LatLngBox(box_lat[0], box_lat[1], math.degrees(lon_min_r), math.degrees(lon_max_r))]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Reference implementation. The SQL expression below must agree with this (tested)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


def _distance_expression(lat: float, lon: float):  # noqa: ANN202 - SQLAlchemy expression
    """Haversine distance in kilometres, evaluated by PostgreSQL on venue coordinates."""
    lat_r = math.radians(lat)
    venue_lat_r = func.radians(Venue.latitude)
    dphi = venue_lat_r - lat_r
    dlam = func.radians(Venue.longitude) - math.radians(lon)
    a = (
        func.power(func.sin(dphi / 2), 2)
        + math.cos(lat_r) * func.cos(venue_lat_r) * func.power(func.sin(dlam / 2), 2)
    )
    # LEAST guards against floating-point values just above 1, which would make asin() fail.
    return 2 * EARTH_RADIUS_KM * func.asin(func.sqrt(func.least(a, 1.0)))


def nearby_query(lat: float, lon: float, radius_km: float, limit: int = DEFAULT_LIMIT):  # noqa: ANN201
    """The exact SELECT that nearby search runs. Exposed so tests can EXPLAIN it."""
    boxes = bounding_boxes(lat, lon, radius_km)
    in_box = or_(*(
        (Venue.latitude.between(b.lat_min, b.lat_max)) & (Venue.longitude.between(b.lon_min, b.lon_max))
        for b in boxes
    ))
    distance = _distance_expression(lat, lon)
    return (
        select(Court.id, Court.name, Venue.name, Venue.currency, distance.label("distance_km"))
        .join(Venue, Court.venue_id == Venue.id)
        .where(
            Court.active.is_(True),
            Venue.latitude.is_not(None),
            Venue.longitude.is_not(None),
            in_box,
            distance <= radius_km,
        )
        .order_by(distance, Court.name, Court.id)
        .limit(limit)
    )


def nearby_courts(
    session: Session,
    *,
    lat: float,
    lon: float,
    radius_km: float,
    limit: int = DEFAULT_LIMIT,
) -> list[NearbyCourt]:
    """Active courts within ``radius_km`` of a point, nearest first, at most ``limit`` rows."""
    rows = session.execute(nearby_query(lat, lon, radius_km, limit)).all()
    return [
        NearbyCourt(court_id=cid, court_name=cname, venue_name=vname, currency=cur,
                    distance_km=round(float(dist), 2))
        for cid, cname, vname, cur, dist in rows
    ]
