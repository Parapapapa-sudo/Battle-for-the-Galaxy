"""Квесты: принятие и отслеживание. characters.tracked_quest — код отслеживаемого.

CharacterQuest.status пополняется значением 'available' (предложено, ждёт
принятия) — схема не меняется, только семантика.
"""
import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("characters", sa.Column("tracked_quest", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("characters", "tracked_quest")
