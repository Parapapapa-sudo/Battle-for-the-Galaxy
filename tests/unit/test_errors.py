"""Доменные ошибки: BusyError (ТЗ: последовательность команд, «занятость»)."""
from core.errors import BusyError, DomainError


def test_busy_error_code_and_message():
    err = BusyError()
    assert err.code == "busy"
    assert "Команда уже выполняется" in err.message


def test_busy_error_is_domain_error_with_empty_details():
    err = BusyError()
    assert isinstance(err, DomainError)
    assert err.details == {}
    assert str(err) == err.message
