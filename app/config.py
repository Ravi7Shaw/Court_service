from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    staff_key: str
    pool_size: int
    max_overflow: int
    environment: str = "development"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "development-only-change-me"
    jwt_issuer: str = "court-service"
    jwt_minutes: int = 30
    email_backend: str = "console"
    payment_provider: str = "simulated"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@example.test"
    app_base_url: str = "http://localhost:8080"
    allow_legacy_headers: bool = True
    allow_direct_confirm: bool = True
    debug_email_tokens: bool = True
    admin_approval_required: bool = True
    allow_http_demo: bool = False
    kafka_bootstrap_servers: str = ""
    kafka_topic: str = "court.notifications.ready"
    kafka_consumer_group: str = "court-notification-workers"
    worker_sweep_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.environment == "production":
            if not self.admin_approval_required:
                raise RuntimeError("Production requires admin approval for demo bookings")
            if len(self.jwt_secret) < 32:
                raise RuntimeError("JWT_SECRET must be at least 32 characters in production")
            if self.allow_legacy_headers or self.allow_direct_confirm or self.debug_email_tokens:
                raise RuntimeError("Production must disable legacy headers, direct confirmation and debug email tokens")
            if not (self.app_base_url.startswith("https://") or
                    (self.allow_http_demo and self.app_base_url.startswith("http://"))):
                raise RuntimeError("APP_BASE_URL must use HTTPS in production")

    @classmethod
    def from_env(cls) -> Settings:
        environment = os.environ.get("APP_ENV", "development")
        jwt_secret = os.environ.get("JWT_SECRET", "development-only-change-me")
        kafka_servers = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "").strip()
        if environment == "production" and len(jwt_secret) < 32:
            raise RuntimeError("JWT_SECRET must be at least 32 characters in production")
        return cls(
            database_url=os.environ.get(
                "DATABASE_URL", "postgresql+psycopg://booking:booking@localhost:5432/booking"
            ),
            staff_key=os.environ.get("STAFF_KEY", "dev-staff-key"),
            pool_size=int(os.environ.get("DB_POOL_SIZE", "5")),
            max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "5")),
            environment=environment,
            allow_http_demo=os.environ.get("ALLOW_HTTP_DEMO", "false").lower() == "true",
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            jwt_secret=jwt_secret,
            jwt_issuer=os.environ.get("JWT_ISSUER", "court-service"),
            jwt_minutes=int(os.environ.get("JWT_MINUTES", "30")),
            email_backend=os.environ.get("EMAIL_BACKEND", "console"),
            payment_provider=os.environ.get("PAYMENT_PROVIDER", "simulated"),
            smtp_host=os.environ.get("SMTP_HOST", ""),
            smtp_port=int(os.environ.get("SMTP_PORT", "587")),
            smtp_username=os.environ.get("SMTP_USERNAME", ""),
            smtp_password=os.environ.get("SMTP_PASSWORD", ""),
            smtp_from=os.environ.get("SMTP_FROM", "no-reply@example.test"),
            app_base_url=os.environ.get("APP_BASE_URL", "http://localhost:8080"),
            allow_legacy_headers=os.environ.get("ALLOW_LEGACY_HEADERS", "false").lower() == "true",
            allow_direct_confirm=os.environ.get("ALLOW_DIRECT_CONFIRM", "false").lower() == "true",
            debug_email_tokens=os.environ.get("DEBUG_EMAIL_TOKENS", "false").lower() == "true",
            kafka_bootstrap_servers=kafka_servers,
            kafka_topic=os.environ.get("KAFKA_NOTIFICATION_TOPIC", "court.notifications.ready"),
            kafka_consumer_group=os.environ.get("KAFKA_CONSUMER_GROUP", "court-notification-workers"),
            # With Kafka the sweep is only a safety net; without it, keep the original 2-second cadence.
            worker_sweep_seconds=float(os.environ.get("WORKER_SWEEP_SECONDS",
                                                     "60" if kafka_servers else "2")),
        )
