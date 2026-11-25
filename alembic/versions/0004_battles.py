"""Этап 4: боевая система — враги, способности, таблицы лута, бои, журнал ходов."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

JSONB = pg.JSONB(astext_type=sa.Text())
DT = sa.DateTime(timezone=True)
NOW = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "enemy_abilities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), unique=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        # {"damage_mult": 1.6, "cooldown": 3, "chance": 0.4}
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )

    op.create_table(
        "loot_tables",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), unique=True, nullable=False),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )

    op.create_table(
        "loot_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "table_id", sa.Uuid(), sa.ForeignKey("loot_tables.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "item_template_id",
            sa.Uuid(),
            sa.ForeignKey("item_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("qty_min", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("qty_max", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("chance", sa.Numeric(4, 3), nullable=False, server_default="1"),
    )
    op.create_index("ix_loot_entries_table", "loot_entries", ["table_id"])

    op.create_table(
        "enemy_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), unique=True, nullable=False),
        sa.Column(
            "category", sa.String(16), nullable=False
        ),  # normal|dangerous|elite|boss (характеристики одинаковы для всех фракций — ТЗ)
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("stats", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        # {"hull","damage","xp"}
        sa.Column("abilities", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        # [коды enemy_abilities]; пассивы/слабости — только elite/boss (ТЗ)
        sa.Column("passives", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("weaknesses", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "loot_table_id", sa.Uuid(), sa.ForeignKey("loot_tables.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("behavior", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default="published"),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )

    op.create_table(
        "battles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("character_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "expedition_id", sa.Uuid(), sa.ForeignKey("expeditions.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "enemy_template_id",
            sa.Uuid(),
            sa.ForeignKey("enemy_templates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("enemy_snapshot", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("player_snapshot", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("state", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        # {"enemy_hull","turn_no","enemy_ability_cooldowns":{code:turn},"consumables_used_turn":int}
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        # active|won|lost|retreated
        sa.Column("retreat_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
        sa.Column("updated_at", DT, nullable=False, server_default=NOW),
    )
    # один активный бой на персонажа
    op.create_index(
        "uq_battle_active",
        "battles",
        ["character_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "battle_turns",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("battle_id", sa.Uuid(), sa.ForeignKey("battles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("turn_no", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(16), nullable=False),  # player|enemy
        sa.Column("action", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("log", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )
    op.create_index("ix_battle_turns_battle", "battle_turns", ["battle_id", "turn_no"])

    # params предметов: {"heal": 15} у расходников и т.п.
    op.add_column(
        "item_templates",
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )

    defaults = [
        ("combat.damage_spread_pct", "10"),
        ("combat.retreat_chance_pct", "60"),
        ("combat.retreat_damage_chance_pct", "30"),
        ("combat.retreat_damage_amount", "10"),
        ("combat.retreat_malfunction_chance_pct", "10"),
        ("combat.consumables_per_turn", "2"),
        ("combat.allowed_consumables", '["nano_patch"]'),
        ("combat.chance_low_max", "35"),
        ("combat.chance_high_min", "65"),
    ]
    for key, value in defaults:
        op.execute(
            sa.text(
                "INSERT INTO config_params (key, value) VALUES (:k, CAST(:v AS jsonb)) "
                "ON CONFLICT (key) DO NOTHING"
            ).bindparams(k=key, v=value)
        )


def downgrade() -> None:
    for table in ("battle_turns", "battles", "enemy_templates", "loot_entries", "loot_tables", "enemy_abilities"):
        op.drop_table(table)
