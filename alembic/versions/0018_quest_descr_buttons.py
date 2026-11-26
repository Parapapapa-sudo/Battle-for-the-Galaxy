"""Описания обучающих заданий: русские названия кнопок вместо команд."""
import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

DESCRIPTIONS = {
    "tutorial_1": "Отправьтесь в экспедицию: нажмите «Экспедиция», затем «Исследовать».",
    "tutorial_2": (
        "Разберитесь с событиями экспедиции: выбирайте варианты на кнопках, "
        "«Исследовать» — двигаться дальше."
    ),
    "tutorial_3": "Победите в пошаговом бою: кнопка «Атака», при необходимости — «Отступить».",
    "story_1": "Создайте предмет в космопорту: Мастерская → «Крафт».",
}


def upgrade() -> None:
    for code, description in DESCRIPTIONS.items():
        op.execute(
            sa.text("UPDATE quest_templates SET description = :descr WHERE code = :code")
            .bindparams(descr=description, code=code)
        )


def downgrade() -> None:
    pass  # формулировки — контент, откат не требуется
