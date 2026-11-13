"""Accounts, notifications, simulated payment ledger and opt-in location."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_platform_features"
down_revision = "0002_idempotency_request_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("venues", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("venues", sa.Column("longitude", sa.Float(), nullable=True))
    op.create_check_constraint(
        "venues_coordinates_valid", "venues",
        "(latitude IS NULL AND longitude IS NULL) OR "
        "(latitude IS NOT NULL AND longitude IS NOT NULL AND "
        "latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)",
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("member_id", sa.Uuid(), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('member', 'staff', 'admin')", name="users_role_valid"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_table(
        "email_verifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_email_verifications_user_id", "email_verifications", ["user_id"])
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("dedupe_key", sa.String(160), nullable=True, unique=True),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending', 'processing', 'sent', 'dead')", name="outbox_status_valid"),
    )
    op.create_index("ix_outbox_pending", "notification_outbox", ["status", "available_at"])
    op.create_index("ix_notification_outbox_user_id", "notification_outbox", ["user_id"])
    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("reservation_id", sa.Uuid(), sa.ForeignKey("reservations.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False, server_default="simulated"),
        sa.Column("provider_reference", sa.String(120), nullable=False, unique=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="paid"),
        sa.Column("refund_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("amount_minor >= 0 AND refund_minor >= 0 AND refund_minor <= amount_minor", name="payments_amounts_valid"),
        sa.CheckConstraint("status IN ('paid', 'partially_refunded', 'refunded')", name="payments_status_valid"),
    )
    op.create_index("ix_payments_user_id", "payments", ["user_id"])
    op.create_table(
        "user_locations",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("consent_version", sa.String(20), nullable=False, server_default="1"),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180", name="user_location_coordinates_valid"),
    )


def downgrade() -> None:
    op.drop_table("user_locations")
    op.drop_index("ix_payments_user_id", table_name="payments")
    op.drop_table("payments")
    op.drop_index("ix_notification_outbox_user_id", table_name="notification_outbox")
    op.drop_index("ix_outbox_pending", table_name="notification_outbox")
    op.drop_table("notification_outbox")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_email_verifications_user_id", table_name="email_verifications")
    op.drop_table("email_verifications")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.drop_constraint("venues_coordinates_valid", "venues", type_="check")
    op.drop_column("venues", "longitude")
    op.drop_column("venues", "latitude")
