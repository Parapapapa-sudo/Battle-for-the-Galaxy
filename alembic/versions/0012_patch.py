"""Ремонт в экспедиции: patch_eta у экспедиции + config ship.patch_duration_sec."""
import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

DT = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("expeditions", sa.Column("patch_eta", DT, nullable=True))
    op.execute(
        sa.text(
            "INSERT INTO config_params (key, value) VALUES ('ship.patch_duration_sec', CAST('20' AS jsonb)) "
            "ON CONFLICT (key) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.drop_column("expeditions", "patch_eta")
