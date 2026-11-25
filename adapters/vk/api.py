"""VK Bots API клиент (async httpx) + отправитель outbox.

Ошибки VK маппятся так, чтобы диспетчер outbox применял retry с бэкоффом:
код 6 (too many requests per second) → VkRateLimited. Остальные — VkApiError
(без ретрая: отправка бессмысленна, запись уйдёт в failed и заметит алерт).
"""
import json
import random
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models_game import AccountIdentity, Character


class VkApiError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"VK error {code}: {message}")


class VkRateLimited(VkApiError):
    def __init__(self, message: str = "too many requests") -> None:
        super().__init__(6, message)


class VkApi:
    BASE = "https://api.vk.com/method/"

    def __init__(
        self,
        token: str,
        *,
        api_version: str = "5.199",
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token
        self._version = api_version
        self._http = http or httpx.AsyncClient(timeout=30)

    async def call(self, method: str, **params: Any) -> Any:
        payload = {k: v for k, v in params.items() if v is not None}
        payload["access_token"] = self._token
        payload["v"] = self._version
        resp = await self._http.post(f"{self.BASE}{method}", data=payload)
        data = resp.json()
        if "error" in data:
            error = data["error"]
            code = int(error.get("error_code", -1))
            if code == 6:
                raise VkRateLimited(error.get("error_msg", "rate limited"))
            raise VkApiError(code, error.get("error_msg", "unknown"))
        return data["response"]

    async def get_long_poll_server(self, group_id: int) -> dict[str, Any]:
        response = await self.call("groups.getLongPollServer", group_id=group_id)
        assert isinstance(response, dict)
        return response

    async def poll(self, server: str, key: str, ts: str | int) -> dict[str, Any] | None:
        """Long poll: сервер держит соединение до событий (wait=25c)."""
        resp = await self._http.get(
            server,
            params={"act": "a_check", "key": key, "ts": ts, "wait": 25},
            timeout=35,
        )
        data = resp.json()
        if "failed" in data:  # 1=ts устарел, 2=key, 3=key истёк, 4=переполнение
            return {"failed": data["failed"], "ts": data.get("ts")}
        assert isinstance(data, dict)
        return data

    async def send_message(
        self, user_id: int, text: str, *, keyboard: dict[str, Any] | None = None
    ) -> int:
        params: dict[str, Any] = {
            "user_id": user_id,
            "message": text,
            "random_id": random.randint(-(2**31), 2**31 - 1),
        }
        if keyboard is not None:
            params["keyboard"] = json.dumps(keyboard, ensure_ascii=False)
        response = await self.call("messages.send", **params)
        return int(response)


class VkSender:
    """Отправитель outbox: персонаж → его VK-привязки → messages.send."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], api: VkApi
    ) -> None:
        self._sessions = sessions
        self._api = api

    async def send(self, *, character_id: str, text: str, platform: str | None) -> None:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    sa.select(AccountIdentity.platform_user_id)
                    .join(Character, Character.account_id == AccountIdentity.account_id)
                    .where(
                        Character.id == sa.cast(character_id, sa.Uuid),
                        AccountIdentity.platform == "vk",
                    )
                )
            ).all()
        for user_id in rows:
            await self._api.send_message(int(user_id), text)
