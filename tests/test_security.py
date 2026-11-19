"""Security boundaries that must hold independently of proxy and UI behavior."""
from dataclasses import replace
from types import SimpleNamespace
import uuid

import jwt
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api import create_app, HTTP_REQUESTS
from app.config import Settings
from app.errors import ApiError
from app.rate_limit import enforce_rate_limit
from app.security import create_access_token, decode_access_token


class Limiter:
    def __init__(self):
        self.counts = {}

    def eval(self, script, count, key, seconds):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


def request_for(limiter, ip, forwarded):
    return Request({"type": "http", "client": (ip, 1234),
                    "headers": [(b"x-forwarded-for", forwarded.encode())],
                    "app": SimpleNamespace(state=SimpleNamespace(
                        redis=limiter, settings=SimpleNamespace(environment="production")))})


def test_forwarded_header_cannot_rotate_direct_client_limit():
    limiter = Limiter()
    enforce_rate_limit(request_for(limiter, "192.0.2.1", "fake-1"), "login", 1, 900)
    with pytest.raises(ApiError) as error:
        enforce_rate_limit(request_for(limiter, "192.0.2.1", "fake-2"), "login", 1, 900)
    assert error.value.status == 429


def test_email_bucket_is_shared_across_source_ips():
    limiter = Limiter()
    enforce_rate_limit(request_for(limiter, "192.0.2.1", ""), "email-hash", 1, 900, per_ip=False)
    with pytest.raises(ApiError) as error:
        enforce_rate_limit(request_for(limiter, "192.0.2.2", ""), "email-hash", 1, 900, per_ip=False)
    assert error.value.status == 429
    # The separate per-IP limiter still permits a different client.
    enforce_rate_limit(request_for(limiter, "192.0.2.2", ""), "login", 1, 900)


def production_settings():
    return Settings("postgresql+psycopg://unused/unused", "unused", 1, 0,
                    environment="production", jwt_secret="x" * 48,
                    app_base_url="https://courts.example.test", allow_legacy_headers=False,
                    allow_direct_confirm=False, debug_email_tokens=False)


@pytest.mark.parametrize("change", [{"allow_legacy_headers": True}, {"allow_direct_confirm": True},
                                    {"debug_email_tokens": True}, {"jwt_secret": "short"},
                                    {"app_base_url": "http://courts.example.test"}])
def test_production_rejects_unsafe_configuration(change):
    with pytest.raises(RuntimeError):
        replace(production_settings(), **change)


def test_access_tokens_require_expiry_and_identity_claims():
    settings = production_settings()
    claims = decode_access_token(create_access_token(uuid.uuid4(), settings), settings)
    for required in ("exp", "sub", "iat", "jti", "iss"):
        incomplete = {key: value for key, value in claims.items() if key != required}
        with pytest.raises(jwt.InvalidTokenError):
            decode_access_token(jwt.encode(incomplete, settings.jwt_secret, algorithm="HS256"), settings)


def test_unknown_paths_do_not_create_metrics_for_each_url(client: TestClient):
    for _ in range(5):
        assert client.get(f"/arbitrary-{uuid.uuid4()}").status_code == 404
    labels = {sample.labels.get("path") for metric in HTTP_REQUESTS.collect() for sample in metric.samples}
    assert "unmatched" in labels
    assert not any(path.startswith("/arbitrary-") for path in labels)


def test_production_demo_registration_waits_for_admin_without_email(engine):
    app = create_app(production_settings(), engine=engine)
    app.state.redis.close()
    app.state.redis = Limiter()
    # Limiter has no sockets but implements the Redis interface used by this request.
    app.state.redis.close = lambda: None
    with TestClient(app) as client:
        response = client.post("/api/auth/register", json={
            "email": "nodelivery@example.com", "password": "LongEnoughPassword!"})
    assert response.status_code == 201
    assert response.json()["requires_admin_verification"] is True
    assert "debug_verification_token" not in response.json()


def test_smtp_validates_server_certificate(monkeypatch):
    import ssl
    from app.notifications import deliver_email

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self, *, context):
            assert context.check_hostname is True
            assert context.verify_mode == ssl.CERT_REQUIRED

        def send_message(self, message):
            assert message["To"] == "recipient@example.test"

    monkeypatch.setattr("app.notifications.smtplib.SMTP", SMTP)
    deliver_email(replace(production_settings(), email_backend="smtp", smtp_host="smtp.example.test"),
                  "recipient@example.test", "Verify", "Synthetic verification message")


def test_member_cannot_pay_read_cancel_or_refund_another_members_booking(client, factory, venue):
    from app.models import Member, User
    from tests.conftest import slot_on

    headers = []
    with factory() as session, session.begin():
        for index in range(2):
            member = Member(id=uuid.uuid4())
            session.add(member)
            session.flush()
            user = User(member_id=member.id, email=f"ownership-{index}@example.test",
                        password_hash="unused", role="member", is_verified=True, is_active=True)
            session.add(user)
            session.flush()
            headers.append({"Authorization": f"Bearer {create_access_token(user.id, client.app.state.settings)}"})
    start, end = slot_on(5, 10)
    hold = client.post('/holds', headers=headers[0], json={"court_id": str(venue.court_a),
                       "starts_at": start.isoformat(), "ends_at": end.isoformat()})
    assert hold.status_code == 201
    reservation_id = hold.json()['id']
    assert client.post(f'/api/payments/{reservation_id}/simulate', headers=headers[1]).status_code == 404
    assert client.post(f'/api/payments/{reservation_id}/simulate', headers=headers[0]).status_code == 200
    assert client.get(f'/api/payments/{reservation_id}', headers=headers[1]).status_code == 404
    assert client.post(f'/bookings/{reservation_id}/cancel', headers=headers[1]).status_code == 404
    assert client.post(f'/api/payments/{reservation_id}/refund/simulate', headers=headers[1]).status_code == 404
    assert client.get('/api/bookings/me', headers=headers[1]).json() == []
    bookings = client.get('/api/bookings/me', headers=headers[0]).json()
    assert bookings[0]['payment_status'] == 'paid'
    assert bookings[0]['timezone'] == 'Asia/Kolkata'


def test_production_auth_fails_closed_without_redis(engine):
    from app.redis_support import RedisError

    class Unavailable(Limiter):
        def eval(self, *args):
            raise RedisError("synthetic outage")

        def close(self):
            pass

    app = create_app(production_settings(), engine=engine)
    app.state.redis.close()
    app.state.redis = Unavailable()
    with TestClient(app) as client:
        response = client.post('/api/auth/login', json={
            'email': 'synthetic@example.com', 'password': 'SyntheticPassword!'})
    assert response.status_code == 503


def test_http_demo_only_relaxes_transport():
    demo = replace(production_settings(), allow_http_demo=True, app_base_url='http://synthetic.elb.amazonaws.com')
    assert demo.environment == 'production'
    for change in [{'allow_legacy_headers': True}, {'allow_direct_confirm': True},
                   {'debug_email_tokens': True}, {'jwt_secret': 'short'},
                   {'admin_approval_required': False}, {'app_base_url': 'file:///tmp/demo'}]:
        with pytest.raises(RuntimeError):
            replace(demo, **change)


def test_http_demo_from_environment(monkeypatch):
    for key, value in {'APP_ENV':'production', 'JWT_SECRET':'x'*48,
                       'APP_BASE_URL':'http://synthetic.elb.amazonaws.com', 'ALLOW_HTTP_DEMO':'true',
                       'ALLOW_LEGACY_HEADERS':'false', 'ALLOW_DIRECT_CONFIRM':'false', 'DEBUG_EMAIL_TOKENS':'false'}.items():
        monkeypatch.setenv(key,value)
    assert Settings.from_env().allow_http_demo
    monkeypatch.setenv('ALLOW_HTTP_DEMO','false')
    with pytest.raises(RuntimeError):
        Settings.from_env()
