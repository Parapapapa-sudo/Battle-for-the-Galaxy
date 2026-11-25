"""Этап 3: экспедиции и события (космопорт, топливо, возвращение)."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

JSONB = pg.JSONB(astext_type=sa.Text())
DT = sa.DateTime(timezone=True)
NOW = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "event_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), unique=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(16), nullable=False),  # interactive|automatic
        sa.Column("fuel_cost", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("danger_min", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("danger_max", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("conditions", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        # interactive: {"choices": [{"key","text","outcome"|"variants"}]}
        # automatic:   {"variants": [{"chance"?,"message","grants","hull_damage"}]}
        # outcome: {"message", "grants": [{"code","qty"}], "hull_damage": int}
        sa.Column("script", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_unique_per_expedition", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("can_refuse", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(16), nullable=False, server_default="published"),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )

    op.create_table(
        "event_tables",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), unique=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )

    op.create_table(
        "event_table_entries",
        sa.Column(
            "table_id",
            sa.Uuid(),
            sa.ForeignKey("event_tables.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "event_template_id",
            sa.Uuid(),
            sa.ForeignKey("event_templates.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("conditions", JSONB, nullable=True),
    )

    op.create_table(
        "expeditions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "character_id",
            sa.Uuid(),
            sa.ForeignKey("characters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        # active|returning|completed|aborted_defeat|aborted_fuel
        sa.Column("started_at", DT, nullable=False, server_default=NOW),
        sa.Column("fuel_at_start", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fuel_remaining", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_queue", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("queue_pos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "return_reason", sa.String(16), nullable=True
        ),  # voluntary|fuel_empty|defeat (NULL = не возвращается)
        sa.Column("return_eta", DT, nullable=True),
        sa.Column("arrived_at", DT, nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )
    # одна незавершённая экспедиция на персонажа
    op.create_index(
        "uq_expedition_ongoing",
        "expeditions",
        ["character_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('active', 'returning')"),
    )

    op.create_table(
        "expedition_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "expedition_id",
            sa.Uuid(),
            sa.ForeignKey("expeditions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_template_id",
            sa.Uuid(),
            sa.ForeignKey("event_templates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="offered"),
        # offered|resolved|declined; завершённое закрыто окончательно (ТЗ)
        sa.Column("snapshot", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("resolution", JSONB, nullable=True),
        sa.Column("resolved_at", DT, nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
        sa.UniqueConstraint("expedition_id", "seq", name="uq_exp_event_seq"),
    )
    op.create_index("ix_exp_events_expedition", "expedition_events", ["expedition_id", "seq"])

    defaults = [
        ("expedition.queue_length", "10"),
        ("expedition.return_delay_sec", "60"),
        ("fuel_empty.malfunctions", "1"),
        ("defeat.malfunctions", "1"),
        ("defeat.loss_min_pct", "10"),
        ("defeat.loss_max_pct", "30"),
        ("danger.low_max", "3"),
        ("danger.high_min", "7"),
    ]
    for key, value in defaults:
        op.execute(
            sa.text(
                "INSERT INTO config_params (key, value) VALUES (:k, CAST(:v AS jsonb)) "
                "ON CONFLICT (key) DO NOTHING"
            ).bindparams(k=key, v=value)
        )


def downgrade() -> None:
    for table in ("expedition_events", "expeditions", "event_table_entries", "event_tables", "event_templates"):
        op.drop_table(table)
