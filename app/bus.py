"""Конвейер команд (ТЗ §Надёжность):

1) идемпотентность — повтор той же команды возвращает сохранённый ответ без эффектов;
2) последовательность — advisory lock на субъект (персонаж/аккаунт), иначе BusyError;
3) атомарность — весь use case, ответ, outbox-записи и ключ идемпотентности в одной транзакции;
4) режим обслуживания — команды отклоняются до снятия флага.
"""
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import db, stores
from app.metrics import observe_command
from app.models import IdempotencyKey
from core.errors import BusyError, DomainError

log = structlog.get_logger(__name__)

Params = dict[str, Any]


@dataclass
class CommandContext:
    """Кто выполняет команду: персонаж (игровые команды) или аккаунт (start/factions)."""

    subject: str  # "char:<uuid>" | "acc:<uuid>" — ключ advisory lock
    character_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None


# handler(session, ctx, args) -> CommandResult
CommandHandler = Callable[[AsyncSession, CommandContext, Params], Awaitable["CommandResult"]]


@dataclass
class OutgoingMessage:
    text: str
    important: bool = False  # важные результаты сообщаются отдельно (ТЗ, крафт)


@dataclass
class CommandResult:
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    messages: list[OutgoingMessage] = field(default_factory=list)
    # пак картинок (assets/images/<пак>); адаптер прикладывает случайное фото
    image_pack: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "data": self.data,
            "messages": [m.__dict__ for m in self.messages],
            "image_pack": self.image_pack,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "CommandResult":
        return CommandResult(
            message=raw.get("message", ""),
            data=raw.get("data", {}),
            messages=[OutgoingMessage(**m) for m in raw.get("messages", [])],
            image_pack=raw.get("image_pack"),
        )


def default_idempotency_key(subject: str, command: str, args: Params) -> str:
    """Детерминированный ключ для ретраев адаптера платформы."""
    body = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(f"{subject}:{command}:{body}".encode()).hexdigest()
    return digest[:64]


class CommandBus:
    def __init__(self, sessions: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._sessions = sessions

    @observe_command("character")
    async def execute(
        self,
        *,
        character_id: uuid.UUID,
        command: str,
        args: Params,
        handler: CommandHandler,
        idempotency_key: str | None = None,
        session: AsyncSession | None = None,
        reply_via_outbox: bool = True,
    ) -> CommandResult:
        ctx = CommandContext(
            subject=f"char:{character_id}", character_id=character_id
        )
        return await self._execute(
            ctx=ctx,
            command=command,
            args=args,
            handler=handler,
            idempotency_key=idempotency_key,
            session=session,
            reply_via_outbox=reply_via_outbox,
        )

    @observe_command("account")
    async def execute_account(
        self,
        *,
        account_id: uuid.UUID,
        command: str,
        args: Params,
        handler: CommandHandler,
        idempotency_key: str | None = None,
        session: AsyncSession | None = None,
        reply_via_outbox: bool = True,
    ) -> CommandResult:
        """Команды до создания персонажа: lock на аккаунт, а не на персонажа."""
        ctx = CommandContext(subject=f"acc:{account_id}", account_id=account_id)
        return await self._execute(
            ctx=ctx,
            command=command,
            args=args,
            handler=handler,
            idempotency_key=idempotency_key,
            session=session,
            reply_via_outbox=reply_via_outbox,
        )

    async def _execute(
        self,
        *,
        ctx: CommandContext,
        command: str,
        args: Params,
        handler: CommandHandler,
        idempotency_key: str | None,
        session: AsyncSession | None,
        reply_via_outbox: bool,
    ) -> CommandResult:
        key = idempotency_key or default_idempotency_key(ctx.subject, command, args)

        async def _run(s: AsyncSession) -> CommandResult:
            stored = await s.get(IdempotencyKey, key)
            if stored is not None:
                log.info("idempotent_hit", key=key, command=command)
                return CommandResult.from_dict(stored.response)

            if await stores.is_maintenance_on(s):
                raise DomainError("maintenance", "Технические работы. Попробуйте позже.")

            if not await db.try_advisory_lock(await s.connection(), ctx.subject):
                raise BusyError()

            result = await handler(s, ctx, args)
            if ctx.character_id is not None:
                # Ответы и уведомления уходят через outbox только для персонажей;
                # для аккаунт-команд (start/factions) ответ доставляет адаптер напрямую.
                if reply_via_outbox:
                    await stores.add_outbox(
                        s, character_id=ctx.character_id, text=result.message,
                        payload={"kind": "reply"},
                    )
                for msg in result.messages:
                    await stores.add_outbox(
                        s,
                        character_id=ctx.character_id,
                        text=msg.text,
                        payload={"important": msg.important},
                    )
            s.add(
                IdempotencyKey(
                    key=key,
                    character_id=ctx.character_id,
                    scope="command",
                    response=result.to_dict(),
                )
            )
            log.info("command_done", command=command, subject=ctx.subject)
            return result

        if session is not None:
            async with db.transactional(session):
                return await _run(session)

        if self._sessions is None:
            raise RuntimeError("CommandBus: не задана фабрика сессий")
        async with self._sessions() as s, db.transactional(s):
            return await _run(s)

    async def run_due_timers(
        self,
        *,
        timer_handlers: dict[str, stores.TimerHandler],
        limit: int = 100,
        session: AsyncSession | None = None,
    ) -> int:
        """Таймер = команда: идемпотентно (status fired в той же транзакции, что и эффект)."""

        async def _run(s: AsyncSession) -> int:
            processed = 0
            due = await stores.claim_due_timers(s, limit=limit)
            for timer in due:
                handler = timer_handlers.get(timer.type)
                if handler is None:
                    log.warning("timer_no_handler", type=timer.type, id=str(timer.id))
                    continue
                await handler(s, timer.payload or {})
                processed += 1
            return processed

        if session is not None:
            async with db.transactional(session):
                return await _run(session)

        if self._sessions is None:
            raise RuntimeError("CommandBus: не задана фабрика сессий")
        async with self._sessions() as s, db.transactional(s):
            return await _run(s)


def error_result(exc: DomainError) -> CommandResult:
    """Маппинг доменной ошибки в ответ игроку (без раскрытия внутренностей)."""
    return CommandResult(message=exc.message, data={"error": exc.code, "details": exc.details})


__all__ = [
    "CommandBus",
    "CommandContext",
    "CommandResult",
    "DomainError",
    "OutgoingMessage",
    "default_idempotency_key",
    "error_result",
]
