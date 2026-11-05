from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.errors import ApiError

T = TypeVar("T")
DEADLOCK = "40P01"
LOCK_NOT_AVAILABLE = "55P03"
MAX_TX_ATTEMPTS = 3


def make_engine(url: str, pool_size: int, max_overflow: int) -> Engine:
    engine = create_engine(url, pool_size=pool_size, max_overflow=max_overflow, pool_pre_ping=True)

    @event.listens_for(engine, "connect")
    def _limit_waits(dbapi_connection: Any, _record: object) -> None:
        # A request that waits on a hot slot fails fast instead of pinning a worker.
        cursor = dbapi_connection.cursor()
        cursor.execute("SET lock_timeout = '2s'")
        cursor.execute("SET statement_timeout = '5s'")
        cursor.execute("SET idle_in_transaction_session_timeout = '10s'")
        cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def sqlstate_of(exc: BaseException) -> str | None:
    orig = getattr(exc, "orig", None)
    value: object = getattr(orig, "sqlstate", None)
    return value if isinstance(value, str) else None


def run_tx(factory: sessionmaker[Session], work: Callable[[Session], T]) -> T:
    """Run one use case in one transaction.

    The transaction commits when `work` returns and rolls back when it raises.
    Deadlocks are retried a few times with jitter. Lock timeouts become 503 so
    clients retry with their idempotency key.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            with factory() as session, session.begin():
                return work(session)
        except OperationalError as exc:
            code = sqlstate_of(exc)
            if code == DEADLOCK and attempt < MAX_TX_ATTEMPTS:
                time.sleep(random.uniform(0.01, 0.05) * attempt)
                continue
            if code == LOCK_NOT_AVAILABLE:
                raise ApiError("retry_later", 503, "the slot is busy, retry shortly") from exc
            raise
