"""Этап 5: модули (способности/навыки) и развитие персонажа."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

JSONB = pg.JSONB(astext_type=sa.Text())
DT = sa.DateTime(timezone=True)
NOW = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "module_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), unique=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(16), nullable=False),  # ability|skill (ТЗ)
        sa.Column("faction_id", sa.Uuid(), sa.ForeignKey("factions.id", ondelete="SET NULL"), nullable=True),
        # skill: {"attack_bonus": {"base": 2, "per_level": 1}}
        # ability: {"ability": {"combat_cmd": "shot", "name": "...", "damage_mult": 2.0, "cooldown": 3}}
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("max_per_ship", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("in_combat_restrictions", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default="published"),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )

    op.create_table(
        "module_instances",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("character_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "template_id", sa.Uuid(), sa.ForeignKey("module_templates.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("xp_invested", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("installed_slot", sa.Integer(), nullable=True),  # NULL = в инвентаре (ТЗ)
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )
    op.create_index("ix_module_instances_char", "module_instances", ["character_id"])
    op.create_index("ix_module_instances_slot", "module_instances", ["character_id", "installed_slot"])

    op.add_column(
        "characters", sa.Column("build_resets", sa.Integer(), nullable=False, server_default="0")
    )

    defaults = [
        ("module.level_thresholds", "[0, 50, 150, 300, 500]"),
        ("progression.reset_item", '"trinium_ore"'),
        ("progression.reset_base_qty", "5"),
        ("progression.reset_formula", '"linear"'),  # linear: base*(resets+1)
    ]
    for key, value in defaults:
        op.execute(
            sa.text(
                "INSERT INTO config_params (key, value) VALUES (:k, CAST(:v AS jsonb)) "
                "ON CONFLICT (key) DO NOTHING"
            ).bindparams(k=key, v=value)
        )


def downgrade() -> None:
    op.drop_column("characters", "build_resets")
    for table in ("module_instances", "module_templates"):
        op.drop_table(table)
