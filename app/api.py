from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import make_engine, make_session_factory, run_tx
from app.errors import ApiError
from app.models import Reservation
from app.services import holds, queries, recurring


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


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = engine or make_engine(settings.database_url, settings.pool_size, settings.max_overflow)
    factory: sessionmaker[Session] = make_session_factory(engine)

    app = FastAPI(title="Court booking service", version="1.0.0")
    app.state.factory = factory

    def get_factory(request: Request) -> sessionmaker[Session]:
        stored: sessionmaker[Session] = request.app.state.factory
        return stored

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"code": exc.code, "detail": exc.detail, "request_id": uuid.uuid4().hex},
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(factory: sessionmaker[Session] = Depends(get_factory)) -> dict[str, str]:
        run_tx(factory, lambda s: s.execute(text("SELECT 1")).scalar_one())
        return {"status": "ready"}

    @app.post("/holds")
    def post_hold(
        body: HoldIn,
        member_id: uuid.UUID = Header(..., alias="X-Member-Id"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        factory: sessionmaker[Session] = Depends(get_factory),
    ) -> JSONResponse:
        result = run_tx(
            factory,
            lambda s: holds.create_hold(
                s, member_id, body.court_id, body.starts_at, body.ends_at, idempotency_key
            ),
        )
        return JSONResponse(status_code=201 if result.created else 200, content=_out(result.reservation))

    @app.post("/holds/{hold_id}/confirm")
    def post_confirm(
        hold_id: uuid.UUID,
        member_id: uuid.UUID = Header(..., alias="X-Member-Id"),
        factory: sessionmaker[Session] = Depends(get_factory),
    ) -> JSONResponse:
        result = run_tx(factory, lambda s: holds.confirm_hold(s, member_id, hold_id))
        # The expiry was committed by run_tx before this check, so 410 never undoes it.
        if result.expired_now:
            raise ApiError("hold_expired", 410, "the hold expired before it was confirmed")
        return JSONResponse(status_code=200, content=_out(result.reservation))

    @app.post("/bookings/{booking_id}/cancel")
    def post_cancel(
        booking_id: uuid.UUID,
        member_id: uuid.UUID = Header(..., alias="X-Member-Id"),
        factory: sessionmaker[Session] = Depends(get_factory),
    ) -> JSONResponse:
        reservation = run_tx(factory, lambda s: holds.cancel_booking(s, member_id, booking_id))
        refund = reservation.refund_minor or 0
        return JSONResponse(
            status_code=200,
            content={
                "id": str(reservation.id),
                "status": reservation.status,
                "refund_minor": refund,
                "refund": "full" if refund > 0 else "none",
                "refund_status": None,
            },
        )

    @app.get("/courts/{court_id}/availability")
    def get_availability(
        court_id: uuid.UUID,
        from_: datetime = Query(..., alias="from"),
        to: datetime = Query(...),
        factory: sessionmaker[Session] = Depends(get_factory),
    ) -> dict[str, object]:
        slots = run_tx(factory, lambda s: queries.availability(s, court_id, from_, to))
        return {
            "court_id": str(court_id),
            "slots": [
                {
                    "starts_at": slot.starts_at.isoformat(),
                    "ends_at": slot.ends_at.isoformat(),
                    "price_minor": slot.price_minor,
                    "currency": slot.currency,
                    "available": slot.available,
                }
                for slot in slots
            ],
        }

    @app.post("/recurring-bookings")
    def post_series(
        body: SeriesIn,
        member_id: uuid.UUID = Header(..., alias="X-Member-Id"),
        factory: sessionmaker[Session] = Depends(get_factory),
    ) -> JSONResponse:
        result = run_tx(
            factory,
            lambda s: recurring.create_series(
                s, member_id, body.court_id, body.starts_at, body.ends_at, body.occurrences
            ),
        )
        return JSONResponse(
            status_code=201,
            content={
                "id": str(result.series.id),
                "court_id": str(body.court_id),
                "occurrences": [_out(r) for r in result.reservations],
            },
        )

    @app.get("/staff/utilization/weekly")
    def get_utilization(
        venue_id: uuid.UUID,
        week_start: date,
        x_staff_key: str | None = Header(default=None, alias="X-Staff-Key"),
        factory: sessionmaker[Session] = Depends(get_factory),
    ) -> dict[str, object]:
        if x_staff_key != settings.staff_key:
            raise ApiError("forbidden", 403, "a valid staff key is required")
        rows = run_tx(factory, lambda s: queries.weekly_utilization(s, venue_id, week_start))
        return {
            "venue_id": str(venue_id),
            "week_start": week_start.isoformat(),
            "courts": [
                {
                    "court_id": str(row.court_id),
                    "court_name": row.court_name,
                    "booked_hours": row.booked_hours,
                    "available_hours": row.available_hours,
                    "utilization": row.utilization,
                }
                for row in rows
            ],
        }

    return app


app = create_app()
