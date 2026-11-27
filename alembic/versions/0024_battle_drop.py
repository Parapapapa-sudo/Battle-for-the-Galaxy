"""Боевой дроп предметов: item_templates.battle_drop_weight (% при победе)."""
import sqlalchemy as sa

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "item_templates",
        sa.Column("battle_drop_weight", sa.Integer(), nullable=False, server_default="0"),
    )
    for key, value in (("combat.victory_quant_min", "15"), ("combat.victory_quant_max", "25")):
        op.execute(
            sa.text(
                "INSERT INTO config_params (key, value) VALUES (:k, CAST(:v AS jsonb)) "
                "ON CONFLICT (key) DO NOTHING"
            ).bindparams(k=key, v=value)
        )


def downgrade() -> None:
    op.drop_column("item_templates", "battle_drop_weight")
