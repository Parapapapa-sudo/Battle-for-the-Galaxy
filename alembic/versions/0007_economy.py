"""Этап 7: экономика — кошельки/ledger, передачи, аукцион, рынок Эонов, подписки."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

JSONB = pg.JSONB(astext_type=sa.Text())
DT = sa.DateTime(timezone=True)
NOW = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "wallets",
        sa.Column("character_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),  # eon|quant (Триний — предмет, не валюта)
        sa.Column("balance", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reserved", sa.BigInteger(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("character_id", "currency", name="pk_wallets"),
    )

    op.create_table(
        "wallet_ledger",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("character_id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("delta", sa.BigInteger(), nullable=False),
        sa.Column("balance_after", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("ref_type", sa.String(32), nullable=True),
        sa.Column("ref_id", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False, server_default="system"),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
        sa.UniqueConstraint("idempotency_key", name="uq_ledger_idem"),
    )
    op.create_index("ix_ledger_char", "wallet_ledger", ["character_id", "currency", "id"])
    op.create_index("ix_ledger_ref", "wallet_ledger", ["ref_type", "ref_id"])

    op.create_table(
        "transfers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("from_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        # {"quant": n} и/или {"items": [{"code","qty"}]}
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending_confirmation"),
        # pending_confirmation|active|completed|expired|rejected|cancelled
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
        sa.Column("expires_at", DT, nullable=True),
        sa.Column("confirmed_at", DT, nullable=True),
        sa.Column("completed_at", DT, nullable=True),
    )
    op.create_index("ix_transfers_to", "transfers", ["to_id", "status"])
    op.create_index("ix_transfers_expiry", "transfers", ["status", "expires_at"])

    op.create_table(
        "auction_lots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("seller_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_code", sa.String(64), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("min_price_quant", sa.BigInteger(), nullable=False),
        sa.Column("buyout_quant", sa.BigInteger(), nullable=True),
        sa.Column("top_bid", sa.BigInteger(), nullable=True),
        sa.Column("top_bidder_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        # active|sold|expired|cancelled_pending_return
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
        sa.Column("expires_at", DT, nullable=False),
    )
    op.create_index("ix_lots_active", "auction_lots", ["status", "expires_at"])

    op.create_table(
        "auction_bids",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("lot_id", sa.Uuid(), sa.ForeignKey("auction_lots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bidder_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )

    op.create_table(
        "eon_market_orders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("character_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),  # buy|sell
        sa.Column("amount_eon", sa.BigInteger(), nullable=False),
        sa.Column("filled_eon", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("price_quant", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        # open|partial|matched|cancelled
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )
    op.create_index("ix_eon_orders", "eon_market_orders", ["status", "side", "price_quant"])

    op.create_table(
        "shop_offers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), unique=True, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),  # pack|subscription
        sa.Column("price_eon", sa.BigInteger(), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default="published"),
    )

    op.create_table(
        "purchases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("character_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("offer_id", sa.Uuid(), sa.ForeignKey("shop_offers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False, server_default="mock"),
        sa.Column("provider_payment_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        # pending|paid|refunded|failed
        sa.Column("paid_at", DT, nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
        sa.UniqueConstraint("provider", "provider_payment_id", name="uq_payment"),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("character_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),  # sub_7|sub_30
        sa.Column("started_at", DT, nullable=False, server_default=NOW),
        sa.Column("expires_at", DT, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("source_purchase_id", sa.Uuid(), sa.ForeignKey("purchases.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )
    op.create_index("ix_subscriptions_char", "subscriptions", ["character_id", "status"])

    defaults = [
        ("transfer.commission_pct", "0"),
        ("transfer.max_quant", "100000"),
        ("transfer.confirm_threshold_quant", "5000"),
        ("transfer.offer_ttl_sec", "86400"),
        ("auction.commission_pct", "1"),
        ("auction.max_lots", "5"),
        ("auction.max_lots_subscriber", "10"),
        ("auction.duration_sec", "86400"),
        ("auction.cancel_return_sec", "1200"),
        ("market.reference_price_quant", "1000"),
        ("market.max_deviation_pct", "50"),
        ("market.order_min_eon", "1"),
        ("market.order_max_eon", "1000"),
        ("subscription.inventory_bonus_pct", "50"),
        ("subscription.craft_slots_bonus", "1"),
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
        "subscriptions",
        "purchases",
        "shop_offers",
        "eon_market_orders",
        "auction_bids",
        "auction_lots",
        "transfers",
        "wallet_ledger",
        "wallets",
    ):
        op.drop_table(table)
