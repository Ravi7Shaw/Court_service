from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, SmallInteger, String, func
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
