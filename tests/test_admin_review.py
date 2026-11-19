"""New demo contract; the legacy booking tests remain independent compatibility coverage."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api import create_app
from app.models import ApprovalRequest, EmailVerification, Member, Notification, NotificationOutbox, Payment, Reservation, User
from app.security import create_access_token
from app.services import approvals
from app.db import run_tx
from app.services.common import db_now
from app.worker import expire_holds, process_one
from tests.conftest import slot_on


@pytest.fixture
def review(engine, factory, client):
    settings = replace(client.app.state.settings, admin_approval_required=True,
                       redis_url='redis://127.0.0.1:1/0')
    app = create_app(settings, engine=engine)
    with factory() as session, session.begin():
        users = []
        for role in ['member', 'admin', 'member']:
            member = Member(id=uuid.uuid4())
            session.add(member)
            session.flush()
            user = User(member_id=member.id, email=f'{uuid.uuid4()}@example.com',
                        password_hash='unused', role=role, is_active=True, is_verified=True)
            session.add(user)
            session.flush()
            users.append(user)
    with TestClient(app) as browser:
        yield browser, users, settings


def headers(user, settings):
    return {'Authorization': f'Bearer {create_access_token(user.id, settings)}'}


def report(review, venue):
    browser, users, settings = review
    start, end = slot_on(5, 10)
    hold = browser.post('/holds', headers=headers(users[0], settings), json={
        'court_id': str(venue.court_a), 'starts_at': start.isoformat(), 'ends_at': end.isoformat()})
    assert hold.status_code == 201
    booking_id = hold.json()['id']
    response = browser.post(f'/api/payments/{booking_id}/report', headers=headers(users[0], settings),
                            json={'message': 'Payment succeeded (member demo report)'})
    assert response.status_code == 202
    return booking_id, response.json()['id']


def decision(review, request_id, approve=True):
    browser, users, settings = review
    return browser.post(f'/api/admin/approvals/{request_id}/decision', headers=headers(users[1], settings),
                        json={'approve': approve, 'note': 'Checked by demo admin'})


def test_member_report_is_not_payment_and_admin_confirms_once(review, venue, factory):
    browser, users, settings = review
    booking_id, request_id = report(review, venue)
    with factory() as session:
        assert session.get(Reservation, uuid.UUID(booking_id)).status == 'payment_pending'
        assert session.scalar(select(func.count()).select_from(Payment)) == 0
        before = session.get(Reservation, uuid.UUID(booking_id)).hold_expires_at
    repeat = browser.post(f'/holds/{booking_id}/confirm', headers=headers(users[0], settings))
    assert repeat.status_code == 202
    assert repeat.json()['id'] == request_id
    with factory() as session:
        assert session.get(Reservation, uuid.UUID(booking_id)).hold_expires_at == before
    assert decision(review, request_id).json()['status'] == 'approved'
    assert decision(review, request_id).json()['status'] == 'approved'
    assert decision(review, request_id, False).status_code == 409
    with factory() as session:
        assert session.get(Reservation, uuid.UUID(booking_id)).status == 'confirmed'
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        assert session.scalar(select(func.count()).select_from(NotificationOutbox).where(
            NotificationOutbox.dedupe_key == f'decision:{request_id}')) == 1


def test_concurrent_admin_retries_one_payment(review, venue, factory):
    _, request_id = report(review, venue)
    admin = review[1][1]
    def approve(_):
        return run_tx(factory, lambda s: approvals.decide(s, uuid.UUID(request_id), admin, True, '').status)
    with ThreadPoolExecutor(max_workers=12) as pool:
        assert set(pool.map(approve, range(12))) == {'approved'}
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 1


def test_members_cannot_review_other_reports_or_read_admin_queue(review, venue):
    browser, users, settings = review
    booking_id, request_id = report(review, venue)
    for index in [0, 2]:
        member = headers(users[index], settings)
        assert browser.get('/api/admin/approvals', headers=member).status_code == 403
        assert browser.get('/api/admin/outbox', headers=member).status_code == 403
        assert browser.post(f'/api/admin/approvals/{request_id}/decision', headers=member,
                            json={'approve': True}).status_code == 403
    assert browser.post(f'/api/payments/{booking_id}/simulate', headers=headers(users[2], settings)).status_code == 404
    repeat = browser.post(f'/api/payments/{booking_id}/simulate', headers=headers(users[0], settings))
    assert repeat.status_code == 202  # Old URL cannot bypass review.
    assert repeat.json()['status'] == 'pending'


def test_expired_review_never_revives_booking_and_slot_is_reusable(review, venue, factory):
    browser, users, settings = review
    booking_id, request_id = report(review, venue)
    with factory() as session, session.begin():
        reservation = session.get(Reservation, uuid.UUID(booking_id))
        reservation.hold_expires_at = db_now(session) - timedelta(seconds=1)
    assert decision(review, request_id).json()['status'] == 'expired'
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 0
        assert session.get(Reservation, uuid.UUID(booking_id)).status == 'expired'
    start, end = slot_on(5, 10)
    assert browser.post('/holds', headers=headers(users[2], settings), json={
        'court_id': str(venue.court_a), 'starts_at': start.isoformat(), 'ends_at': end.isoformat()}).status_code == 201


def test_worker_expires_pending_approval(review, venue, factory):
    booking_id, request_id = report(review, venue)
    with factory() as session, session.begin():
        session.get(Reservation, uuid.UUID(booking_id)).payment_hard_deadline = db_now(session) - timedelta(seconds=1)
    expire_holds(factory)
    with factory() as session:
        assert session.get(Reservation, uuid.UUID(booking_id)).status == 'expired'
        assert session.get(ApprovalRequest, uuid.UUID(request_id)).status == 'expired'


def test_refund_requires_admin_and_stored_eligibility(review, venue, factory):
    browser, users, settings = review
    booking_id, request_id = report(review, venue)
    assert decision(review, request_id).status_code == 200
    member = headers(users[0], settings)
    assert browser.post(f'/bookings/{booking_id}/cancel', headers=member).json()['refund_minor'] > 0
    requested = browser.post(f'/api/payments/{booking_id}/refund/request', headers=member)
    assert requested.status_code == 202
    with factory() as session:
        assert session.scalar(select(Payment)).status == 'paid'
    assert decision(review, requested.json()['id']).json()['status'] == 'approved'
    assert decision(review, requested.json()['id']).json()['status'] == 'approved'
    with factory() as session:
        payment = session.scalar(select(Payment))
        assert payment.status == 'refunded'
        assert payment.refund_minor == payment.amount_minor


def test_failed_decision_transaction_rolls_back_ledger_and_status(review, venue, factory, monkeypatch):
    booking_id, request_id = report(review, venue)
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic queue failure')
    monkeypatch.setattr(approvals, 'notify_and_queue_email', fail)
    with pytest.raises(RuntimeError):
        run_tx(factory, lambda s: approvals.decide(s, uuid.UUID(request_id), review[1][1], True, ''))
    with factory() as session:
        assert session.get(Reservation, uuid.UUID(booking_id)).status == 'payment_pending'
        assert session.get(ApprovalRequest, uuid.UUID(request_id)).status == 'pending'
        assert session.scalar(select(func.count()).select_from(Payment)) == 0


def queue_one(factory, user):
    with factory() as session, session.begin():
        row = NotificationOutbox(user_id=user.id, recipient=user.email, subject='Synthetic event', body='Demo only')
        session.add(row)
        session.flush()
        return row.id


def test_queue_simulation_is_truthful_and_duplicate_claims_do_not_duplicate_delivery(review, factory):
    message_id = queue_one(factory, review[1][0])
    def work(_):
        return process_one(factory, review[2])
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(work, range(8))) == 1
    with factory() as session:
        row = session.get(NotificationOutbox, message_id)
        assert row.status == 'simulated'
        assert row.attempts == 1
        assert row.claim_token is None
        assert session.scalar(select(Notification)).kind == 'delivery_simulated'


def test_queue_failure_backoff_dead_letter_and_admin_retry(review, factory, monkeypatch):
    message_id = queue_one(factory, review[1][0])
    def failure(*args):
        raise RuntimeError('private-provider-secret-must-not-persist')
    monkeypatch.setattr('app.worker.deliver_email', failure)
    for attempt in range(5):
        assert process_one(factory, review[2])
        with factory() as session, session.begin():
            row = session.get(NotificationOutbox, message_id)
            assert row.attempts == attempt + 1
            assert row.last_error == 'RuntimeError'
            assert row.available_at > db_now(session)
            row.available_at = db_now(session) - timedelta(seconds=1)
    with factory() as session:
        assert session.get(NotificationOutbox, message_id).status == 'dead'
    browser, users, settings = review
    assert browser.post(f'/api/admin/outbox/{message_id}/retry', headers=headers(users[0], settings)).status_code == 403
    assert browser.post(f'/api/admin/outbox/{message_id}/retry', headers=headers(users[1], settings)).status_code == 200
    with factory() as session:
        row = session.get(NotificationOutbox, message_id)
        assert (row.status, row.attempts) == ('pending', 0)


def test_crashed_worker_fifth_claim_cannot_retry_forever(review, factory):
    message_id = queue_one(factory, review[1][0])
    with factory() as session, session.begin():
        row = session.get(NotificationOutbox, message_id)
        row.status, row.attempts, row.claim_token = 'processing', 5, uuid.uuid4()
        row.available_at = db_now(session) - timedelta(seconds=1)
    assert process_one(factory, review[2])
    with factory() as session:
        assert session.get(NotificationOutbox, message_id).status == 'dead'


def test_stale_worker_cannot_acknowledge_reclaimed_message(review, factory, monkeypatch):
    message_id = queue_one(factory, review[1][0])
    replacement = uuid.uuid4()
    def reclaim(*args):
        with factory() as session, session.begin():
            row = session.get(NotificationOutbox, message_id)
            row.claim_token = replacement
    monkeypatch.setattr('app.worker.deliver_email', reclaim)
    assert process_one(factory, review[2])
    with factory() as session:
        row = session.get(NotificationOutbox, message_id)
        assert row.status == 'processing'
        assert row.claim_token == replacement
        assert session.scalar(select(func.count()).select_from(Notification)) == 0


def test_admin_verifies_account_without_exposing_email_token(review, factory):
    browser, users, settings = review
    registered = browser.post('/api/auth/register', json={'email': 'manual-review@example.com', 'password': 'StrongPassword-2026!'})
    assert registered.status_code == 201
    assert registered.json()['requires_admin_verification'] is True
    assert 'debug_verification_token' not in registered.json()
    user_id = registered.json()['id']
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(EmailVerification)) == 0
        assert session.scalar(select(Notification).where(Notification.user_id == uuid.UUID(user_id))).kind == 'account_pending'
    assert browser.post(f'/api/admin/users/{user_id}/verify', headers=headers(users[0], settings)).status_code == 403
    assert browser.post(f'/api/admin/users/{user_id}/verify', headers=headers(users[1], settings)).status_code == 200
    assert browser.post('/api/auth/login', json={'email': 'manual-review@example.com', 'password': 'StrongPassword-2026!'}).status_code == 200


def test_rejection_frees_slot_and_cannot_be_reversed(review, venue, factory):
    browser, users, settings = review
    booking_id, request_id = report(review, venue)
    assert decision(review, request_id, False).json()['status'] == 'rejected'
    assert decision(review, request_id, False).json()['status'] == 'rejected'
    assert decision(review, request_id).status_code == 409
    with factory() as session:
        assert session.get(Reservation, uuid.UUID(booking_id)).status == 'expired'
        assert session.scalar(select(func.count()).select_from(Payment)) == 0
    start, end = slot_on(5, 10)
    assert browser.post('/holds', headers=headers(users[2], settings), json={
        'court_id': str(venue.court_a), 'starts_at': start.isoformat(), 'ends_at': end.isoformat()}).status_code == 201


def test_legacy_recurring_route_cannot_bypass_review_and_long_key_is_rejected(review, venue, factory):
    browser, users, settings = review
    start, end = slot_on(5, 10)
    body = {'court_id': str(venue.court_a), 'starts_at': start.isoformat(), 'ends_at': end.isoformat()}
    assert browser.post('/recurring-bookings', headers=headers(users[0], settings),
                        json={**body, 'occurrences': 2}).status_code == 409
    assert browser.post('/holds', headers={**headers(users[0], settings), 'Idempotency-Key': 'x' * 121},
                        json=body).status_code == 422
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Reservation)) == 0
