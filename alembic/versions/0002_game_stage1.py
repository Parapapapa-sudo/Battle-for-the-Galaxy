"""Этап 1: аккаунты, привязки платформ, фракции, персонажи, репутация,
корабли, инвентарь, журнал модерации имён + стартовые config_params."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

JSONB = pg.JSONB(astext_type=sa.Text())
DT = sa.DateTime(timezone=True)
NOW = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("ban_until", DT, nullable=True),
        sa.Column("ban_reason", sa.Text(), nullable=True),
        sa.Column("banned_by", sa.String(64), nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )

    op.create_table(
        "account_identities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id", sa.Uuid(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("platform_user_id", sa.String(64), nullable=False),
        sa.Column("linked_at", DT, nullable=False, server_default=NOW),
        sa.Column("verified_at", DT, nullable=True),
        sa.Column("link_token_hash", sa.String(64), nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
        sa.UniqueConstraint("platform", "platform_user_id", name="uq_identity"),
    )
    op.create_index("ix_identities_account", "account_identities", ["account_id"])

    op.create_table(
        "factions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(32), unique=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("bonuses", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("start_resources", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )

    op.create_table(
        "characters",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id", sa.Uuid(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("name_normalized", sa.String(64), nullable=False),
        sa.Column(
            "faction_id",
            sa.Uuid(),
            sa.ForeignKey("factions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("tutorial_state", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("craft_slots", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("inventory_size", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("warehouse_size", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("faction_changed_at", DT, nullable=True),
        sa.Column("xp_pool", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )
    op.create_index(
        "ix_characters_account_active",
        "characters",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_characters_name_norm", "characters", ["name_normalized"])

    op.create_table(
        "reputations",
        sa.Column(
            "character_id",
            sa.Uuid(),
            sa.ForeignKey("characters.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "faction_id", sa.Uuid(), sa.ForeignKey("factions.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("value", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", DT, nullable=False, server_default=NOW),
    )
    op.create_index(
        "uq_reputation_active",
        "reputations",
        ["character_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "character_titles",
        sa.Column(
            "character_id",
            sa.Uuid(),
            sa.ForeignKey("characters.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("title_code", sa.String(64), primary_key=True),
        sa.Column("granted_at", DT, nullable=False, server_default=NOW),
        sa.Column("granted_by", sa.String(64), nullable=True),
    )

    op.create_table(
        "ship_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), unique=True, nullable=False),
        sa.Column(
            "faction_id",
            sa.Uuid(),
            sa.ForeignKey("factions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("base_stats", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("module_slots", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )

    op.create_table(
        "ships",
        sa.Column(
            "character_id",
            sa.Uuid(),
            sa.ForeignKey("characters.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "template_id",
            sa.Uuid(),
            sa.ForeignKey("ship_templates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("base_max_hull", sa.Integer(), nullable=False),
        sa.Column("current_hull", sa.Integer(), nullable=False),
        sa.Column("malfunctions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fuel", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="dock"),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )
    op.create_index("ix_ships_status", "ships", ["status"])

    op.create_table(
        "item_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), unique=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.String(32), nullable=False, server_default="resource"),
        sa.Column("rarity", sa.String(16), nullable=False, server_default="common"),
        sa.Column("stack_limit", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_unique", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sellable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("tradable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("losable_on_defeat", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )

    op.create_table(
        "inventories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "character_id",
            sa.Uuid(),
            sa.ForeignKey("characters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("size_limit", sa.Integer(), nullable=True),  # NULL = безлимит (waiting)
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
        sa.UniqueConstraint("character_id", "kind", name="uq_inventory_kind"),
    )

    op.create_table(
        "inventory_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "inventory_id",
            sa.Uuid(),
            sa.ForeignKey("inventories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_template_id",
            sa.Uuid(),
            sa.ForeignKey("item_templates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("qty", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("module_instance_id", sa.Uuid(), nullable=True),
        sa.Column("acquired_at", DT, nullable=False, server_default=NOW),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )
    op.create_index("ix_inv_items_inventory", "inventory_items", ["inventory_id"])

    op.create_table(
        "moderation_names",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "character_id",
            sa.Uuid(),
            sa.ForeignKey("characters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("rule_hit", sa.String(32), nullable=True),
        sa.Column("moderator_id", sa.String(64), nullable=True),
        sa.Column("at", DT, nullable=False, server_default=NOW),
    )

    # Стартовые config_params (ТЗ открытые числа — здесь, а не в коде).
    defaults = [
        ("inventory.base_size", "30"),
        ("warehouse.base_size", "20"),
        ("names.min_length", "3"),
        ("names.max_length", "24"),
        ("names.forbidden_patterns", '["\\\\[admin\\\\]", "(?i)админ", "(?i)admin"]'),
        ("reputation.start_value", "0"),
    ]
    for key, value in defaults:
        op.execute(
            sa.text(
                "INSERT INTO config_params (key, value) VALUES (:k, CAST(:v AS jsonb)) "
                "ON CONFLICT (key) DO NOTHING"
            ).bindparams(k=key, v=value)
        )


def downgrade() -> None:
    for table in (
        "moderation_names",
        "inventory_items",
        "inventories",
        "item_templates",
        "ships",
        "ship_templates",
        "character_titles",
        "reputations",
        "characters",
        "factions",
        "account_identities",
        "accounts",
    ):
        op.drop_table(table)
