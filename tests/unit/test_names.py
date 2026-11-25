"""Проверка имён персонажей (ТЗ: оскорбления, запрещённые символы, имитация админов)."""
import pytest

from core.names import normalize_name, validate_name

FORBIDDEN = [r"\[admin\]", "(?i)админ", "(?i)admin"]


@pytest.mark.parametrize(
    "name",
    ["Иван", "Alice", "Капитан Немо", "X-9", "Мария.Петрова", "user_42"],
)
def test_valid_names(name):
    assert validate_name(name, forbidden_patterns=FORBIDDEN) is None


def test_too_short():
    assert validate_name("Аб", forbidden_patterns=FORBIDDEN) == "too_short"


def test_too_long():
    assert validate_name("ОченьДлинноеИмяДляПерсонажаMMO", max_length=24,
                         forbidden_patterns=FORBIDDEN) == "too_long"


def test_invalid_chars():
    assert validate_name("Иван!@#", forbidden_patterns=FORBIDDEN) == "invalid_chars"
    assert validate_name("О'кей", forbidden_patterns=FORBIDDEN) == "invalid_chars"


def test_admin_imitation():
    assert validate_name("[Admin]Грех", forbidden_patterns=FORBIDDEN) == "forbidden"
    assert validate_name("Администратор", forbidden_patterns=FORBIDDEN) == "forbidden"


def test_normalize_collapse_spaces():
    assert normalize_name("  Иван   Грозный  ") == "Иван Грозный"
