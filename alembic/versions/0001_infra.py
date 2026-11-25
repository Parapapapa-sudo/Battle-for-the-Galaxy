"""Этап 0: инфраструктурные таблицы.

Идемпотентность команд, outbox сообщений, конфигурация баланса, формулы,
планировщик таймеров, аудит, режим обслуживания.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

JSONB = pg.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("character_id", sa.Uuid(), nullable=True),
        sa.Column("scope", sa.String(32), nullable=False, server_default="command"),
        sa.Column("request_hash", sa.String(64), nullable=True),
        sa.Column("response", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("character_id", sa.Uuid(), nullable=False),
        sa.Column("platform", sa.String(16), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_outbox_pending", "outbox", ["status", "next_attempt_at"])

    op.create_table(
        "config_params",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("schema", JSONB, nullable=True),
        sa.Column("hot_reloadable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "formulas",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("params", JSONB, nullable=True),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "timers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("character_id", sa.Uuid(), nullable=True),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("fires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_timers_due", "timers", ["status", "fires_at"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("before", JSONB, nullable=True),
        sa.Column("after", JSONB, nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_target", "audit_log", ["target_type", "target_id"])
    op.create_index("ix_audit_at", "audit_log", ["at"])

    op.create_table(
        "maintenance_state",
        sa.Column("id", sa.SmallInteger(), primary_key=True, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("message", sa.Text(), nullable=False, server_default="Технические работы."),
        sa.CheckConstraint("id = 1", name="ck_maintenance_single_row"),
    )


def downgrade() -> None:
    for table in (
        "maintenance_state",
        "audit_log",
        "timers",
        "formulas",
        "config_params",
        "outbox",
        "idempotency_keys",
    ):
        op.drop_table(table)
