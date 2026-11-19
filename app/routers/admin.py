from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.db import run_tx
from app.dependencies import get_current_user
from app.errors import ApiError
from app.models import ApprovalRequest, Court, NotificationOutbox, Reservation, User, Venue
from sqlalchemy import select, case
from app.services import approvals
from app.services.common import db_now
from app.notifications import notify_and_queue_email

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
        request.app.state.redis.delete("courts:active:v2")
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
        request.app.state.redis.delete("courts:active:v2")
    except Exception:
        pass
    return {"status": "updated"}

@router.get("/venues")
def list_venues(request: Request, user: User = Depends(get_current_user)) -> list[dict[str, str]]:
    if user.role != "admin":
        raise ApiError("forbidden", 403, "administrator role required")
    with request.app.state.factory() as session:
        return [{"id": str(v.id), "name": v.name} for v in session.query(Venue).order_by(Venue.name).all()]


class DecisionIn(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=500)


def require_admin(user: User) -> None:
    if user.role != "admin":
        raise ApiError("forbidden", 403, "administrator role required")


@router.get("/approvals")
def pending_approvals(request: Request, user: User = Depends(get_current_user)) -> list[dict[str, object]]:
    require_admin(user)
    with request.app.state.factory() as session:
        now = db_now(session)
        rows = session.execute(select(ApprovalRequest, Reservation, User.email, Court.name)
            .join(Reservation, Reservation.id == ApprovalRequest.reservation_id)
            .join(User, User.id == ApprovalRequest.user_id).join(Court, Court.id == Reservation.court_id)
            .where(ApprovalRequest.status == "pending")
            .order_by(ApprovalRequest.created_at, ApprovalRequest.id).limit(100)).all()
        return [{**approvals.output(a), "email": email, "court": court,
                 "amount_minor": r.price_minor if a.kind == "payment" else r.refund_minor,
                 "currency": r.currency, "expires_at": r.hold_expires_at.isoformat() if a.kind == "payment" else None,
                 "actionable": a.kind == "refund" or (r.status == "payment_pending" and r.hold_expires_at > now)}
                for a, r, email, court in rows]


@router.post("/approvals/{request_id}/decision")
def review_request(request_id: uuid.UUID, body: DecisionIn, request: Request,
                   user: User = Depends(get_current_user)) -> dict[str, object]:
    require_admin(user)
    return run_tx(request.app.state.factory, lambda s: approvals.output(
        approvals.decide(s, request_id, user, body.approve, body.note)))


@router.get("/users/pending")
def pending_users(request: Request, user: User = Depends(get_current_user)) -> list[dict[str, str]]:
    require_admin(user)
    with request.app.state.factory() as session:
        return [{"id": str(u.id), "email": u.email} for u in session.scalars(select(User).where(
            User.is_verified.is_(False), User.is_active.is_(True)).order_by(User.created_at).limit(100))]


@router.post("/users/{user_id}/verify")
def verify_user(user_id: uuid.UUID, request: Request, user: User = Depends(get_current_user)) -> dict[str, str]:
    require_admin(user)
    def work(session: Session) -> dict[str, str]:
        member = session.scalar(select(User).where(User.id == user_id).with_for_update())
        if not member or not member.is_active:
            raise ApiError("not_found", 404, "active account not found")
        member.is_verified = True
        notify_and_queue_email(session, member, "account_verified", "Account approved by admin",
                               "You can sign in to the demo. This does not verify ownership of your email address.",
                               "Demo account approved", "Your demo account is approved.", dedupe_key=f"verified:{member.id}")
        return {"status": "verified"}
    return run_tx(request.app.state.factory, work)


@router.get("/outbox")
def outbox(request: Request, user: User = Depends(get_current_user)) -> list[dict[str, object]]:
    require_admin(user)
    with request.app.state.factory() as session:
        rows = session.scalars(select(NotificationOutbox).order_by(
            case((NotificationOutbox.status == "dead", 0), else_=1),
            NotificationOutbox.created_at.desc()).limit(100)).all()
        return [{"id": str(r.id), "subject": r.subject, "status": r.status, "attempts": r.attempts,
                 "last_error": r.last_error} for r in rows]


@router.post("/outbox/{message_id}/retry")
def retry_message(message_id: uuid.UUID, request: Request, user: User = Depends(get_current_user)) -> dict[str, str]:
    require_admin(user)
    def work(session: Session) -> dict[str, str]:
        row = session.scalar(select(NotificationOutbox).where(NotificationOutbox.id == message_id).with_for_update())
        if not row or row.status != "dead":
            raise ApiError("not_retryable", 409, "only dead messages can be retried")
        row.status, row.attempts, row.last_error, row.claim_token = "pending", 0, None, None
        row.available_at = db_now(session)
        return {"status": "pending"}
    return run_tx(request.app.state.factory, work)
