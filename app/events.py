"""Kafka wakeup for the notification worker.

Kafka carries only a signal: "outbox work exists". It never carries the job.

* PostgreSQL remains the source of truth. A notification is written in the same
  transaction as the business change, and the worker always claims work from
  ``notification_outbox`` with ``FOR UPDATE SKIP LOCKED``.
* The signal is published only after a commit that created an outbox row, so the
  worker is never woken for work that does not exist (no dual-write race).
* Publishing is best effort. A broker outage is logged and never fails the request.
  The worker's periodic sweep recovers anything a lost signal would have announced.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from sqlalchemy import event
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

OUTBOX_DIRTY = "outbox_dirty"
PAYLOAD = b'{"event":"outbox_ready"}'


class WakeupPublisher(Protocol):
    def publish_ready(self) -> None: ...

    def close(self) -> None: ...


class NullPublisher:
    """Used when Kafka is not configured. The worker then relies on its sweep interval."""

    def publish_ready(self) -> None:
        return None

    def close(self) -> None:
        return None


class KafkaPublisher:
    def __init__(self, bootstrap_servers: str, topic: str) -> None:
        from confluent_kafka import Producer

        self._topic = topic
        self._producer = Producer({
            "bootstrap.servers": bootstrap_servers,
            "linger.ms": 5,
            "message.timeout.ms": 5000,
            "acks": "all",
            "enable.idempotence": True,
        })

    def publish_ready(self) -> None:
        from confluent_kafka import KafkaException

        try:
            self._producer.produce(self._topic, value=PAYLOAD, on_delivery=_log_delivery)
        except (BufferError, KafkaException):
            log.warning("wakeup not queued; the worker sweep will pick the work up")
        self._producer.poll(0)

    def close(self) -> None:
        self._producer.flush(5)


def _log_delivery(err: Any, _msg: Any) -> None:
    if err is not None:
        log.warning("wakeup delivery failed code=%s", err.code() if hasattr(err, "code") else err)


_publisher: WakeupPublisher = NullPublisher()


def set_publisher(publisher: WakeupPublisher) -> None:
    global _publisher
    _publisher = publisher


def get_publisher() -> WakeupPublisher:
    return _publisher


def mark_outbox_dirty(session: Session) -> None:
    """Called when a transaction creates an outbox row. Publishing waits for the commit."""
    session.info[OUTBOX_DIRTY] = True


@event.listens_for(Session, "after_commit")
def _publish_after_commit(session: Session) -> None:
    if not session.info.pop(OUTBOX_DIRTY, False):
        return
    try:
        _publisher.publish_ready()
    except Exception:  # The commit has already happened; a wakeup failure must never surface as a 500.
        log.exception("wakeup publish failed")


@event.listens_for(Session, "after_rollback")
def _discard_on_rollback(session: Session) -> None:
    session.info.pop(OUTBOX_DIRTY, None)


class KafkaWaker:
    """Blocks until a wakeup arrives or the timeout expires. Consumes in short slices to honour stop()."""

    def __init__(self, bootstrap_servers: str, topic: str, group: str, should_stop: Callable[[], bool]) -> None:
        from confluent_kafka import Consumer

        self._consumer = Consumer({
            "bootstrap.servers": bootstrap_servers,
            "group.id": group,
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
        })
        self._consumer.subscribe([topic])
        self._should_stop = should_stop

    def wait(self, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        woke = False
        while not self._should_stop():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            for message in self._consumer.consume(num_messages=100, timeout=min(1.0, remaining)):
                if message.error() is None:
                    woke = True
                else:
                    log.warning("wakeup consume error; sweep continues")
            if woke:
                break
        return woke

    def close(self) -> None:
        self._consumer.close()


class SleepWaker:
    """Fallback without Kafka: wait out the interval, returning early only on stop."""

    def __init__(self, should_stop: Callable[[], bool]) -> None:
        self._should_stop = should_stop

    def wait(self, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while not self._should_stop() and time.monotonic() < deadline:
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
        return False

    def close(self) -> None:
        return None
