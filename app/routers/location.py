from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import run_tx
from app.dependencies import get_current_user
from app.errors import ApiError
from app.models import Court, User, UserLocation, Venue
from app.services.location import nearby_courts

router = APIRouter(prefix="/api", tags=["location and courts"])

COURT_LIST_CACHE_KEY = "courts:active:v2"
COURT_LIST_TTL_SECONDS = 30


class LocationIn(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    consent: bool


@router.get("/me/location")
def location_consent(request: Request, user: User = Depends(get_current_user)) -> dict[str, bool]:
    with request.app.state.factory() as session:
        return {"shared": session.get(UserLocation, user.id) is not None}


@router.post("/me/location")
def save_location(body: LocationIn, request: Request, user: User = Depends(get_current_user)) -> dict[str, str]:
    if not body.consent:
        raise ApiError("consent_required", 400, "explicit location consent is required")

    def work(session: Session) -> None:
        row = session.get(UserLocation, user.id)
        if row is None:
            session.add(UserLocation(user_id=user.id, latitude=body.latitude, longitude=body.longitude,
                                     consent_version="1"))
        else:
            row.latitude, row.longitude = body.latitude, body.longitude

    run_tx(request.app.state.factory, work)
    return {"status": "saved", "message": "Location saved with your consent."}


@router.delete("/me/location")
def delete_location(request: Request, user: User = Depends(get_current_user)) -> dict[str, str]:
    def work(session: Session) -> None:
        row = session.get(UserLocation, user.id)
        if row:
            session.delete(row)

    run_tx(request.app.state.factory, work)
    return {"status": "deleted", "message": "Saved location and consent record removed."}


@router.get("/courts")
def list_courts(request: Request) -> list[dict[str, object]]:
    """Shared, non-personal court list. Cached briefly; admin changes invalidate the key."""
    cache = request.app.state.redis
    try:
        cached = cache.get(COURT_LIST_CACHE_KEY)
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    with request.app.state.factory() as session:
        rows = session.execute(
            select(Court, Venue).join(Venue, Court.venue_id == Venue.id).where(Court.active.is_(True))
            .order_by(Venue.name, Court.name)
        ).all()
        result = [{"id": str(c.id), "name": c.name, "venue_id": str(v.id), "venue": v.name,
                   "latitude": v.latitude, "longitude": v.longitude, "currency": v.currency,
                   "payments_enabled": v.payments_enabled, "timezone": v.timezone} for c, v in rows]
    try:
        cache.setex(COURT_LIST_CACHE_KEY, COURT_LIST_TTL_SECONDS, json.dumps(result))
    except Exception:
        pass
    return result


@router.get("/courts/nearby")
def nearby(request: Request, radius_km: float = 25,
           user: User = Depends(get_current_user)) -> list[dict[str, object]]:
    if not 1 <= radius_km <= 100:
        raise ApiError("invalid_radius", 422, "radius_km must be between 1 and 100")
    with request.app.state.factory() as session:
        saved = session.get(UserLocation, user.id)
        if saved is None:
            raise ApiError("location_not_shared", 404, "share your location first; it is always optional")
        matches = nearby_courts(session, lat=saved.latitude, lon=saved.longitude, radius_km=radius_km)
    return [{"id": str(m.court_id), "name": m.court_name, "venue": m.venue_name,
             "distance_km": m.distance_km, "currency": m.currency} for m in matches]
