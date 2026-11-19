from __future__ import annotations
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, func
from app.dependencies import get_current_user
from app.models import ApprovalRequest, Court, Payment, Reservation, User, Venue

router = APIRouter(prefix="/api/bookings", tags=["bookings"])

@router.get("/me")
def my_bookings(request: Request, user: User = Depends(get_current_user)) -> list[dict[str, object]]:
    with request.app.state.factory() as session:
        rows = session.execute(select(Reservation, Court, Venue, Payment.status).join(Court, Reservation.court_id == Court.id)
            .join(Venue, Court.venue_id == Venue.id).outerjoin(Payment, Payment.reservation_id == Reservation.id).where(Reservation.member_id == user.member_id)
            .order_by(Reservation.starts_at.desc()).limit(100)).all()
        reviews = session.scalars(select(ApprovalRequest).where(
            ApprovalRequest.user_id == user.id,
            ApprovalRequest.reservation_id.in_([row[0].id for row in rows]))).all()
        review_map = {(r.reservation_id, r.kind): r.status for r in reviews}
        now = session.scalar(select(func.clock_timestamp()))
        return [{"id": str(r.id), "court_id": str(c.id), "court": c.name, "venue": v.name, "timezone": v.timezone,
            "status": ("expired" if r.status in {"held", "payment_pending"} and r.hold_expires_at <= now else r.status), "starts_at": r.starts_at.isoformat(), "ends_at": r.ends_at.isoformat(),
            "payment_review": review_map.get((r.id, "payment")),
            "refund_review": review_map.get((r.id, "refund")), "payment_status": payment_status, "price_minor": r.price_minor, "currency": r.currency, "refund_minor": r.refund_minor}
            for r, c, v, payment_status in rows]
