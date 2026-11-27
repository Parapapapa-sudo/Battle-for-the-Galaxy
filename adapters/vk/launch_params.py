"""Верификация подписанных launch params VK Mini Apps (решение C2).

Клиент присылает сырые launch params (query string открытия Mini App) в заголовке
X-VK-Launch-Params; сервер проверяет sign по защищённому ключу приложения
(настройка VK_APP_SECRET). Без ключа — только dev-заголовок X-Character-Id.
"""
import base64
import hashlib
import hmac
from urllib.parse import parse_qsl, urlencode


def verify_launch_params(query: str, app_secret: str) -> dict[str, str]:
    """Проверить sign launch params. Возвращает params при валидной подписи, иначе ValueError."""
    params = dict(parse_qsl(query, keep_blank_values=True))
    sign = params.pop("sign", None)
    if not sign:
        raise ValueError("нет параметра sign")
    # Официальная схема VK: только параметры vk_*, отсортированные пары
    # key=value, перекодированные urlencode, HMAC-SHA256 защищённым ключом,
    # base64url без padding.
    vk_params = {k: v for k, v in params.items() if k.startswith("vk_")}
    data = urlencode(sorted(vk_params.items()))
    digest = hmac.new(app_secret.encode(), data.encode(), hashlib.sha256).digest()
    expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    if not hmac.compare_digest(sign, expected):
        raise ValueError("невалидная подпись launch params")
    return params
