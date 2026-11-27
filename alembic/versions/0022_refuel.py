"""Заправка в Мастерской: characters.last_refuel_at — кулдаун заправки."""
import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

DT = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("characters", sa.Column("last_refuel_at", DT, nullable=True))


def downgrade() -> None:
    op.drop_column("characters", "last_refuel_at")
