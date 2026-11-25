"""VK API клиент: маппинг ошибок (rate limit → retry через outbox), messages.send."""
import json

import httpx
import pytest

from adapters.vk.api import VkApi, VkApiError, VkRateLimited


def _transport(handler) -> httpx.MockTransport:
    def _handle(request: httpx.Request) -> httpx.Response:
        # формат VK: POST form; эмулируем ответ по URL метода
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.dumps(handler(method))
        return httpx.Response(200, content=body.encode())

    return httpx.MockTransport(_handle)


async def test_send_message_success():
    def handler(method: str) -> dict[str, object]:
        assert method == "messages.send"
        return {"response": 123}

    async with httpx.AsyncClient(transport=_transport(handler)) as http:
        api = VkApi("token", http=http)
        assert await api.send_message(42, "привет") == 123


async def test_rate_limit_mapped_for_retry():
    def handler(method: str) -> dict[str, object]:
        return {"error": {"error_code": 6, "error_msg": "too many requests per second"}}

    async with httpx.AsyncClient(transport=_transport(handler)) as http:
        api = VkApi("token", http=http)
        with pytest.raises(VkRateLimited):
            await api.send_message(42, "x")


async def test_fatal_error_not_retried():
    def handler(method: str) -> dict[str, object]:
        return {"error": {"error_code": 5, "error_msg": "authorization failed"}}

    async with httpx.AsyncClient(transport=_transport(handler)) as http:
        api = VkApi("token", http=http)
        with pytest.raises(VkApiError) as exc_info:
            await api.send_message(42, "x")
        assert exc_info.value.code == 5
