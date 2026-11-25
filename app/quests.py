"""Этап 8: задания (обучение/сюжет) и фракционные события.

ТЗ-критерии:
- первое сюжетное задание обязательно; обучение через цепочку заданий и события первой экспедиции;
- фракционные события: общие цели + индивидуальный вклад; запуск по расписанию или вручную;
- награды начисляются атомарно с завершением.
"""
import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import characters, economy, stores
from app.models import Timer
from app.models_game import (
    Character,
    CharacterQuest,
    FactionEvent,
    FactionEventParticipant,
    QuestTemplate,
)
from core.errors import DomainError

# --- цепочка заданий ---------------------------------------------------------------


async def activate_first_quests(session: AsyncSession, character: Character) -> None:
    """Обучение: активируем задание с chain_order=1 (общее или фракционное)."""
    quest = await session.scalar(
        sa.select(QuestTemplate)
        .where(
            QuestTemplate.status == "published",
            QuestTemplate.chain_order == 1,
            sa.or_(
                QuestTemplate.faction_id.is_(None),
                QuestTemplate.faction_id == character.faction_id,
            ),
        )
        .order_by(QuestTemplate.faction_id.is_(None))  # фракционное предпочтительнее
        .limit(1)
    )
    if quest is None:
        return
    session.add(CharacterQuest(character_id=character.id, quest_id=quest.id))
    await session.flush()


async def active_quests(session: AsyncSession, character: Character) -> list[tuple[CharacterQuest, QuestTemplate]]:
    rows = (
        await session.execute(
            sa.select(CharacterQuest, QuestTemplate)
            .join(QuestTemplate, QuestTemplate.id == CharacterQuest.quest_id)
            .where(CharacterQuest.character_id == character.id, CharacterQuest.status == "active")
            .order_by(QuestTemplate.chain_order)
        )
    ).all()
    return [(cq, qt) for cq, qt in rows]


async def track(session: AsyncSession, character: Character, event_type: str, data: dict[str, Any] | None = None) -> None:
    """Единая точка прогресса: задания + фракционные события.

    Вызывается из экспедиций/боёв/крафта в той же транзакции (ленивый импорт в местах вызова).
    """
    data = data or {}
    for quest_state, quest in await active_quests(session, character):
        changed = False
        for idx, objective in enumerate(quest.objectives):
            if objective.get("type") != event_type:
                continue
            if objective.get("code") and objective.get("code") != data.get("code"):
                continue
            key = str(idx)
            quest_state.progress[key] = int(quest_state.progress.get(key, 0)) + 1
            changed = True
        if changed and _quest_complete(quest_state, quest):
            await _complete_quest(session, character, quest_state, quest)

    await _track_faction_event(session, character, event_type)


def _quest_complete(quest_state: CharacterQuest, quest: QuestTemplate) -> bool:
    for idx, objective in enumerate(quest.objectives):
        need = int(objective.get("count", 1))
        if int(quest_state.progress.get(str(idx), 0)) < need:
            return False
    return True


async def _complete_quest(
    session: AsyncSession,
    character: Character,
    quest_state: CharacterQuest,
    quest: QuestTemplate,
) -> None:
    quest_state.status = "completed"
    quest_state.completed_at = datetime.now(UTC)

    rewards = quest.rewards or {}
    for grant in rewards.get("items", []):
        await characters.grant_items(
            session, character, code=str(grant["code"]), qty=int(grant["qty"]), allow_loss=True
        )
    if rewards.get("quant"):
        await economy.credit(
            session, character, economy.QUANT, int(rewards["quant"]), reason="quest_reward"
        )
    if rewards.get("xp"):
        character.xp_pool += int(rewards["xp"])
    await stores.add_outbox(
        session,
        character_id=character.id,
        text=f"Задание «{quest.name}» выполнено! Награды начислены.",
    )

    # следующее в цепочке
    nxt = await session.scalar(
        sa.select(QuestTemplate).where(
            QuestTemplate.status == "published",
            QuestTemplate.chain_order == quest.chain_order + 1,
            sa.or_(
                QuestTemplate.faction_id.is_(None),
                QuestTemplate.faction_id == character.faction_id,
            ),
        )
    )
    if nxt is not None:
        existing = await session.scalar(
            sa.select(CharacterQuest).where(
                CharacterQuest.character_id == character.id, CharacterQuest.quest_id == nxt.id
            )
        )
        if existing is None:
            session.add(CharacterQuest(character_id=character.id, quest_id=nxt.id))
            await stores.add_outbox(
                session,
                character_id=character.id,
                text=f"Новое задание: «{nxt.name}». {nxt.description}",
            )
    await session.flush()


async def describe_quests(session: AsyncSession, character: Character) -> str:
    rows = await active_quests(session, character)
    if not rows:
        return "Активных заданий нет."
    lines = ["Задания:"]
    for state, quest in rows:
        parts = []
        for idx, objective in enumerate(quest.objectives):
            need = int(objective.get("count", 1))
            have = min(int(state.progress.get(str(idx), 0)), need)
            parts.append(f"{objective.get('type')} {have}/{need}")
        lines.append(f"— {quest.name}: {', '.join(parts)}")
    return "\n".join(lines)


# --- фракционные события ------------------------------------------------------------


async def _track_faction_event(session: AsyncSession, character: Character, event_type: str) -> None:
    events = (
        await session.scalars(
            sa.select(FactionEvent).where(
                FactionEvent.status == "active",
                FactionEvent.starts_at <= datetime.now(UTC),
                FactionEvent.ends_at > datetime.now(UTC),
            )
        )
    ).all()
    for event in events:
        goal = (event.config or {}).get("goal", {})
        if goal.get("type") != event_type:
            continue
        participant = await session.get(
            FactionEventParticipant, (event.id, character.id)
        )
        if participant is None:
            participant = FactionEventParticipant(event_id=event.id, character_id=character.id)
            session.add(participant)
            await session.flush()
        participant.contribution += 1
        await _maybe_finish_faction_event(session, event)


async def _maybe_finish_faction_event(session: AsyncSession, event: FactionEvent) -> None:
    goal_count = int((event.config or {}).get("goal", {}).get("count", 0))
    if goal_count <= 0:
        return
    total = int(
        await session.scalar(
            sa.select(sa.func.coalesce(sa.func.sum(FactionEventParticipant.contribution), 0)).where(
                FactionEventParticipant.event_id == event.id
            )
        )
        or 0
    )
    if total >= goal_count:
        await finish_faction_event(session, event)


async def schedule_faction_event(
    session: AsyncSession,
    *,
    name: str,
    config: dict[str, Any],
    starts_at: datetime,
    ends_at: datetime,
    created_by: str = "system",
) -> FactionEvent:
    """Запуск по расписанию (таймеры старта/финиша) — ТЗ."""
    if ends_at <= starts_at:
        raise DomainError("bad_window", "Окно события некорректно.")
    event = FactionEvent(
        name=name, config=config, starts_at=starts_at, ends_at=ends_at, created_by=created_by
    )
    session.add(event)
    await session.flush()
    session.add(
        Timer(type="faction_event_start", payload={"event_id": str(event.id)}, fires_at=starts_at)
    )
    session.add(
        Timer(type="faction_event_end", payload={"event_id": str(event.id)}, fires_at=ends_at)
    )
    await session.flush()
    return event


async def faction_event_start_handler(session: AsyncSession, payload: dict[str, Any]) -> None:
    event = await session.get(FactionEvent, uuid.UUID(str(payload["event_id"])))
    if event is None or event.status != "scheduled":
        return
    event.status = "active"
    await session.flush()


async def faction_event_end_handler(session: AsyncSession, payload: dict[str, Any]) -> None:
    event = await session.get(FactionEvent, uuid.UUID(str(payload["event_id"])))
    if event is None or event.status not in ("scheduled", "active"):
        return
    await finish_faction_event(session, event)


async def finish_faction_event(session: AsyncSession, event: FactionEvent) -> None:
    """Финиш: если общая цель достигнута — награды всем участникам с вкладом (ТЗ)."""
    event.status = "finished"
    goal = (event.config or {}).get("goal", {})
    rewards = (event.config or {}).get("rewards", {})
    goal_count = int(goal.get("count", 0))
    total = int(
        await session.scalar(
            sa.select(sa.func.coalesce(sa.func.sum(FactionEventParticipant.contribution), 0)).where(
                FactionEventParticipant.event_id == event.id
            )
        )
        or 0
    )
    success = goal_count <= 0 or total >= goal_count

    participants = (
        await session.scalars(
            sa.select(FactionEventParticipant).where(
                FactionEventParticipant.event_id == event.id,
                FactionEventParticipant.contribution > 0,
            )
        )
    ).all()
    if success:
        for participant in participants:
            if participant.rewards_granted:
                continue
            character = await session.get(Character, participant.character_id)
            if character is None:
                continue
            for grant in rewards.get("items", []):
                await characters.grant_items(
                    session, character, code=str(grant["code"]), qty=int(grant["qty"]), allow_loss=True
                )
            if rewards.get("quant"):
                await economy.credit(
                    session, character, economy.QUANT, int(rewards["quant"]),
                    reason="faction_event", ref_id=str(event.id),
                )
            if rewards.get("xp"):
                character.xp_pool += int(rewards["xp"])
            participant.rewards_granted = True
            await stores.add_outbox(
                session,
                character_id=character.id,
                text=f"Фракционное событие «{event.name}» завершено успешно! Награды начислены (вклад: {participant.contribution}).",
            )
    else:
        for participant in participants:
            await stores.add_outbox(
                session,
                character_id=participant.character_id,
                text=f"Событие «{event.name}» завершено: цель не достигнута ({total}/{goal_count}).",
            )
    await session.flush()
