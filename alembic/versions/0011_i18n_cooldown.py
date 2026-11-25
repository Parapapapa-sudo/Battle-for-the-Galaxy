"""i18n-инфраструктура и миграция 0011: язык игрока, кулдаун событий."""
import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "characters", sa.Column("lang", sa.String(8), nullable=False, server_default="ru")
    )
    op.add_column(
        "expeditions",
        sa.Column("next_event_at", sa.DateTime(timezone=True), nullable=True),
    )

    # сокращение задержки возвращения вдвое (ТЗ-задача от заказчика 25.11.2026)
    op.execute(
        sa.text("UPDATE config_params SET value = CAST('30' AS jsonb) WHERE key = 'expedition.return_delay_sec'")
    )
    defaults = [
        ("expedition.event_cooldown_base_sec", "10"),
        ("expedition.event_cooldown_per_2fuel_sec", "1"),
        ("vk.send_per_sec", "20"),
    ]
    for key, value in defaults:
        op.execute(
            sa.text(
                "INSERT INTO config_params (key, value) VALUES (:k, CAST(:v AS jsonb)) "
                "ON CONFLICT (key) DO NOTHING"
            ).bindparams(k=key, v=value)
        )


def downgrade() -> None:
    op.drop_column("expeditions", "next_event_at")
    op.drop_column("characters", "lang")
