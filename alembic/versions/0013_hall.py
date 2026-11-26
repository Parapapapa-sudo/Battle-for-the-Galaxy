"""Залы космопорта: characters.hall (main|workshop|hq)."""
import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "characters",
        sa.Column("hall", sa.String(16), nullable=False, server_default="main"),
    )


def downgrade() -> None:
    op.drop_column("characters", "hall")
