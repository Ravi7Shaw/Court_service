"""Initial schema: venues, courts, members, reservations and the no-overlap constraint."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Needed so a GiST index can compare uuid equality alongside time ranges.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "venues",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("peak_rate_minor", sa.BigInteger(), nullable=False),
        sa.Column("offpeak_rate_minor", sa.BigInteger(), nullable=False),
        sa.Column("opening_hour", sa.SmallInteger(), nullable=False),
        sa.Column("closing_hour", sa.SmallInteger(), nullable=False),
        sa.Column("hold_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("payments_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "opening_hour >= 0 AND closing_hour <= 24 AND opening_hour < closing_hour",
            name="venues_hours_valid",
        ),
        sa.CheckConstraint("peak_rate_minor >= 0 AND offpeak_rate_minor >= 0", name="venues_rates_valid"),
    )

    op.create_table(
        "peak_windows",
        sa.Column("venue_id", sa.Uuid(), sa.ForeignKey("venues.id"), primary_key=True),
        sa.Column("day_of_week", sa.SmallInteger(), primary_key=True),
        sa.Column("start_hour", sa.SmallInteger(), primary_key=True),
        sa.Column("end_hour", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            "day_of_week BETWEEN 1 AND 7 AND start_hour >= 0 AND end_hour <= 24 AND start_hour < end_hour",
            name="peak_windows_valid",
        ),
    )
    # Two peak windows for one venue and weekday may not overlap.
    op.execute(
        """
        ALTER TABLE peak_windows ADD CONSTRAINT peak_windows_no_overlap
        EXCLUDE USING gist (
            venue_id WITH =,
            day_of_week WITH =,
            int4range(start_hour, end_hour, '[)') WITH &&
        )
        """
    )

    op.create_table(
        "courts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("venue_id", sa.Uuid(), sa.ForeignKey("venues.id"), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "recurring_series",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("member_id", sa.Uuid(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("court_id", sa.Uuid(), sa.ForeignKey("courts.id"), nullable=False),
        sa.Column("occurrences", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("occurrences BETWEEN 1 AND 3", name="series_occurrences_valid"),
    )

    op.create_table(
        "reservations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("court_id", sa.Uuid(), sa.ForeignKey("courts.id"), nullable=False),
        sa.Column("member_id", sa.Uuid(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("series_id", sa.Uuid(), sa.ForeignKey("recurring_series.id"), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("hold_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_hard_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refund_minor", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "status IN ('held', 'payment_pending', 'confirmed', 'expired', 'cancelled')",
            name="reservations_status_valid",
        ),
        sa.CheckConstraint("ends_at > starts_at", name="reservations_valid_interval"),
        sa.CheckConstraint(
            "extract(epoch FROM (ends_at - starts_at)) % 3600 = 0", name="reservations_whole_hours"
        ),
        sa.CheckConstraint("ends_at - starts_at <= interval '4 hours'", name="reservations_max_duration"),
        sa.CheckConstraint("price_minor >= 0", name="reservations_price_valid"),
        sa.CheckConstraint(
            "refund_minor IS NULL OR refund_minor IN (0, price_minor)",
            name="reservations_refund_zero_or_full",
        ),
        sa.CheckConstraint(
            """
            (status = 'held' AND hold_expires_at IS NOT NULL AND payment_hard_deadline IS NULL
                AND confirmed_at IS NULL AND cancelled_at IS NULL AND refund_minor IS NULL)
            OR (status = 'payment_pending' AND hold_expires_at IS NOT NULL
                AND payment_hard_deadline IS NOT NULL
                AND confirmed_at IS NULL AND cancelled_at IS NULL AND refund_minor IS NULL)
            OR (status = 'confirmed' AND confirmed_at IS NOT NULL
                AND cancelled_at IS NULL AND refund_minor IS NULL)
            OR (status = 'expired' AND hold_expires_at IS NOT NULL
                AND confirmed_at IS NULL AND cancelled_at IS NULL AND refund_minor IS NULL)
            OR (status = 'cancelled' AND confirmed_at IS NOT NULL
                AND cancelled_at IS NOT NULL AND refund_minor IS NOT NULL)
            """,
            name="reservations_status_fields",
        ),
    )
    # The guarantee: live reservations on one court can never overlap. The check is
    # made by the index, so it holds for every client and every concurrent request.
    op.execute(
        """
        ALTER TABLE reservations ADD CONSTRAINT reservations_no_overlap
        EXCLUDE USING gist (
            court_id WITH =,
            tstzrange(starts_at, ends_at, '[)') WITH &&
        ) WHERE (status IN ('held', 'payment_pending', 'confirmed'))
        """
    )
    op.create_index(
        "reservations_member_live",
        "reservations",
        ["member_id", "starts_at"],
        postgresql_where=sa.text("status IN ('held', 'payment_pending', 'confirmed')"),
    )
    op.create_index(
        "reservations_utilization",
        "reservations",
        ["court_id", "starts_at"],
        postgresql_where=sa.text("status = 'confirmed'"),
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("member_id", sa.Uuid(), sa.ForeignKey("members.id"), primary_key=True),
        sa.Column("operation", sa.String(40), primary_key=True),
        sa.Column("key", sa.String(120), primary_key=True),
        sa.Column("reservation_id", sa.Uuid(), sa.ForeignKey("reservations.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_index("reservations_utilization", table_name="reservations")
    op.drop_index("reservations_member_live", table_name="reservations")
    op.drop_table("reservations")
    op.drop_table("recurring_series")
    op.drop_table("members")
    op.drop_table("courts")
    op.drop_table("peak_windows")
    op.drop_table("venues")
