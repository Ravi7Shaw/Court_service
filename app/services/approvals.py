"""Reservation row locks serialize member submissions, expiry and admin decisions."""
from datetime import timedelta
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models import ApprovalRequest, Payment, Reservation, User
from app.notifications import notify_admins, notify_and_queue_email
from app.services.common import db_now


def submit(session: Session, member_id: uuid.UUID, reservation_id: uuid.UUID,
           kind: str, message: str = "") -> ApprovalRequest:
    reservation = session.scalar(select(Reservation).where(Reservation.id == reservation_id).with_for_update())
    if reservation is None or reservation.member_id != member_id:
        raise ApiError("not_found", 404, "booking not found")
    user = session.scalar(select(User).where(User.member_id == member_id))
    if user is None:
        raise ApiError("unauthorized", 401, "verified account required")
    existing = session.scalar(select(ApprovalRequest).where(
        ApprovalRequest.reservation_id == reservation_id, ApprovalRequest.kind == kind))
    if existing:
        return existing
    now = db_now(session)
    if kind == "payment":
        if reservation.status != "held" or not reservation.hold_expires_at or reservation.hold_expires_at <= now:
            raise ApiError("hold_expired", 410, "only an unexpired hold can be submitted")
        reservation.status = "payment_pending"
        deadline = min(now + timedelta(minutes=15), reservation.starts_at)
        reservation.hold_expires_at = deadline
        reservation.payment_hard_deadline = deadline
    else:
        payment = session.scalar(select(Payment).where(Payment.reservation_id == reservation_id))
        if reservation.status != "cancelled" or not reservation.refund_minor or not payment or payment.status != "paid":
            raise ApiError("not_refundable", 409, "cancel an approved, refundable booking first")
    request = ApprovalRequest(reservation_id=reservation_id, user_id=user.id, kind=kind,
                              status="pending", member_message=message)
    session.add(request)
    session.flush()
    title = "Payment report received" if kind == "payment" else "Refund request received"
    text = f"Request {request.id} awaits admin review. No real money moves in this demo."
    notify_and_queue_email(session, user, "approval_pending", title, text, title, text,
                           dedupe_key=f"submitted:{request.id}")
    notify_admins(session, "review_required", "Review requested", f"{user.email}: {kind} for booking {reservation.id}. {message}",
                  f"review:{request.id}")
    return request


def decide(session: Session, request_id: uuid.UUID, admin: User, approve: bool, note: str) -> ApprovalRequest:
    reference = session.get(ApprovalRequest, request_id)
    if reference is None:
        raise ApiError("not_found", 404, "request not found")
    reservation = session.scalar(select(Reservation).where(
        Reservation.id == reference.reservation_id).with_for_update())
    request = session.scalar(select(ApprovalRequest).where(
        ApprovalRequest.id == request_id).with_for_update().execution_options(populate_existing=True))
    if reservation is None or request is None:
        raise ApiError("not_found", 404, "booking or request not found")
    if request.status != "pending":
        if request.status in {"approved", "rejected"} and (request.status == "approved") != approve:
            raise ApiError("already_reviewed", 409, "this request already has a final decision")
        return request
    now = db_now(session)
    user = session.get(User, request.user_id)
    if user is None:
        raise ApiError("not_found", 404, "account not found")
    if request.kind == "payment" and (reservation.status != "payment_pending" or
            not reservation.hold_expires_at or reservation.hold_expires_at <= now or
            not reservation.payment_hard_deadline or reservation.payment_hard_deadline <= now or
            reservation.starts_at <= now):
        request.status = "expired"
        if reservation.status == "payment_pending":
            reservation.status = "expired"
    elif not approve:
        request.status = "rejected"
        if request.kind == "payment":
            reservation.status = "expired"
    elif request.kind == "payment":
        session.add(Payment(reservation_id=reservation.id, user_id=user.id, provider="manual_demo",
                            provider_reference=f"DEMO-{request.id}", amount_minor=reservation.price_minor,
                            currency=reservation.currency, status="paid", refund_minor=0))
        reservation.status = "confirmed"
        reservation.confirmed_at = now
        request.status = "approved"
    else:
        payment = session.scalar(select(Payment).where(Payment.reservation_id == reservation.id).with_for_update())
        if reservation.status != "cancelled" or not payment or payment.status != "paid" or not reservation.refund_minor:
            raise ApiError("not_refundable", 409, "booking is no longer refundable")
        payment.status = "refunded"
        payment.refund_minor = reservation.refund_minor
        request.status = "approved"
    request.reviewed_by, request.reviewed_at, request.review_note = admin.id, now, note
    title = f"{request.kind.title()} {request.status}"
    message = f"Admin review for booking {reservation.id}: {request.status}. {note} Demo only; no money moved."
    notify_and_queue_email(session, user, "approval_decision", title, message, title, message,
                           dedupe_key=f"decision:{request.id}")
    session.flush()
    return request


def output(request: ApprovalRequest) -> dict[str, object]:
    return {"id": str(request.id), "reservation_id": str(request.reservation_id), "kind": request.kind,
            "status": request.status, "member_message": request.member_message, "review_note": request.review_note,
            "reviewed_by": str(request.reviewed_by) if request.reviewed_by else None,
            "created_at": request.created_at.isoformat()}
