"""Предметы: настройка бонусного дропа в экспедициях (админ-панель).

drop_weight — шанс выпадения после события экспедиции, % (0 = не выпадает);
drop_qty_min/max — диапазон количества. Дефолты (0/1/1) не меняют баланс.
"""
import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "item_templates",
        sa.Column("drop_weight", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "item_templates",
        sa.Column("drop_qty_min", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "item_templates",
        sa.Column("drop_qty_max", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("item_templates", "drop_qty_max")
    op.drop_column("item_templates", "drop_qty_min")
    op.drop_column("item_templates", "drop_weight")
