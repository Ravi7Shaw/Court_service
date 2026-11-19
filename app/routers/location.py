from __future__ import annotations
import math
import json
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import run_tx
from app.dependencies import get_current_user
from app.errors import ApiError
from app.models import Court, User, UserLocation, Venue

router = APIRouter(prefix="/api", tags=["location and courts"])

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
    cache = request.app.state.redis
    try:
        cached = cache.get("courts:active:v2")
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    with request.app.state.factory() as session:
        rows = session.execute(select(Court, Venue).join(Venue, Court.venue_id == Venue.id).where(Court.active.is_(True))).all()
        result = [{"id": str(c.id), "name": c.name, "venue_id": str(v.id), "venue": v.name,
                   "latitude": v.latitude, "longitude": v.longitude, "currency": v.currency,
                   "payments_enabled": v.payments_enabled, "timezone": v.timezone} for c, v in rows]
    try:
        cache.setex("courts:active:v2", 30, json.dumps(result))
    except Exception:
        pass
    return result

@router.get("/courts/nearby")
def nearby_courts(request: Request, radius_km: float = 25,
                  user: User = Depends(get_current_user)) -> list[dict[str, object]]:
    if not 1 <= radius_km <= 100:
        raise ApiError("invalid_radius", 422, "radius_km must be between 1 and 100")
    with request.app.state.factory() as session:
        loc = session.get(UserLocation, user.id)
        if loc is None:
            raise ApiError("location_not_shared", 404, "share your location first; it is always optional")
        rows = session.execute(select(Court, Venue).join(Venue, Court.venue_id == Venue.id).where(
            Court.active.is_(True), Venue.latitude.is_not(None), Venue.longitude.is_not(None))).all()
        output = []
        for court, venue in rows:
            lat1, lon1, lat2, lon2 = map(math.radians, [loc.latitude, loc.longitude, venue.latitude, venue.longitude])
            dlat, dlon = lat2-lat1, lon2-lon1
            a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
            distance = 6371 * 2 * math.asin(math.sqrt(a))
            if distance <= radius_km:
                output.append({"id": str(court.id), "name": court.name, "venue": venue.name,
                               "distance_km": round(distance, 2), "currency": venue.currency})
        return sorted(output, key=lambda item: float(item["distance_km"]))
