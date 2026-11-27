"""Фракции и репутация: kind (main|neutral), фракция и репутация у врагов,
нейтральные фракции, мульти-репутация (индекс «одна активная» удалён).
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

NEUTRAL_FACTIONS = [
    ("pirates", "Пираты"),
    ("helios", "Гелиос"),
    ("dark_cartel", "Тёмный картель"),
    ("veil_keepers", "Хранители завесы"),
    ("red_syndicate", "Красный синдикат"),
]

ENEMY_REP = {
    "pirate_scout": -25,
    "pirate_raider": -50,
    "pirate_captain": -100,
}


def upgrade() -> None:
    op.add_column(
        "factions",
        sa.Column("kind", sa.String(16), nullable=False, server_default="main"),
    )
    op.add_column(
        "enemy_templates",
        sa.Column("faction_id", sa.Uuid(), sa.ForeignKey("factions.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "enemy_templates",
        sa.Column("reputation", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    # репутация теперь со ВСЕМИ фракциями одновременно — «одна активная» не нужна
    op.drop_index("uq_reputation_active", table_name="reputations")

    for code, name in NEUTRAL_FACTIONS:
        op.execute(
            sa.text(
                "INSERT INTO factions (id, code, name, description, kind, bonuses, start_resources) "
                "VALUES (gen_random_uuid(), :code, :name, '', 'neutral', '{}'::jsonb, '{}'::jsonb) "
                "ON CONFLICT (code) DO NOTHING"
            ).bindparams(code=code, name=name)
        )
    for enemy_code, delta in ENEMY_REP.items():
        op.execute(
            sa.text(
                "UPDATE enemy_templates SET "
                "faction_id = (SELECT id FROM factions WHERE code = 'pirates'), "
                "reputation = jsonb_build_object('pirates', CAST(:delta AS int)) "
                "WHERE code = :code"
            ).bindparams(delta=delta, code=enemy_code)
        )

    for key, value in (
        ("reputation.hatred_below", "-10000"),
        ("reputation.friendly_from", "10000"),
        ("reputation.exalted_from", "50000"),
        ("faction.join_penalty", "-1000"),
    ):
        op.execute(
            sa.text(
                "INSERT INTO config_params (key, value) VALUES (:k, CAST(:v AS jsonb)) "
                "ON CONFLICT (key) DO NOTHING"
            ).bindparams(k=key, v=value)
        )


def downgrade() -> None:
    op.create_index(
        "uq_reputation_active",
        "reputations",
        ["character_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.drop_column("enemy_templates", "reputation")
    op.drop_column("enemy_templates", "faction_id")
    op.drop_column("factions", "kind")
