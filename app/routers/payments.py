from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from app.services import approvals
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import run_tx
from app.services.common import db_now
from app.dependencies import get_current_user
from app.errors import ApiError
from app.models import Payment, Reservation, User
from app.notifications import notify_and_queue_email

router = APIRouter(prefix="/api/payments", tags=["payments"])

class ReportIn(BaseModel):
    message: str = Field(default="Payment reported by member", max_length=500)


@router.post("/{reservation_id}/report", status_code=202)
def report_payment(reservation_id: uuid.UUID, body: ReportIn, request: Request,
                   user: User = Depends(get_current_user)) -> dict[str, object]:
    return run_tx(request.app.state.factory, lambda s: approvals.output(
        approvals.submit(s, user.member_id, reservation_id, "payment", body.message)))


@router.post("/{reservation_id}/simulate", response_model=None)
def simulate_payment(reservation_id: uuid.UUID, request: Request,
                     user: User = Depends(get_current_user)) -> dict[str, object] | JSONResponse:
    if request.app.state.settings.admin_approval_required:
        result = run_tx(request.app.state.factory, lambda s: approvals.output(
            approvals.submit(s, user.member_id, reservation_id, "payment")))
        return JSONResponse(status_code=202, content=result)
    provider = request.app.state.payment_provider
    def work(session: Session) -> dict[str, object]:
        reservation = session.scalar(select(Reservation).where(
            Reservation.id == reservation_id, Reservation.member_id == user.member_id
        ).with_for_update())
        if reservation is None:
            raise ApiError("not_found", 404, "reservation not found")
        existing = session.scalar(select(Payment).where(Payment.reservation_id == reservation.id))
        if existing:
            return {"payment_id": str(existing.id), "status": existing.status,
                    "amount_minor": existing.amount_minor, "currency": existing.currency}
        if reservation.status != "held":
            raise ApiError("not_payable", 409, "only an active hold can be paid")
        now = db_now(session)
        if reservation.hold_expires_at is None or reservation.hold_expires_at <= now:
            raise ApiError("hold_expired", 410, "booking hold expired")
        result = provider.capture(reservation.price_minor, reservation.currency, str(reservation.id))
        payment = Payment(reservation_id=reservation.id, user_id=user.id, provider=provider.name,
                          provider_reference=result.provider_reference, amount_minor=reservation.price_minor,
                          currency=reservation.currency, status="paid", refund_minor=0)
        session.add(payment)
        reservation.status = "confirmed"
        reservation.confirmed_at = now
        notify_and_queue_email(session, user, "payment", "Payment confirmed",
            f"Payment of {reservation.price_minor} {reservation.currency} was simulated successfully.",
            "Court booking payment confirmation",
            f"Your booking {reservation.id} is confirmed. Amount: {reservation.price_minor} {reservation.currency}.")
        return {"payment_id": str(payment.id), "status": "paid", "amount_minor": payment.amount_minor,
                "currency": payment.currency, "provider": provider.name}
    return run_tx(request.app.state.factory, work)

@router.get("/{reservation_id}")
def payment_status(reservation_id: uuid.UUID, request: Request,
                   user: User = Depends(get_current_user)) -> dict[str, object]:
    with request.app.state.factory() as session:
        payment = session.scalar(select(Payment).join(Reservation, Payment.reservation_id == Reservation.id).where(
            Payment.reservation_id == reservation_id, Reservation.member_id == user.member_id))
        if payment is None:
            raise ApiError("not_found", 404, "payment not found")
        return {"id": str(payment.id), "status": payment.status, "amount_minor": payment.amount_minor,
                "refund_minor": payment.refund_minor, "currency": payment.currency,
                "provider": payment.provider}

@router.post("/{reservation_id}/refund/request", response_model=None)
@router.post("/{reservation_id}/refund/simulate", response_model=None)
def simulate_refund(reservation_id: uuid.UUID, request: Request,
                    user: User = Depends(get_current_user)) -> dict[str, object] | JSONResponse:
    if request.app.state.settings.admin_approval_required:
        result = run_tx(request.app.state.factory, lambda s: approvals.output(
            approvals.submit(s, user.member_id, reservation_id, "refund")))
        return JSONResponse(status_code=202, content=result)
    provider = request.app.state.payment_provider
    def work(session: Session) -> dict[str, object]:
        reservation = session.scalar(select(Reservation).where(
            Reservation.id == reservation_id, Reservation.member_id == user.member_id).with_for_update())
        if reservation is None:
            raise ApiError("not_found", 404, "reservation not found")
        payment = session.scalar(select(Payment).where(Payment.reservation_id == reservation.id).with_for_update())
        if payment is None:
            raise ApiError("payment_not_found", 404, "no captured payment exists for this booking")
        if reservation.status != "cancelled":
            raise ApiError("booking_not_cancelled", 409, "cancel the booking before requesting a refund")
        amount = reservation.refund_minor or 0
        if amount <= 0:
            raise ApiError("not_refundable", 409, "this cancellation is not eligible for a refund")
        if payment.status == "refunded":
            return {"status": "refunded", "refund_minor": payment.refund_minor, "currency": payment.currency}
        provider.refund(payment.provider_reference, amount, f"refund-{reservation.id}")
        payment.refund_minor = amount
        payment.status = "refunded" if amount == payment.amount_minor else "partially_refunded"
        notify_and_queue_email(session, user, "refund", "Refund processed",
            f"A simulated refund of {amount} {payment.currency} has been processed.",
            "Court booking refund confirmation",
            f"Refund processed for booking {reservation.id}: {amount} {payment.currency}.")
        return {"status": payment.status, "refund_minor": amount, "currency": payment.currency}
    return run_tx(request.app.state.factory, work)
