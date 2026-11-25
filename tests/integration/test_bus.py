"""Конвейер команд: последовательность, идемпотентность, откат при ошибке, техработы.

Покрывает требования ТЗ «Надёжность»: команды последовательны, повторы без эффектов.
"""
import asyncio
import uuid

import pytest
import sqlalchemy as sa

from app import stores
from app.bus import CommandContext, CommandResult, default_idempotency_key
from app.models import IdempotencyKey, MaintenanceState, Outbox
from core.errors import BusyError, DomainError
from tests.conftest import requires_db


@requires_db
async def test_command_writes_idempotency_record(session, bus):
    char_id = uuid.uuid4()

    async def handler(s, ctx: CommandContext, args):
        return CommandResult(message="ok")

    result = await bus.execute(
        character_id=char_id, command="demo", args={}, handler=handler, session=session
    )
    assert result.message == "ok"

    stored = await session.get(IdempotencyKey, default_idempotency_key(f"char:{char_id}", "demo", {}))
    assert stored is not None
    assert stored.response["message"] == "ok"


@requires_db
async def test_repeat_command_returns_stored_response_without_effect(session, bus):
    char_id = uuid.uuid4()
    calls = 0

    async def handler(s, ctx: CommandContext, args):
        nonlocal calls
        calls += 1
        return CommandResult(message=f"ok#{calls}")

    r1 = await bus.execute(
        character_id=char_id, command="demo", args={}, handler=handler, session=session
    )
    r2 = await bus.execute(
        character_id=char_id, command="demo", args={}, handler=handler, session=session
    )
    assert calls == 1  # эффект ровно один
    assert r2.message == r1.message == "ok#1"


@requires_db
async def test_concurrent_command_of_same_character_gets_busy(session, bus):
    """Вторая команда того же персонажа во время выполнения первой → BusyError.

    Как в проде: две команды идут по РАЗНЫМ соединениям; lock держит первая
    транзакция, вторая получает «занято» и ничего не выполняет.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db import engine

    char_id = uuid.uuid4()
    started = asyncio.Event()
    release = asyncio.Event()
    second_called = False

    async def slow_handler(s, ctx: CommandContext, args):
        started.set()
        await release.wait()
        return CommandResult(message="done")

    async def second_handler(s, ctx: CommandContext, args):
        nonlocal second_called
        second_called = True
        return CommandResult(message="never")

    task = asyncio.create_task(
        bus.execute(
            character_id=char_id, command="slow", args={}, handler=slow_handler, session=session
        )
    )
    await asyncio.wait_for(started.wait(), timeout=5)

    async with engine.connect() as conn2:
        session2 = AsyncSession(bind=conn2, expire_on_commit=False)
        with pytest.raises(BusyError):
            await bus.execute(
                character_id=char_id,
                command="other",
                args={},
                handler=second_handler,
                session=session2,
            )
        await conn2.close()

    release.set()
    assert (await asyncio.wait_for(task, timeout=5)).message == "done"
    assert not second_called


@requires_db
async def test_domain_error_rolls_back_everything(session, bus):
    char_id = uuid.uuid4()

    async def failing(s, ctx: CommandContext, args):
        assert ctx.character_id is not None
        await stores.add_outbox(s, character_id=ctx.character_id, text="не должно уйти")
        raise DomainError("boom", "Всё откачено")

    with pytest.raises(DomainError):
        await bus.execute(
            character_id=char_id, command="fail", args={}, handler=failing, session=session
        )

    assert await session.get(IdempotencyKey, default_idempotency_key(f"char:{char_id}", "fail", {})) is None
    rows = (
        await session.scalars(sa.select(Outbox).where(Outbox.character_id == char_id))
    ).all()
    assert rows == []


@requires_db
async def test_account_scope_commands_lock_account(session, bus):
    """execute_account: lock на аккаунт, контекст с account_id."""
    account_id = uuid.uuid4()
    seen_ctx = None

    async def handler(s, ctx: CommandContext, args):
        nonlocal seen_ctx
        seen_ctx = ctx
        return CommandResult(message="acc-ok")

    result = await bus.execute_account(
        account_id=account_id, command="start", args={}, handler=handler, session=session
    )
    assert result.message == "acc-ok"
    assert seen_ctx is not None
    assert seen_ctx.account_id == account_id
    assert seen_ctx.character_id is None
    assert seen_ctx.subject == f"acc:{account_id}"


@requires_db
async def test_maintenance_mode_rejects_commands(session, bus):
    char_id = uuid.uuid4()
    session.add(MaintenanceState(id=1, enabled=True, message="Техработы"))
    await session.flush()

    async def handler(s, ctx: CommandContext, args):
        raise AssertionError("handler не должен вызываться при техработах")

    with pytest.raises(DomainError) as exc_info:
        await bus.execute(
            character_id=char_id, command="me", args={}, handler=handler, session=session
        )
    assert exc_info.value.code == "maintenance"
