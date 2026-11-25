"""Конфигурация приложения. Все значения — через переменные окружения."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"  # dev | staging | prod
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"

    # VK (заполняется при подключении реального бота; этап 1)
    vk_bot_token: str | None = None
    vk_group_id: int | None = None
    vk_api_version: str = "5.199"

    # worker
    worker_poll_interval_sec: float = 1.0
    outbox_batch_size: int = 50
    outbox_send_per_sec: float = 20.0  # лимит исходящих сообщений
    outbox_max_attempts: int = 10

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
