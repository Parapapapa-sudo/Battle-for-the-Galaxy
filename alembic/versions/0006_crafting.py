"""Этап 6: крафт (рецепты, заказы, таймеры) и склад."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

JSONB = pg.JSONB(astext_type=sa.Text())
DT = sa.DateTime(timezone=True)
NOW = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "recipe_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), unique=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("result_item_code", sa.String(64), nullable=False),  # → item_templates.code
        sa.Column("result_qty", sa.Integer(), nullable=False, server_default="1"),
        # [{"code": "scrap_metal", "qty": 3}]
        sa.Column("materials", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("duration_sec", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("cancel_return_pct", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("requirements", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default="published"),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )

    op.create_table(
        "craft_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("character_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "recipe_id", sa.Uuid(), sa.ForeignKey("recipe_templates.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("slot_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", DT, nullable=False, server_default=NOW),
        sa.Column("finishes_at", DT, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),  # running|done|cancelled
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )
    op.create_index("ix_craft_jobs_char", "craft_jobs", ["character_id", "status"])
    op.create_index("ix_craft_jobs_due", "craft_jobs", ["status", "finishes_at"])


def downgrade() -> None:
    for table in ("craft_jobs", "recipe_templates"):
        op.drop_table(table)
