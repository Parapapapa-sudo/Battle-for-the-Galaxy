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
from app.handlers import ACCOUNT_COMMANDS, CHARACTER_COMMANDS, parse_command
from app.locale import t
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
    if battle.status in ("won", "lost"):
        return CommandResult(
            message=await battles.describe_battle_outcome(session, battle, character.lang)
        )
    return CommandResult(
        message=await battles.describe_round_result(session, battle, lang=character.lang)
    )


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
        return CommandResult(message=t("welcome_new"))

    async with sessions() as session, transactional(session):
        account, _created = await characters.get_or_create_account(
            session, platform=platform, platform_user_id=platform_user_id
        )
        character = await characters.get_active_character(session, account.id)

        # автосинхронизация таймеров (прибытие, крафт…) при ЛЮБОЙ команде —
        # без worker'а прогресс не зависает; эффект идемпотентен.
        if character is not None:
            from app.timer_handlers import sync_character_timers

            await sync_character_timers(session, character.id)

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
        # онбординг: после выбора фракции любое свободное сообщение — имя капитана
        onboarding = dict(account.onboarding or {})
        faction_code = onboarding.get("faction")
        if faction_code:
            try:
                created = await characters.create_character(
                    session,
                    account_id=account.id,
                    name=text.strip(),
                    faction_code=str(faction_code),
                )
            except DomainError as exc:  # имя не прошло валидацию — состояние сохраняется
                return error_result(exc)
            account.onboarding = {}
            await session.flush()
            faction = await characters.get_faction_by_code(session, str(faction_code))
            return CommandResult(
                message=t(
                    "start_created",
                    created.lang,
                    name=created.name,
                    faction=faction.name if faction else str(faction_code),
                ),
                data={"character_id": str(created.id)},
            )
        # новичок: приветствие + кнопка «Начать»
        return CommandResult(message=t("welcome_new"))
