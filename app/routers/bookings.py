from __future__ import annotations
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from app.dependencies import get_current_user
from app.models import Court, Reservation, User, Venue

router = APIRouter(prefix="/api/bookings", tags=["bookings"])

@router.get("/me")
def my_bookings(request: Request, user: User = Depends(get_current_user)) -> list[dict[str, object]]:
    with request.app.state.factory() as session:
        rows = session.execute(select(Reservation, Court, Venue).join(Court, Reservation.court_id == Court.id)
            .join(Venue, Court.venue_id == Venue.id).where(Reservation.member_id == user.member_id)
            .order_by(Reservation.starts_at.desc()).limit(100)).all()
        return [{"id": str(r.id), "court_id": str(c.id), "court": c.name, "venue": v.name,
            "status": r.status, "starts_at": r.starts_at.isoformat(), "ends_at": r.ends_at.isoformat(),
            "price_minor": r.price_minor, "currency": r.currency, "refund_minor": r.refund_minor}
            for r, c, v in rows]
