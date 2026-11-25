"""Prometheus-метрики: команды, латентность, занятость (мониторинг — ТЗ)."""
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from core.errors import BusyError

F = TypeVar("F", bound=Callable[..., Any])

COMMANDS_TOTAL = Counter(
    "mmo_commands_total",
    "Игровые команды по результату",
    ["command", "result"],  # result: ok|domain_error|busy|error
)
COMMAND_LATENCY = Histogram(
    "mmo_command_seconds",
    "Латентность исполнения команды",
    ["command"],
)
TIMER_FIRES = Counter("mmo_timer_fires_total", "Сработавшие таймеры", ["type"])
OUTBOX_SENT = Counter("mmo_outbox_sent_total", "Доставленные сообщения outbox")
OUTBOX_FAILED = Counter("mmo_outbox_attempts_failed_total", "Неудачные попытки отправки")


def observe_command(command: str) -> Callable[[F], F]:
    """Декоратор: считает результат и латентность исполнения команды."""

    def decorator(fn: F) -> F:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
            except BusyError:
                COMMANDS_TOTAL.labels(command=command, result="busy").inc()
                raise
            except Exception:
                COMMANDS_TOTAL.labels(command=command, result="error").inc()
                raise
            else:
                status = "ok"
                data = getattr(result, "data", None)
                if isinstance(data, dict) and data.get("error"):
                    status = "busy" if data["error"] == "busy" else "domain_error"
                COMMANDS_TOTAL.labels(command=command, result=status).inc()
                return result
            finally:
                COMMAND_LATENCY.labels(command=command).observe(time.perf_counter() - start)

        return wrapper  # type: ignore[return-value]

    return decorator


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
