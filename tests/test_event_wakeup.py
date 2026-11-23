"""Kafka wakeups: fired only after a commit that created outbox work, never able to fail a request,
and the worker drains work instead of scanning on a timer."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app import events
from app.config import Settings
from app.db import run_tx
from app.models import Member, NotificationOutbox, User
from app.notifications import notify_and_queue_email
from app.worker import build_waker, drain
from tests.conftest import TEST_URL


class RecordingPublisher:
    def __init__(self, fail: bool = False) -> None:
        self.count = 0
        self.fail = fail

    def publish_ready(self) -> None:
        if self.fail:
            raise ConnectionError("broker unreachable")
        self.count += 1

    def close(self) -> None:
        return None


@pytest.fixture()
def publisher():
    recorder = RecordingPublisher()
    events.set_publisher(recorder)
    yield recorder
    events.set_publisher(events.NullPublisher())


def _user(factory: sessionmaker[Session]) -> uuid.UUID:
    with factory() as session, session.begin():
        member = Member(id=uuid.uuid4())
        session.add(member)
        session.flush()
        user = User(member_id=member.id, email=f"{uuid.uuid4().hex[:8]}@gmail.com", password_hash="x",
                    role="member", is_verified=True, is_active=True)
        session.add(user)
        session.flush()
        return user.id


def _notify(factory: sessionmaker[Session], user_id: uuid.UUID, fail_after: bool = False) -> None:
    def work(session: Session) -> None:
        user = session.get(User, user_id)
        assert user is not None
        notify_and_queue_email(session, user, "test", "Hello", "body", "Subject", "Body")
        if fail_after:
            raise RuntimeError("business rule failed after the notification was queued")

    run_tx(factory, work)


def test_committed_outbox_row_wakes_the_worker_exactly_once(factory, publisher) -> None:
    user_id = _user(factory)
    _notify(factory, user_id)
    assert publisher.count == 1


def test_rolled_back_work_never_wakes_the_worker(factory, publisher) -> None:
    user_id = _user(factory)
    with pytest.raises(RuntimeError):
        _notify(factory, user_id, fail_after=True)
    assert publisher.count == 0
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 0


def test_commits_without_outbox_work_do_not_wake_the_worker(factory, publisher) -> None:
    _user(factory)
    assert publisher.count == 0


def test_a_broker_failure_does_not_fail_the_request_or_lose_the_work(factory) -> None:
    events.set_publisher(RecordingPublisher(fail=True))
    try:
        user_id = _user(factory)
        _notify(factory, user_id)  # must not raise
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 1
    finally:
        events.set_publisher(events.NullPublisher())


def test_drain_delivers_every_pending_message_then_stops(factory) -> None:
    user_id = _user(factory)
    for _ in range(3):
        _notify(factory, user_id)
    settings = Settings(TEST_URL, "k", 5, 5, email_backend="console")
    assert drain(factory, settings, lambda: False) == 3
    with factory() as session:
        statuses = set(session.scalars(select(NotificationOutbox.status)))
    assert statuses == {"simulated"}


class FakeMessage:
    def __init__(self, error=None) -> None:
        self._error = error

    def error(self):
        return self._error


class FakeConsumer:
    def __init__(self, batches) -> None:
        self._batches = list(batches)
        self.calls = 0

    def consume(self, num_messages: int, timeout: float):
        self.calls += 1
        return self._batches.pop(0) if self._batches else []


def test_kafka_waker_returns_on_a_wakeup_and_ignores_errors() -> None:
    waker = events.KafkaWaker.__new__(events.KafkaWaker)
    waker._consumer = FakeConsumer([[FakeMessage(error="transport")], [FakeMessage()]])
    waker._should_stop = lambda: False
    assert waker.wait(30) is True
    assert waker._consumer.calls == 2


def test_kafka_waker_times_out_without_busy_looping() -> None:
    waker = events.KafkaWaker.__new__(events.KafkaWaker)
    waker._consumer = FakeConsumer([])
    waker._should_stop = lambda: False
    assert waker.wait(0.05) is False
    assert waker._consumer.calls >= 1


def test_worker_uses_the_sleep_waker_without_kafka_and_stops_promptly() -> None:
    settings = Settings(TEST_URL, "k", 5, 5, kafka_bootstrap_servers="", worker_sweep_seconds=2.0)
    stop = {"now": False}
    waker = build_waker(settings, lambda: stop["now"])
    assert isinstance(waker, events.SleepWaker)
    stop["now"] = True
    assert waker.wait(60) is False  # returns immediately once stop is requested
