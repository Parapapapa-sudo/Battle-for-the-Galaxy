"""Маршрутизация текстовой команды от платформы в CommandBus.

Общая логика адаптеров (VK/консоль): identity → аккаунт → выбор скоупа
(персонаж существует → character-команды; иначе → account-команды).
Создание аккаунта фиксируется даже если команда отклонена доменной ошибкой.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import characters
from app.bus import CommandBus, CommandContext, CommandResult, error_result
from app.db import transactional
from app.handlers import ACCOUNT_COMMANDS, CHARACTER_COMMANDS, USAGE_NO_CHARACTER, parse_command
from core.errors import DomainError


async def _ability_handler(
    session: AsyncSession, ctx: CommandContext, args: dict[str, Any]
) -> CommandResult:
    """Обработчик динамических боевых команд установленных способностей."""
    from app import battles
    from app.characters import load_character

    assert ctx.character_id is not None
    character = await load_character(session, ctx.character_id)
    battle = await battles.player_ability(session, character, str(args.get("cmd", "")))
    header = await battles.describe_battle(session, battle)
    turns = await battles.battle_journal(session, battle, limit=3)
    lines = [header] + [f"[{t.actor}] {t.summary}" for t in turns]
    return CommandResult(message="\n".join(lines))


async def route_text_command(
    bus: CommandBus,
    sessions: async_sessionmaker[AsyncSession],
    *,
    platform: str,
    platform_user_id: str,
    text: str,
    idempotency_key: str | None = None,
) -> CommandResult:
    cmd, args = parse_command(text)
    if not cmd:
        return CommandResult(message=USAGE_NO_CHARACTER)

    async with sessions() as session, transactional(session):
        account, _created = await characters.get_or_create_account(
            session, platform=platform, platform_user_id=platform_user_id
        )
        character = await characters.get_active_character(session, account.id)

        try:
            if character is not None and cmd in CHARACTER_COMMANDS:
                return await bus.execute(
                    character_id=character.id,
                    command=cmd,
                    args=args,
                    handler=CHARACTER_COMMANDS[cmd],
                    idempotency_key=idempotency_key,
                    session=session,
                    reply_via_outbox=False,
                )
            # динамические команды способностей установленных модулей (этап 5)
            if character is not None:
                from app import modules as modules_app

                ability = await modules_app.ability_for_cmd(session, character, cmd)
                if ability is not None:
                    return await bus.execute(
                        character_id=character.id,
                        command=cmd,
                        args={"cmd": cmd},
                        handler=_ability_handler,
                        idempotency_key=idempotency_key,
                        session=session,
                        reply_via_outbox=False,
                    )
                # цифровой ответ на интерактивное событие: кнопки «1»/«2» с клавиатуры
                if cmd.isdigit():
                    from app.expeditions import _current_offered_event, get_ongoing_expedition

                    expedition = await get_ongoing_expedition(session, character.id)
                    if expedition is not None and expedition.status == "active":
                        offered = await _current_offered_event(session, expedition)
                        if (
                            offered is not None
                            and offered.snapshot.get("kind") == "interactive"
                            and any(
                                str(c.get("key")) == cmd
                                for c in offered.snapshot.get("script", {}).get("choices", [])
                            )
                        ):
                            return await bus.execute(
                                character_id=character.id,
                                command="choice",
                                args={"key": cmd},
                                handler=CHARACTER_COMMANDS["choice"],
                                idempotency_key=idempotency_key,
                                session=session,
                                reply_via_outbox=False,
                            )
            if cmd in ACCOUNT_COMMANDS:
                return await bus.execute_account(
                    account_id=account.id,
                    command=cmd,
                    args=args,
                    handler=ACCOUNT_COMMANDS[cmd],
                    idempotency_key=idempotency_key,
                    session=session,
                    reply_via_outbox=False,
                )
        except DomainError as exc:
            return error_result(exc)

        if character is not None:
            return CommandResult(message=f"Неизвестная команда: {cmd}")
        return CommandResult(message=USAGE_NO_CHARACTER)
