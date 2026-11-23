from __future__ import annotations

from contextlib import asynccontextmanager

import uuid
from datetime import date, datetime

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from app.redis_support import Redis, RedisError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import make_engine, make_session_factory, run_tx
from app.dependencies import get_member_id
from app.errors import ApiError
from app.models import Notification, Reservation, User
from app.payments.factory import build_payment_provider
from app.payments.base import PaymentProvider
from app.notifications import notify_and_queue_email
from app.routers import admin, auth, bookings, location, notifications, payments
from app.security import decode_access_token
from app.services import approvals, holds, queries, recurring
from app import events

HTTP_REQUESTS = Counter("court_http_requests_total", "HTTP requests", ["method", "path", "status"])
HTTP_LATENCY = Histogram("court_http_request_duration_seconds", "HTTP request duration", ["method", "path"])


class HoldIn(BaseModel):
    court_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime


class SeriesIn(BaseModel):
    court_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    occurrences: int = Field(ge=1, le=3)


class ReservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    court_id: uuid.UUID
    series_id: uuid.UUID | None = None
    status: str
    starts_at: datetime
    ends_at: datetime
    price_minor: int
    currency: str
    hold_expires_at: datetime | None = None
    confirmed_at: datetime | None = None
    cancelled_at: datetime | None = None
    refund_minor: int | None = None


def _out(reservation: Reservation) -> dict[str, object]:
    return ReservationOut.model_validate(reservation).model_dump(mode="json")


def create_app(settings: Settings | None = None, engine: Engine | None = None,
               payment_provider: PaymentProvider | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    owns_engine = engine is None
    engine = engine or make_engine(settings.database_url, settings.pool_size, settings.max_overflow)
    factory: sessionmaker[Session] = make_session_factory(engine)
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            events.get_publisher().close()
            app.state.redis.close()
            if owns_engine:
                engine.dispose()

    app = FastAPI(
        title="Court booking service",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.state.factory = factory
    app.state.settings = settings
    app.state.redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
        health_check_interval=30,
    )
    app.state.payment_provider = (
        payment_provider or build_payment_provider(settings.payment_provider)
    )
    if settings.kafka_bootstrap_servers:
        events.set_publisher(events.KafkaPublisher(settings.kafka_bootstrap_servers, settings.kafka_topic))
    else:
        events.set_publisher(events.NullPublisher())
    app.add_middleware(CORSMiddleware, allow_origins=[settings.app_base_url.rstrip("/")],
                       allow_credentials=True, allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])

    @app.middleware("http")
    async def observe_requests(request: Request, call_next):
        import time
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
        HTTP_LATENCY.labels(request.method, path).observe(time.perf_counter() - started)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status,
                            content={"code": exc.code, "detail": exc.detail,
                                     "request_id": request.headers.get("x-request-id", uuid.uuid4().hex)})

    app.include_router(auth.router)
    app.include_router(bookings.router)
    app.include_router(notifications.router)
    app.include_router(payments.router)
    app.include_router(location.router)
    app.include_router(admin.router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        run_tx(factory, lambda s: s.execute(text("SELECT 1")).scalar_one())
        try:
            app.state.redis.ping()
        except RedisError:
            if settings.environment == "production":
                raise ApiError("redis_unavailable", 503, "Redis is unavailable")
        return {"status": "ready"}

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/holds")
    def post_hold(body: HoldIn, member_id: uuid.UUID = Depends(get_member_id),
                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=1, max_length=120)) -> JSONResponse:
        result = run_tx(factory, lambda s: holds.create_hold(
            s, member_id, body.court_id, body.starts_at, body.ends_at, idempotency_key))
        return JSONResponse(status_code=201 if result.created else 200, content=_out(result.reservation))

    @app.post("/holds/{hold_id}/confirm")
    def post_confirm(hold_id: uuid.UUID, member_id: uuid.UUID = Depends(get_member_id)) -> JSONResponse:
        if settings.admin_approval_required:
            review = run_tx(factory, lambda s: approvals.submit(s, member_id, hold_id, "payment"))
            return JSONResponse(status_code=202, content=approvals.output(review))
        if not settings.allow_direct_confirm:
            raise ApiError("payment_required", 409, "use the simulated payment endpoint to confirm this booking")
        result = run_tx(factory, lambda s: holds.confirm_hold(s, member_id, hold_id))
        if result.expired_now:
            raise ApiError("hold_expired", 410, "the hold expired before it was confirmed")
        return JSONResponse(status_code=200, content=_out(result.reservation))

    @app.post("/bookings/{booking_id}/cancel")
    def post_cancel(booking_id: uuid.UUID,
                    member_id: uuid.UUID = Depends(get_member_id)) -> JSONResponse:
        def cancel_and_notify(session: Session) -> Reservation:
            reservation = holds.cancel_booking(session, member_id, booking_id)
            refund_amount = reservation.refund_minor or 0
            user = session.scalar(select(User).where(User.member_id == member_id))
            if user:
                notify_and_queue_email(session, user, "cancellation", "Booking cancelled",
                    f"Your booking was cancelled. Refund eligibility: {refund_amount} {reservation.currency}.",
                    "Court booking cancellation", f"Booking {reservation.id} was cancelled. Refund amount: {refund_amount} {reservation.currency}.",
                    dedupe_key=f"booking-cancelled:{reservation.id}")
                if refund_amount > 0:
                    existing_refund_notice = session.scalar(select(Notification.id).where(
                        Notification.user_id == user.id,
                        Notification.kind == "refund_action",
                        Notification.message.contains(str(reservation.id)),
                    ))
                    if existing_refund_notice is None:
                        session.add(Notification(user_id=user.id, kind="refund_action", title="Refund available",
                            message=f"Booking {reservation.id} is eligible for a refund. Request a refund for admin review."))
            return reservation
        reservation = run_tx(factory, cancel_and_notify)
        refund = reservation.refund_minor or 0
        return JSONResponse(status_code=200, content={"id": str(reservation.id), "status": reservation.status,
            "refund_minor": refund, "refund": "full" if refund > 0 else "none",
            "refund_status": ("awaiting_admin_request" if settings.admin_approval_required else "awaiting_simulation") if refund > 0 else "not_eligible"})

    @app.get("/courts/{court_id}/availability")
    def get_availability(court_id: uuid.UUID, from_: datetime = Query(..., alias="from"),
                         to: datetime = Query(...)) -> dict[str, object]:
        slots = run_tx(factory, lambda s: queries.availability(s, court_id, from_, to))
        return {"court_id": str(court_id), "slots": [{"starts_at": slot.starts_at.isoformat(),
            "ends_at": slot.ends_at.isoformat(), "price_minor": slot.price_minor,
            "currency": slot.currency, "available": slot.available} for slot in slots]}

    @app.post("/recurring-bookings")
    def post_series(body: SeriesIn, member_id: uuid.UUID = Depends(get_member_id)) -> JSONResponse:
        if settings.admin_approval_required or not settings.allow_direct_confirm:
            raise ApiError("recurring_payment_unsupported", 409, "recurring bookings are disabled until recurring payment checkout is implemented")
        result = run_tx(factory, lambda s: recurring.create_series(
            s, member_id, body.court_id, body.starts_at, body.ends_at, body.occurrences))
        return JSONResponse(status_code=201, content={"id": str(result.series.id),
            "court_id": str(body.court_id), "occurrences": [_out(r) for r in result.reservations]})

    @app.get("/staff/utilization/weekly")
    def get_utilization(venue_id: uuid.UUID, week_start: date,
                        authorization: str | None = Header(default=None),
                        x_staff_key: str | None = Header(default=None, alias="X-Staff-Key")) -> dict[str, object]:
        allowed = False
        if authorization and authorization.lower().startswith("bearer "):
            try:
                claims = decode_access_token(authorization.split(" ", 1)[1], settings)
                with factory() as session:
                    staff = session.get(User, uuid.UUID(claims["sub"]))
                    allowed = bool(staff and staff.is_active and staff.is_verified and staff.role in {"staff", "admin"})
            except Exception:
                allowed = False
        elif settings.allow_legacy_headers:
            allowed = x_staff_key == settings.staff_key
        if not allowed:
            raise ApiError("forbidden", 403, "staff or administrator role required")
        rows = run_tx(factory, lambda s: queries.weekly_utilization(s, venue_id, week_start))
        return {"venue_id": str(venue_id), "week_start": week_start.isoformat(), "courts": [
            {"court_id": str(row.court_id), "court_name": row.court_name,
             "booked_hours": row.booked_hours, "available_hours": row.available_hours,
             "utilization": row.utilization} for row in rows]}

    return app


app = create_app()
