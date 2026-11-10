"""Persist the semantic request fingerprint for each idempotency key.

Existing rows receive a legacy sentinel because their original request payload
was not stored. Replaying such a key fails closed rather than guessing.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_idempotency_request_hash"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "idempotency_keys",
        sa.Column(
            "request_hash",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'legacy'"),
        ),
    )
    op.alter_column("idempotency_keys", "request_hash", server_default=None)


def downgrade() -> None:
    op.drop_column("idempotency_keys", "request_hash")
