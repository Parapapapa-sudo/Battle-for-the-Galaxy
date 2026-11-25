"""Доменные ошибки. Сообщение DomainError можно показывать игроку."""
from typing import Any


class DomainError(Exception):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


class BusyError(DomainError):
    """Персонаж занят выполнением другой команды (ТЗ: последовательность команд)."""

    def __init__(self) -> None:
        super().__init__("busy", "Команда уже выполняется. Подождите завершения.")
