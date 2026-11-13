from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.db import run_tx
from app.dependencies import get_current_user
from app.errors import ApiError
from app.models import Court, User, Venue

router = APIRouter(prefix="/api/admin", tags=["administration"])

class CourtCreate(BaseModel):
    venue_id: uuid.UUID
    name: str = Field(min_length=2, max_length=60)

class VenueCoordinates(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

@router.post("/courts", status_code=201)
def create_court(body: CourtCreate, request: Request, user: User = Depends(get_current_user)) -> dict[str, str]:
    if user.role != "admin":
        raise ApiError("forbidden", 403, "administrator role required")
    def work(session: Session) -> Court:
        venue = session.get(Venue, body.venue_id)
        if venue is None:
            raise ApiError("venue_not_found", 404, "venue not found")
        court = Court(venue_id=venue.id, name=body.name.strip(), active=True)
        session.add(court)
        session.flush()
        return court
    court = run_tx(request.app.state.factory, work)
    try:
        request.app.state.redis.delete("courts:active:v1")
    except Exception:
        pass
    return {"id": str(court.id), "name": court.name, "venue_id": str(court.venue_id)}

@router.put("/venues/{venue_id}/coordinates")
def update_venue_coordinates(venue_id: uuid.UUID, body: VenueCoordinates, request: Request,
                             user: User = Depends(get_current_user)) -> dict[str, str]:
    if user.role != "admin":
        raise ApiError("forbidden", 403, "administrator role required")
    def work(session: Session) -> None:
        venue = session.get(Venue, venue_id)
        if venue is None:
            raise ApiError("venue_not_found", 404, "venue not found")
        venue.latitude, venue.longitude = body.latitude, body.longitude
    run_tx(request.app.state.factory, work)
    try:
        request.app.state.redis.delete("courts:active:v1")
    except Exception:
        pass
    return {"status": "updated"}

@router.get("/venues")
def list_venues(request: Request, user: User = Depends(get_current_user)) -> list[dict[str, str]]:
    if user.role != "admin":
        raise ApiError("forbidden", 403, "administrator role required")
    with request.app.state.factory() as session:
        return [{"id": str(v.id), "name": v.name} for v in session.query(Venue).order_by(Venue.name).all()]
