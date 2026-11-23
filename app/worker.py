from __future__ import annotations

import logging
import signal
import threading
import time
import uuid
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import func, select, update, or_, and_

from app.config import Settings
from app.db import make_engine, make_session_factory
from app.models import Notification, NotificationOutbox, Reservation, ApprovalRequest
from app.notifications import deliver_email
from app.services.common import db_now

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("court.worker")


def process_one(factory, settings: Settings) -> bool:
    # A committed lease survives process crashes. The token fences late completions.
    with factory() as session, session.begin():
        now = db_now(session)
        row = session.scalar(select(NotificationOutbox).where(
            NotificationOutbox.status.in_(["pending", "processing"]),
            NotificationOutbox.available_at <= now,
        ).order_by(NotificationOutbox.created_at, NotificationOutbox.id)
          .with_for_update(skip_locked=True).limit(1))
        if row is None:
            return False
        if row.attempts >= 5:
            row.status, row.claim_token = "dead", None
            row.last_error = "Delivery attempt limit reached"
            return True
        claim = uuid.uuid4()
        row.status, row.claim_token = "processing", claim
        row.available_at = now + timedelta(minutes=5)
        row.attempts += 1
        row_id = row.id
        recipient, subject, body, user_id = row.recipient, row.subject, row.body, row.user_id
    error = None
    try:
        deliver_email(settings, recipient, subject, body)
    except Exception as exc:
        error = type(exc).__name__  # Never persist credentials or message bodies from provider errors.
        log.warning("delivery failed message=%s error=%s", row_id, error)
    with factory() as session, session.begin():
        row = session.scalar(select(NotificationOutbox).where(
            NotificationOutbox.id == row_id, NotificationOutbox.status == "processing",
            NotificationOutbox.claim_token == claim, NotificationOutbox.available_at > func.clock_timestamp(),
        ).with_for_update())
        if row is None:
            return True
        row.claim_token = None
        if error:
            row.last_error = error
            row.status = "dead" if row.attempts >= 5 else "pending"
            row.available_at = db_now(session) + timedelta(seconds=min(3600, 2 ** row.attempts * 15))
        else:
            simulated = settings.email_backend == "console"
            row.status = "simulated" if simulated else "sent"
            row.sent_at = db_now(session)
            row.last_error = None
            session.add(Notification(user_id=user_id, kind="delivery_simulated" if simulated else "email_sent",
                title="Demo message recorded" if simulated else "Email sent",
                message=f"{subject}: delivery simulated; no email or SMS sent." if simulated else f"Email sent: {subject}"))
    return True


def expire_holds(factory) -> None:
    with factory() as session, session.begin():
        session.execute(update(Reservation).where(
            or_(and_(Reservation.status == "held", Reservation.hold_expires_at <= func.clock_timestamp()),
                and_(Reservation.status == "payment_pending", or_(
                    Reservation.hold_expires_at <= func.clock_timestamp(),
                    Reservation.payment_hard_deadline <= func.clock_timestamp())))
        ).values(status="expired"))
        session.execute(update(ApprovalRequest).where(
            ApprovalRequest.status == "pending", ApprovalRequest.kind == "payment",
            ApprovalRequest.reservation_id.in_(select(Reservation.id).where(Reservation.status == "expired"))
        ).values(status="expired"))


def drain(factory, settings: Settings, should_stop: Callable[[], bool]) -> int:
    """Deliver every claimable message, then return. Used on each wakeup and on each sweep."""
    processed = 0
    while not should_stop() and process_one(factory, settings):
        processed += 1
    return processed


def build_waker(settings: Settings, should_stop: Callable[[], bool]):  # noqa: ANN201
    if settings.kafka_bootstrap_servers:
        from app.events import KafkaWaker

        log.info("waking on Kafka topic %s; sweep every %.0fs", settings.kafka_topic, settings.worker_sweep_seconds)
        return KafkaWaker(settings.kafka_bootstrap_servers, settings.kafka_topic,
                          settings.kafka_consumer_group, should_stop)
    from app.events import SleepWaker

    log.info("no Kafka configured; polling every %.0fs", settings.worker_sweep_seconds)
    return SleepWaker(should_stop)


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
    waker = build_waker(settings, stopping.is_set)
    log.info("notification worker started")
    next_sweep = 0.0
    try:
        while not stopping.is_set():
            try:
                drain(factory, settings, stopping.is_set)
                now = time.monotonic()
                if now >= next_sweep:
                    # Expiry is housekeeping, not a delivery path, so it runs on a timer, not per wakeup.
                    expire_holds(factory)
                    next_sweep = now + settings.worker_sweep_seconds
                waker.wait(next_sweep - time.monotonic())
            except Exception:
                log.exception("worker iteration failed")
                stopping.wait(5)
    finally:
        waker.close()
        engine.dispose()


if __name__ == "__main__":
    main()
