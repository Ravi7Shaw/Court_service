"""Index the venue coordinates and the court lookup used by nearby search.

Nearby search filters venues by a latitude/longitude box and then joins active
courts by venue. Without these indexes both steps scan the whole table.
Both indexes are partial, so they only contain rows that can match.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_location_search_indexes"
down_revision = "0004_admin_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_venues_geo_box",
        "venues",
        ["latitude", "longitude"],
        postgresql_where=sa.text("latitude IS NOT NULL AND longitude IS NOT NULL"),
    )
    op.create_index(
        "ix_courts_venue_active",
        "courts",
        ["venue_id"],
        postgresql_where=sa.text("active"),
    )


def downgrade() -> None:
    op.drop_index("ix_courts_venue_active", table_name="courts")
    op.drop_index("ix_venues_geo_box", table_name="venues")
