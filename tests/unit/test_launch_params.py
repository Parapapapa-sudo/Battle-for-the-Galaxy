"""Верификация подписи VK Mini Apps launch params."""
import base64
import hashlib
import hmac

import pytest

from adapters.vk.launch_params import verify_launch_params

SECRET = "test-app-secret"


def _sign(params: dict[str, str], secret: str = SECRET) -> str:
    data = "".join(f"{k}={v}" for k, v in sorted(params.items()))
    digest = hmac.new(secret.encode(), data.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def test_valid_signature() -> None:
    params = {"vk_user_id": "12345", "vk_app_id": "512000", "vk_ts": "1700000000"}
    query = "&".join(f"{k}={v}" for k, v in params.items()) + f"&sign={_sign(params)}"
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
