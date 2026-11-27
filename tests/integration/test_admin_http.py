"""Этап 9: HTTP — Admin API (RBAC + аудит) и Mini App API (dev-auth).

HTTP-эндпоинты используют db.session_factory с настоящими коммитами. Фикстура
`http_seeded` засевает контент коммитом (эндпоинты его не увидят иначе) и
подчищает за собой TRUNCATE'ом — иначе закоммиченные сиды ломают тесты,
считающие «первичность» сидов, а незакоммиченные фикстурные транзакции
блокируют вставки тех же ключей (deadlock по уникальному индексу).
"""
import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.db import session_factory
from app.models_game import Account, Character, Faction
from tests.conftest import requires_db

OWNER_KEY = "dev-only-owner-key"

CONTENT_TABLES = [
    "shop_offers", "quest_templates", "recipe_templates", "module_templates",
    "loot_entries", "loot_tables", "enemy_abilities", "enemy_templates",
    "event_table_entries", "event_tables", "event_templates",
    "ship_templates", "factions", "item_templates",
]


@pytest.fixture
async def http_seeded() -> AsyncIterator[None]:
    from app.seeds import ensure_seeds

    async with session_factory() as s, s.begin():
        await ensure_seeds(s)
    try:
        yield
    finally:
        async with session_factory() as s, s.begin():
            await s.execute(sa.text(f"TRUNCATE TABLE {', '.join(CONTENT_TABLES)} CASCADE"))


async def _client() -> AsyncClient:
    from adapters.http.app_factory import create_app_from_settings

    return AsyncClient(
        transport=ASGITransport(app=create_app_from_settings()), base_url="http://t"
    )


@requires_db
async def test_admin_api_auth_and_config(http_seeded):
    async with await _client() as client:
        r = await client.get("/admin/config/inventory.base_size")
        assert r.status_code == 401

        r = await client.get(
            "/admin/config/inventory.base_size", headers={"X-Admin-Key": OWNER_KEY}
        )
        assert r.status_code == 200
        assert int(r.json()["value"]) == 30

        r = await client.put(
            "/admin/config/inventory.base_size",
            json={"value": 33},
            headers={"X-Admin-Key": OWNER_KEY},
        )
        assert r.status_code == 200
        try:
            r = await client.get(
                "/admin/config/inventory.base_size", headers={"X-Admin-Key": OWNER_KEY}
            )
            assert int(r.json()["value"]) == 33
        finally:
            await client.put(
                "/admin/config/inventory.base_size",
                json={"value": 30},
                headers={"X-Admin-Key": OWNER_KEY},
            )


@requires_db
async def test_admin_api_content_publish(http_seeded):
    async with await _client() as client:
        r = await client.post(
            "/admin/content/event_templates/quiet_drift/status",
            json={"status": "draft"},
            headers={"X-Admin-Key": OWNER_KEY},
        )
        assert r.status_code == 200
        r = await client.post(
            "/admin/content/event_templates/quiet_drift/status",
            json={"status": "published"},
            headers={"X-Admin-Key": OWNER_KEY},
        )
        assert r.status_code == 200


@requires_db
async def test_miniapp_vk_launch_params_auto_account(http_seeded):
    """Мини-апп: валидная подпись VK → аккаунт создаётся сам; без персонажа — 404 с подсказкой."""
    import base64
    import hashlib
    import hmac
    from urllib.parse import urlencode

    from app.config import get_settings

    secret = get_settings().vk_app_secret
    if not secret:
        pytest.skip("VK_APP_SECRET не задан")
    vk_user = f"999{uuid.uuid4().int % 10**9}"
    params = {"vk_app_id": "512000", "vk_ts": "1700000000", "vk_user_id": vk_user}
    data = urlencode(sorted(params.items()))
    sign = (
        base64.urlsafe_b64encode(
            hmac.new(secret.encode(), data.encode(), hashlib.sha256).digest()
        )
        .decode()
        .rstrip("=")
    )
    query = urlencode(params) + "&sign=" + sign
    try:
        async with await _client() as client:
            r = await client.get("/api/me", headers={"X-VK-Launch-Params": query})
            assert r.status_code == 404
            assert "Персонаж ещё не создан" in r.json()["detail"]
        # аккаунт с привязкой VK создан автоматически (не «аккаунт не привязан»)
        async with session_factory() as s:
            from app.models_game import AccountIdentity

            identity = await s.scalar(
                sa.select(AccountIdentity).where(
                    AccountIdentity.platform == "vk",
                    AccountIdentity.platform_user_id == vk_user,
                )
            )
            assert identity is not None
    finally:
        async with session_factory() as s, s.begin():
            await s.execute(
                sa.delete(Account).where(
                    Account.id.in_(
                        sa.select(AccountIdentity.account_id).where(
                            AccountIdentity.platform_user_id == vk_user
                        )
                    )
                )
            )


@requires_db
async def test_miniapp_api_me(http_seeded):
    char_id = uuid.uuid4()
    async with session_factory() as s, s.begin():
        account = Account()
        s.add(account)
        await s.flush()
        faction_id = await s.scalar(
            sa.select(Faction.id).where(Faction.code == "azimuth")
        )
        s.add(
            Character(
                id=char_id,
                account_id=account.id,
                name=f"API{uuid.uuid4().hex[:6]}",
                name_normalized=f"api{uuid.uuid4().hex[:6]}",
                faction_id=faction_id,
            )
        )
    try:
        async with await _client() as client:
            r = await client.get("/api/me", headers={"X-Character-Id": str(char_id)})
            assert r.status_code == 200
            assert r.json()["ship"] is None  # персонаж создан без стартового набора

            r = await client.get("/api/me")
            assert r.status_code == 401
    finally:
        async with session_factory() as s, s.begin():
            await s.execute(sa.delete(Character).where(Character.id == char_id))
            await s.execute(
                sa.delete(Account).where(
                    Account.id.not_in(sa.select(Character.account_id))
                )
            )
