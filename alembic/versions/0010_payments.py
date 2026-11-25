"""Платёжный контур (под реального провайдера): заказы, webhook, возвраты, журнал.

Курс: 1 Эон = 1 рубль (config `eon.rub_rate`), сумма и количество Эонов
фиксируются при создании заказа и не меняются.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

JSONB = pg.JSONB(astext_type=sa.Text())
DT = sa.DateTime(timezone=True)
NOW = sa.func.now()


def upgrade() -> None:
    op.add_column("shop_offers", sa.Column("price_rub", sa.BigInteger(), nullable=True))

    op.create_table(
        "payment_orders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("character_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("offer_id", sa.Uuid(), sa.ForeignKey("shop_offers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        # ID заказа у провайдера; UNIQUE(provider, provider_order_id) — защита от
        # повторного начисления на уровне БД (ТЗ).
        sa.Column("provider_order_id", sa.String(128), nullable=False),
        # Фиксация на момент создания заказа:
        sa.Column("amount_rub", sa.BigInteger(), nullable=False),
        sa.Column("eons", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="created"),
        # created|pending|paid|failed|cancelled|refunded|refund_pending_balance
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("pay_url", sa.Text(), nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
        sa.Column("paid_at", DT, nullable=True),
        sa.Column("cancelled_at", DT, nullable=True),
        sa.Column("refunded_at", DT, nullable=True),
        sa.UniqueConstraint("provider", "provider_order_id", name="uq_provider_order"),
        sa.UniqueConstraint("idempotency_key", name="uq_payment_idem"),
    )
    op.create_index("ix_payment_orders_char", "payment_orders", ["character_id", "created_at"])

    # Журнал транзакций: append-only, каждый переход состояния и движение средств.
    op.create_table(
        "payment_transactions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("payment_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        # order_created|payment_pending|payment_confirmed|eons_granted|
        # cancel_initiated|cancel_confirmed|refund_initiated|refund_confirmed|
        # refund_pending_balance|eons_clawed_back
        sa.Column("amount_rub", sa.BigInteger(), nullable=True),
        sa.Column("eons", sa.BigInteger(), nullable=True),
        sa.Column("actor", sa.String(32), nullable=False, server_default="system"),
        # user|provider|admin|system
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("created_at", DT, nullable=False, server_default=NOW),
    )
    op.create_index("ix_payment_tx_order", "payment_transactions", ["order_id", "id"])

    op.execute(
        sa.text(
            "INSERT INTO config_params (key, value) VALUES ('eon.rub_rate', CAST('1' AS jsonb)) "
            "ON CONFLICT (key) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.drop_table("payment_transactions")
    op.drop_table("payment_orders")
    op.drop_column("shop_offers", "price_rub")
