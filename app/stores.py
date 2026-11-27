"""Хранилища для инфраструктурных сущностей (config, outbox, timers, audit)."""
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, ConfigParam, Formula, MaintenanceState, Outbox, Timer

Params = dict[str, Any]
TimerHandler = Callable[[AsyncSession, Params], Coroutine[Any, Any, None]]


async def get_param(session: AsyncSession, key: str, default: Any = None) -> Any:
    """Чтение параметра конфигурации. Hot-reload: значение читается из БД (ТЗ)."""
    value = await session.scalar(sa.select(ConfigParam.value).where(ConfigParam.key == key))
    return default if value is None else value


async def list_params(session: AsyncSession) -> dict[str, Any]:
    rows = (await session.scalars(sa.select(ConfigParam.key, ConfigParam.value))).all()
    return {k: v for k, v in rows}


async def get_formula(session: AsyncSession, key: str) -> str | None:
    return await session.scalar(sa.select(Formula.expression).where(Formula.key == key))


async def add_outbox(
    session: AsyncSession,
    *,
    character_id: uuid.UUID,
    text: str,
    platform: str | None = None,
    payload: Params | None = None,
) -> None:
    # next_attempt_at — с клиентских часов: на Windows datetime.now() грубее
    # часов PostgreSQL на тик (~15 мс), и свежее сообщение с server_default
    # now() могло «не наступить» для первого же fetch (мигал тесты и задерживал
    # первую попытку доставки).
    session.add(
        Outbox(
            character_id=character_id,
            text=text,
            platform=platform,
            payload=payload,
            next_attempt_at=datetime.now(UTC),
        )
    )


async def fetch_due_outbox(
    session: AsyncSession, *, limit: int, now: datetime | None = None
) -> list[Outbox]:
    """Забирает пачку pending-сообщений, пропуская уже занятые (SKIP LOCKED).

    Время — с клиента: now() PostgreSQL заморожено на начало транзакции,
    что ломало retry внутри длинной транзакции.
    """
    now = now or datetime.now(UTC)
    return list(
        (
            await session.scalars(
                sa.select(Outbox)
                .where(Outbox.status == "pending", Outbox.next_attempt_at <= now)
                .order_by(Outbox.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )


def schedule_outbox_retry(
    msg: Outbox,
    *,
    max_attempts: int,
    base_delay: float = 1.0,
    cap: float = 300.0,
    now: datetime | None = None,
) -> None:
    """Экспоненциальный бэкофф (base × 2^(n-1), кап); после max_attempts — failed."""
    now = now or datetime.now(UTC)
    if msg.attempts >= max_attempts:
        msg.status = "failed"
        return
    delay = min(cap, base_delay * 2 ** (msg.attempts - 1))
    msg.next_attempt_at = datetime.fromtimestamp(now.timestamp() + delay, tz=UTC)


async def claim_due_timers(
    session: AsyncSession, *, limit: int, now: datetime | None = None
) -> list[Timer]:
    """Помечает просроченные таймеры fired и возвращает их (выполнение — в той же транзакции).

    Время — с клиента (см. fetch_due_outbox).
    """
    now = now or datetime.now(UTC)
    subq = (
        sa.select(Timer.id)
        .where(Timer.status == "pending", Timer.fires_at <= now)
        .order_by(Timer.fires_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = (
        await session.scalars(
            sa.update(Timer)
            .where(Timer.id.in_(subq))
            .values(status="fired")
            .returning(Timer)
        )
    ).all()
    return list(rows)


async def cancel_timer(
    session: AsyncSession, *, timer_type: str, payload_id_key: str, payload_id: Any
) -> bool:
    """Отмена таймера по ссылке в payload (отмена крафта/лота/передатки)."""
    expr = Timer.payload[payload_id_key].astext == str(payload_id)
    cancelled_id = await session.scalar(
        sa.update(Timer)
        .where(Timer.type == timer_type, Timer.status == "pending", expr)
        .values(status="cancelled")
        .returning(Timer.id)
    )
    return cancelled_id is not None


async def is_maintenance_on(session: AsyncSession) -> bool:
    row = await session.execute(sa.select(MaintenanceState.enabled))
    value = row.scalar_one_or_none()
    return bool(value) if value is not None else False


async def write_audit(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: str,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    before: Params | None = None,
    after: Params | None = None,
    ip: str | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_id=actor_id,
            actor_role=actor_role,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before=before,
            after=after,
            ip=ip,
        )
    )
