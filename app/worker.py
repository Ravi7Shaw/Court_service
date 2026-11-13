from __future__ import annotations

import logging
import signal
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from app.config import Settings
from app.db import make_engine, make_session_factory
from app.models import Notification, NotificationOutbox, Reservation
from app.notifications import deliver_email

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("court.worker")


def process_one(factory, settings: Settings) -> bool:
    now = datetime.now(UTC)
    # Claim and commit before external I/O. SKIP LOCKED lets multiple workers coexist.
    with factory() as session, session.begin():
        row = session.scalar(select(NotificationOutbox).where(
            NotificationOutbox.status.in_(["pending", "processing"]),
            NotificationOutbox.available_at <= now,
        ).order_by(NotificationOutbox.created_at).with_for_update(skip_locked=True).limit(1))
        if row is None:
            return False
        row.status = "processing"
        row.available_at = now + timedelta(minutes=5)
        row.attempts += 1
        row_id = row.id
        recipient, subject, body, user_id = row.recipient, row.subject, row.body, row.user_id
    try:
        deliver_email(settings, recipient, subject, body)
    except Exception as exc:
        with factory() as session, session.begin():
            row = session.get(NotificationOutbox, row_id)
            if row:
                row.last_error = str(exc)[:2000]
                if row.attempts >= 5:
                    row.status = "dead"
                else:
                    row.status = "pending"
                    row.available_at = datetime.now(UTC) + timedelta(seconds=min(3600, 2 ** row.attempts * 15))
        log.exception("email delivery failed recipient=%s", recipient)
    else:
        with factory() as session, session.begin():
            row = session.get(NotificationOutbox, row_id)
            if row:
                row.status = "sent"
                row.sent_at = datetime.now(UTC)
                row.last_error = None
            session.add(Notification(user_id=user_id, kind="email_sent", title="Email sent",
                                     message=f"We sent '{subject}' to {recipient}."))
    return True


def expire_holds(factory) -> None:
    with factory() as session, session.begin():
        session.execute(update(Reservation).where(
            Reservation.status == "held", Reservation.hold_expires_at <= func.clock_timestamp()
        ).values(status="expired"))


def main() -> None:
    settings = Settings.from_env()
    engine = make_engine(settings.database_url, settings.pool_size, settings.max_overflow)
    factory = make_session_factory(engine)
    stopping = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        log.info("notification worker stopping")
        stopping.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    log.info("notification worker started")
    try:
        while not stopping.is_set():
            try:
                worked = process_one(factory, settings)
                expire_holds(factory)
                if not worked:
                    stopping.wait(2)
            except Exception:
                log.exception("worker iteration failed")
                stopping.wait(5)
    finally:
        engine.dispose()

if __name__ == "__main__":
    main()
