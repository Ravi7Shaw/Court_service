from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api import create_app
from app.config import Settings
from app.db import make_engine, make_session_factory
from app.models import Court, PeakWindow, Venue

ROOT = Path(__file__).resolve().parent.parent
TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://postgres@localhost:5432/booking_test")
STAFF_KEY = "test-staff-key"
IST = ZoneInfo("Asia/Kolkata")
TABLES = "idempotency_keys, reservations, recurring_series, peak_windows, courts, members, venues"


def _guard_database_name(url: str) -> None:
    # Tests delete rows. They must never point at the application database.
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        raise RuntimeError("tests refuse to run against a database whose name does not end in _test")


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    _guard_database_name(TEST_URL)
    os.environ["DATABASE_URL"] = TEST_URL
    eng = make_engine(TEST_URL, pool_size=40, max_overflow=20)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


@pytest.fixture()
def factory(engine: Engine) -> sessionmaker[Session]:
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    return make_session_factory(engine)


@dataclass(frozen=True)
class Venue_:
    venue_id: uuid.UUID
    court_a: uuid.UUID
    court_b: uuid.UUID


def make_venue(
    factory: sessionmaker[Session],
    *,
    name: str = "Test venue",
    opening: int = 6,
    closing: int = 22,
    hold_ttl: int = 300,
) -> Venue_:
    with factory() as session, session.begin():
        venue = Venue(
            id=uuid.uuid4(),
            name=name,
            timezone="Asia/Kolkata",
            currency="INR",
            peak_rate_minor=80_000,
            offpeak_rate_minor=50_000,
            opening_hour=opening,
            closing_hour=closing,
            hold_ttl_seconds=hold_ttl,
            payments_enabled=False,
        )
        session.add(venue)
        session.flush()
        for day in range(1, 8):
            session.add(PeakWindow(venue_id=venue.id, day_of_week=day, start_hour=17, end_hour=21))
        court_a = Court(id=uuid.uuid4(), venue_id=venue.id, name="Court A", active=True)
        court_b = Court(id=uuid.uuid4(), venue_id=venue.id, name="Court B", active=True)
        session.add_all([court_a, court_b])
        session.flush()
        return Venue_(venue.id, court_a.id, court_b.id)


@pytest.fixture()
def venue(factory: sessionmaker[Session]) -> Venue_:
    return make_venue(factory)


@pytest.fixture()
def client(engine: Engine, factory: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app(Settings(TEST_URL, STAFF_KEY, 40, 20), engine=engine)
    with TestClient(app) as test_client:
        yield test_client


def member_headers(member: uuid.UUID) -> dict[str, str]:
    return {"X-Member-Id": str(member)}


def slot_on(days_ahead: int, hour: int) -> tuple[datetime, datetime]:
    """A one-hour slot at a local hour, the given number of days in the future (IST)."""
    day = (datetime.now(IST) + timedelta(days=days_ahead)).date()
    start = datetime.combine(day, time(hour), tzinfo=IST)
    return start, start + timedelta(hours=1)
