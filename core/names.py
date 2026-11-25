"""Проверка имён персонажей (ТЗ: оскорбления, запрещённые символы, имитация администрации).

Конкретные blacklist-паттерны и длины — config_params (решение D8, docs/04-decisions.md),
значения по умолчанию в миграции 0002. Здесь только чистая логика.
"""
import re
from collections.abc import Sequence

ALLOWED_CHARS = re.compile(r"^[\w\-. ]+$", re.UNICODE)  # буквы (вкл. кириллицу), цифры, _-. и пробел


def normalize_name(name: str) -> str:
    """Схлопывание пробелов и trim для сравнения/поиска."""
    return " ".join(name.split())


def validate_name(
    name: str,
    *,
    min_length: int = 3,
    max_length: int = 24,
    forbidden_patterns: Sequence[str] = (),
) -> str | None:
    """None — имя допустимо; иначе код причины отказа."""
    normalized = normalize_name(name)
    if len(normalized) < min_length:
        return "too_short"
    if len(normalized) > max_length:
        return "too_long"
    # сначала запрещённые паттерны: попытка имитации админов с [ ] отчитывается как forbidden,
    # а не как invalid_chars (скобки вне разрешённого набора символов)
    for pattern in forbidden_patterns:
        if re.search(pattern, normalized):
            return "forbidden"
    if not ALLOWED_CHARS.match(normalized):
        return "invalid_chars"
    return None
