"""Этап 8: задания (обучение/сюжет) и фракционные события MVP."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

JSONB = pg.JSONB(astext_type=sa.Text())
DT = sa.DateTime(timezone=True)
NOW = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "quest_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), unique=True, nullable=False),
        sa.Column("faction_id", sa.Uuid(), sa.ForeignKey("factions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(16), nullable=False, server_default="tutorial"),
        # story|tutorial|side|faction_event
        sa.Column("chain_order", sa.Integer(), nullable=False, server_default="0"),
        # objectives: [{"type": "expedition_start"|"event_resolved"|"battle_won"|"crafted", "count": n, "code"?}]
        sa.Column("objectives", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        # rewards: {"items": [{"code","qty"}], "quant": n, "xp": n}
        sa.Column("rewards", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default="published"),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )

    op.create_table(
        "character_quests",
        sa.Column("character_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("quest_id", sa.Uuid(), sa.ForeignKey("quest_templates.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("progress", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("completed_at", DT, nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )

    op.create_table(
        "faction_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        # {"goal": {"type","count"}, "rewards": {"items": [...], "quant": n, "xp": n}}
        sa.Column("config", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("starts_at", DT, nullable=False),
        sa.Column("ends_at", DT, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="scheduled"),
        # scheduled|active|finished|cancelled
        sa.Column("created_by", sa.String(64), nullable=True),  # admin id или "system"
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )

    op.create_table(
        "faction_event_participants",
        sa.Column("event_id", sa.Uuid(), sa.ForeignKey("faction_events.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("character_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("contribution", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rewards_granted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    for table in (
        "faction_event_participants",
        "faction_events",
        "character_quests",
        "quest_templates",
    ):
        op.drop_table(table)
