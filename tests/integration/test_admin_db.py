"""Этап 9: админ-панель — RBAC, аудит, модерация, конфигурация, контент, аналитика."""
import uuid

import pytest
import sqlalchemy as sa

from app import admin, characters
from app.models import AuditLog
from app.models_game import AdminUser, Character, CharacterEffect
from core.errors import DomainError
from tests.conftest import requires_db

OWNER_KEY = "dev-only-owner-key"


async def _admins(session) -> tuple[AdminUser, AdminUser, AdminUser]:
    """owner (admin), moderator, content_manager."""
    mod = AdminUser(login="mod1", api_key_hash=admin.hash_key("mod-key"), role="moderator")
    cm = AdminUser(
        login="cm1", api_key_hash=admin.hash_key("cm-key"), role="content_manager"
    )
    session.add_all([mod, cm])
    await session.flush()
    owner = await session.scalar(sa.select(AdminUser).where(AdminUser.login == "owner"))
    assert owner is not None
    return owner, mod, cm


async def _character(session, seeded) -> Character:
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=f"user-{uuid.uuid4().hex[:12]}"
    )
    return await characters.create_character(
        session, account_id=account.id, name=f"Тестер{uuid.uuid4().hex[:6]}", faction_code="azimuth"
    )


@requires_db
async def test_authenticate_and_role_matrix(session, seeded):
    owner, mod, cm = await _admins(session)
    assert (await admin.authenticate(session, OWNER_KEY)).login == "owner"
    with pytest.raises(DomainError):
        await admin.authenticate(session, "wrong-key")

    character = await _character(session, seeded)
    # модератор: бан и эффекты — можно; конфиги — нельзя
    await admin.ban_account(session, mod, character.account_id, reason="тест")
    await admin.unban_account(session, mod, character.account_id)
    await admin.apply_effect(session, mod, character.id, "xp_boost", params={"power": 2})
    with pytest.raises(DomainError) as exc_info:
        await admin.set_config(session, mod, "inventory.base_size", 40)
    assert exc_info.value.code == "forbidden"

    # контент-менеджер: публикация в своей зоне — можно; бан — нельзя
    await admin.set_content_status(session, cm, "event_templates", "quiet_drift", "draft")
    with pytest.raises(DomainError) as exc_info:
        await admin.ban_account(session, cm, character.account_id, reason="x")
    assert exc_info.value.code == "forbidden"
    with pytest.raises(DomainError) as exc_info:
        await admin.set_config(session, cm, "inventory.base_size", 40)
    assert exc_info.value.code == "forbidden"


@requires_db
async def test_audit_log_written(session, seeded):
    owner, mod, _ = await _admins(session)
    character = await _character(session, seeded)
    await admin.ban_account(session, mod, character.account_id, reason="нарушение")
    await admin.set_config(session, owner, "inventory.base_size", 35)

    rows = (
        await session.scalars(
            sa.select(AuditLog).where(AuditLog.actor_id.in_(["mod1", "owner"]))
        )
    ).all()
    actions = {r.action for r in rows}
    assert "ban_account" in actions
    assert "set_config" in actions
    ban = next(r for r in rows if r.action == "ban_account")
    assert ban.actor_role == "moderator"
    assert ban.before is not None and ban.after is not None  # diff (ТЗ: аудит)


@requires_db
async def test_ban_blocks_game_commands(session, seeded):
    _, mod, _ = await _admins(session)
    character = await _character(session, seeded)
    await admin.ban_account(session, mod, character.account_id, reason="тест")
    with pytest.raises(DomainError) as exc_info:
        await characters.ensure_account_active(session, character.account_id)
    assert exc_info.value.code == "banned"


@requires_db
async def test_config_hot_reload_applies(session, seeded):
    """ТЗ: баланс правится без кода и перезапуска — игра читает новое значение."""
    owner, _, _ = await _admins(session)
    await admin.set_config(session, owner, "inventory.base_size", 35)
    character = await _character(session, seeded)  # создаётся ПОСЛЕ правки
    assert character.inventory_size == 35


@requires_db
async def test_moderator_cannot_create_effects(session, seeded):
    """ТЗ: модератор применяет только существующие эффекты."""
    _, mod, _ = await _admins(session)
    character = await _character(session, seeded)
    with pytest.raises(DomainError) as exc_info:
        await admin.apply_effect(session, mod, character.id, "nonexistent_effect")
    assert exc_info.value.code == "no_effect"
    await admin.apply_effect(session, mod, character.id, "xp_boost")
    effects = (
        await session.scalars(
            sa.select(CharacterEffect).where(CharacterEffect.character_id == character.id)
        )
    ).all()
    assert len(effects) == 1


@requires_db
async def test_analytics_mvp(session, seeded):
    owner, _, _ = await _admins(session)
    await _character(session, seeded)
    data = await admin.analytics(session, owner)
    assert data["total_characters"] >= 1
    assert "tutorial_funnel" in data
