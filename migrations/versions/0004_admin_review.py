"""Durable demo approvals and fenced notification delivery claims."""
import sqlalchemy as sa
from alembic import op

revision = "0004_admin_review"
down_revision = "0003_platform_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("reservation_id", sa.Uuid(), sa.ForeignKey("reservations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("member_message", sa.String(500), nullable=False, server_default=""),
        sa.Column("review_note", sa.String(500), nullable=False, server_default=""),
        sa.Column("reviewed_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("reservation_id", "kind", name="approval_booking_kind"),
        sa.CheckConstraint("kind IN ('payment', 'refund')", name="approval_kind_valid"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'expired')", name="approval_status_valid"),
    )
    op.create_index("ix_approvals_pending", "approval_requests", ["status", "created_at"])
    op.create_index("ix_outbox_claimable", "notification_outbox", ["status", "available_at", "created_at"])
    op.add_column("notification_outbox", sa.Column("claim_token", sa.Uuid(), nullable=True))
    op.drop_constraint("outbox_status_valid", "notification_outbox", type_="check")
    op.create_check_constraint("outbox_status_valid", "notification_outbox",
                               "status IN ('pending', 'processing', 'sent', 'simulated', 'dead')")


def downgrade() -> None:
    op.execute("UPDATE notification_outbox SET status='sent' WHERE status='simulated'")
    op.drop_constraint("outbox_status_valid", "notification_outbox", type_="check")
    op.create_check_constraint("outbox_status_valid", "notification_outbox",
                               "status IN ('pending', 'processing', 'sent', 'dead')")
    op.drop_index("ix_outbox_claimable", table_name="notification_outbox")
    op.drop_column("notification_outbox", "claim_token")
    op.drop_index("ix_approvals_pending", table_name="approval_requests")
    op.drop_table("approval_requests")
