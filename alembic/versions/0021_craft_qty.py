"""Крафт ×N: craft_jobs.qty — количество изделий в заказе."""
import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "craft_jobs", sa.Column("qty", sa.Integer(), nullable=False, server_default="1")
    )


def downgrade() -> None:
    op.drop_column("craft_jobs", "qty")
