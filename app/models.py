from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, SmallInteger, String, Text, Float, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Venue(Base):
    __tablename__ = "venues"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    peak_rate_minor: Mapped[int] = mapped_column(BigInteger)
    offpeak_rate_minor: Mapped[int] = mapped_column(BigInteger)
    opening_hour: Mapped[int] = mapped_column(SmallInteger)
    closing_hour: Mapped[int] = mapped_column(SmallInteger)
    hold_ttl_seconds: Mapped[int] = mapped_column(Integer)
    payments_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)


class PeakWindow(Base):
    __tablename__ = "peak_windows"

    venue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("venues.id"), primary_key=True)
    day_of_week: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    start_hour: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    end_hour: Mapped[int] = mapped_column(SmallInteger)


class Court(Base):
    __tablename__ = "courts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    venue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str] = mapped_column(String(60))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Member(Base):
    __tablename__ = "members"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecurringSeries(Base):
    __tablename__ = "recurring_series"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id"))
    court_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courts.id"))
    occurrences: Mapped[int] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Reservation(Base):
    __tablename__ = "reservations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    court_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courts.id"))
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id"))
    series_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recurring_series.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(24))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    price_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_hard_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refund_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id"), primary_key=True)
    operation: Mapped[str] = mapped_column(String(40), primary_key=True)
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reservations.id"), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), unique=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="member", server_default="member")
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmailVerification(Base):
    __tablename__ = "email_verifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(160))
    message: Mapped[str] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    recipient: Mapped[str] = mapped_column(String(320))
    dedupe_key: Mapped[str | None] = mapped_column(String(160), unique=True, nullable=True)
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    claim_token: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    reservation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reservations.id", ondelete="CASCADE"), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40), default="simulated")
    provider_reference: Mapped[str] = mapped_column(String(120), unique=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(24), default="paid")
    refund_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UserLocation(Base):
    __tablename__ = "user_locations"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    consent_version: Mapped[str] = mapped_column(String(20), default="1")


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    reservation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reservations.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    member_message: Mapped[str] = mapped_column(String(500), default="")
    review_note: Mapped[str] = mapped_column(String(500), default="")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
