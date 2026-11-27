"""Верификация подписи VK Mini Apps launch params (официальная схема VK)."""
import base64
import hashlib
import hmac
from urllib.parse import urlencode

import pytest

from adapters.vk.launch_params import verify_launch_params

SECRET = "test-app-secret"


def _sign(params: dict[str, str], secret: str = SECRET) -> str:
    """Подпись как у VK: urlencode отсортированных vk_*-пар, HMAC-SHA256, base64url."""
    data = urlencode(sorted(params.items()))
    digest = hmac.new(secret.encode(), data.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def test_valid_signature() -> None:
    params = {"vk_user_id": "12345", "vk_app_id": "512000", "vk_ts": "1700000000"}
    query = "&".join(f"{k}={v}" for k, v in params.items()) + f"&sign={_sign(params)}"
    result = verify_launch_params(query, SECRET)
    assert result["vk_user_id"] == "12345"


def test_valid_signature_with_special_chars() -> None:
    """Значения со спецсимволами: VK подписывает urlencode-форму."""
    params = {"vk_user_id": "12345", "vk_ref": "some ref/with+chars"}
    query = "vk_user_id=12345&vk_ref=some+ref%2Fwith%2Bchars&sign=" + _sign(params)
    result = verify_launch_params(query, SECRET)
    assert result["vk_ref"] == "some ref/with+chars"


def test_extra_non_vk_params_ignored() -> None:
    """Посторонние параметры (не vk_*) не участвуют в подписи."""
    params = {"vk_user_id": "12345"}
    query = f"utm_source=x&vk_user_id=12345&sign={_sign(params)}"
    result = verify_launch_params(query, SECRET)
    assert result["vk_user_id"] == "12345"


def test_tampered_params_rejected() -> None:
    params = {"vk_user_id": "12345", "vk_ts": "1700000000"}
    query = f"vk_user_id=99999&vk_ts={params['vk_ts']}&sign={_sign(params)}"
    with pytest.raises(ValueError):
        verify_launch_params(query, SECRET)


def test_wrong_secret_rejected() -> None:
    params = {"vk_user_id": "12345"}
    query = f"vk_user_id=12345&sign={_sign(params, 'other-secret')}"
    with pytest.raises(ValueError):
        verify_launch_params(query, SECRET)


def test_missing_sign() -> None:
    with pytest.raises(ValueError):
        verify_launch_params("vk_user_id=12345", SECRET)
