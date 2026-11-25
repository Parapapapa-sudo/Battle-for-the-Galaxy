"""Этап 9: админ-панель — роли, аудит, модерация, эффекты."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

JSONB = pg.JSONB(astext_type=sa.Text())
DT = sa.DateTime(timezone=True)
NOW = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("login", sa.String(64), unique=True, nullable=False),
        sa.Column("api_key_hash", sa.String(128), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),  # admin|moderator|content_manager
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )

    # Белый список эффектов для модераторов (ТЗ: модератор не создаёт эффекты)
    op.create_table(
        "effect_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), unique=True, nullable=False),
        sa.Column("scope", sa.String(16), nullable=False, server_default="character"),
        sa.Column("params_schema", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("duration_default_sec", sa.Integer(), nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )

    op.create_table(
        "character_effects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("character_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("effect_template_id", sa.Uuid(), sa.ForeignKey("effect_templates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False, server_default="moderator"),
        sa.Column("source_id", sa.String(64), nullable=True),
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("applied_at", DT, nullable=False, server_default=NOW),
        sa.Column("expires_at", DT, nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )
    op.create_index("ix_character_effects_char", "character_effects", ["character_id"])

    # Дефолтный владелец (сменить ключ через ADMIN_API_KEY до прода; hash = sha256(key))
    op.execute(
        sa.text(
            "INSERT INTO admin_users (id, login, api_key_hash, role, created_by) VALUES "
            "(gen_random_uuid(), 'owner', :k, 'admin', 'system') "
            "ON CONFLICT (login) DO NOTHING"
        ).bindparams(k="15f8e21eeaa853a3fd006607434e6aeb91e795806236cb2af8c8ee3a874dc0e7")
    )
    op.execute(
        sa.text(
            "INSERT INTO effect_templates (id, code, scope, params_schema, duration_default_sec) VALUES "
            "(gen_random_uuid(), 'xp_boost', 'character', "
            "'{\"power\": \"number\"}'::jsonb, 3600), "
            "(gen_random_uuid(), 'trade_restrict', 'character', '{}'::jsonb, 86400) "
            "ON CONFLICT (code) DO NOTHING"
        )
    )


def downgrade() -> None:
    for table in ("character_effects", "effect_templates", "admin_users"):
        op.drop_table(table)
