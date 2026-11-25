"""Use cases этапа 4: пошаговой бой.

ТЗ-критерии:
- базовый порядок: игрок, затем противник; эффекты могут менять порядок (этап 5+);
- при бездействии игрока ход противника НЕ исполняется автоматически (нет таймеров в бою);
- игрок не может пропустить ход добровольно (нет команды «пропустить»);
- в бою нельзя менять модули (смена только вне боя — проверка на этапе 5);
- игрок видит текущую прочность/эффекты противника, но не скрытые параметры;
- расходники за ход: лимит из config; ремкомплект лечит не в бою, а текущую прочность — нанопластырь;
- отступление: одна попытка; успех = противник исчезает, наград нет, возможны повреждение/поломка;
- опыт за противника только за победу; награды начисляются атомарно с победой;
- поражение = автовозврат + потеря доли добычи (переиспользуем логику поражения экспедиции).
"""
import random
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import characters, expeditions, stores
from app.locale import t
from app.models_game import (
    Battle,
    BattleTurn,
    Character,
    EnemyAbility,
    EnemyTemplate,
    Expedition,
    ItemTemplate,
    LootEntry,
    ShipTemplate,
)
from core.battle import (
    EnemyLoadout,
    apply_incoming_modifiers,
    enemy_choose_action,
    hp_bar,
    roll_damage,
    roll_loot,
    roll_retreat,
)
from core.errors import DomainError
from core.hull import max_hull


async def get_active_battle(session: AsyncSession, character_id: uuid.UUID) -> Battle | None:
    return await session.scalar(
        sa.select(Battle).where(Battle.character_id == character_id, Battle.status == "active")
    )


async def _load_enemy(session: AsyncSession, enemy_code: str) -> EnemyTemplate:
    template = await session.scalar(
        sa.select(EnemyTemplate).where(
            EnemyTemplate.code == enemy_code, EnemyTemplate.status == "published"
        )
    )
    if template is None:
        raise DomainError("unknown_enemy", f"Неизвестный противник: {enemy_code}")
    return template


async def _loadout(session: AsyncSession, template: EnemyTemplate) -> EnemyLoadout:
    stats = template.stats or {}
    abilities: list[dict[str, Any]] = []
    for code in template.abilities or []:
        ability = await session.scalar(
            sa.select(EnemyAbility).where(EnemyAbility.code == code)
        )
        if ability is not None:
            abilities.append({"code": ability.code, "name": ability.name, **ability.params})
    return EnemyLoadout(
        code=template.code,
        name=template.name,
        category=template.category,
        hull=int(stats.get("hull", 10)),
        damage=int(stats.get("damage", 5)),
        xp=int(stats.get("xp", 0)),
        abilities=abilities,
        passives=dict(template.passives or {}),
        weaknesses=dict(template.weaknesses or {}),
    )


async def start_battle(
    session: AsyncSession,
    character: Character,
    *,
    enemy_code: str,
    expedition: Expedition | None = None,
) -> Battle:
    if await get_active_battle(session, character.id) is not None:
        raise DomainError("battle_active", "Бой уже идёт.")
    template = await _load_enemy(session, enemy_code)
    loadout = await _loadout(session, template)

    ship = await expeditions.get_ship(session, character.id)
    ship_template = await session.get(ShipTemplate, ship.template_id)
    assert ship_template is not None

    from app.modules import effective_attack

    battle = Battle(
        character_id=character.id,
        expedition_id=expedition.id if expedition else None,
        enemy_template_id=template.id,
        enemy_snapshot={
            "code": loadout.code,
            "name": loadout.name,
            "category": loadout.category,
            "max_hull": loadout.hull,
            "passives": loadout.passives,
            "weaknesses": loadout.weaknesses,
        },
        player_snapshot={"attack": await effective_attack(session, character)},
        state={
            "enemy_hull": loadout.hull,
            "turn_no": 1,
            "cooldowns": {},
            "consumables_used_turn": 0,
        },
        status="active",
    )
    session.add(battle)
    ship.status = "battle"
    await session.flush()
    return battle


# --- раунд ---------------------------------------------------------------------


async def _write_turn(
    session: AsyncSession,
    battle: Battle,
    *,
    actor: str,
    action: dict[str, Any],
    summary: str,
    log: list[str],
) -> None:
    session.add(
        BattleTurn(
            battle_id=battle.id,
            turn_no=int(battle.state.get("turn_no", 1)),
            actor=actor,
            action=action,
            summary=summary,
            log=log,
        )
    )


async def _enemy_round(
    session: AsyncSession,
    character: Character,
    battle: Battle,
    loadout: EnemyLoadout,
    rng: random.Random,
    *,
    lang: str = "ru",
) -> str:
    """Ход противника; возвращает краткий итог."""
    state = battle.state
    turn_no = int(state.get("turn_no", 1))
    cooldowns: dict[str, int] = dict(state.get("cooldowns", {}))
    action = enemy_choose_action(loadout, cooldowns, turn_no, rng)

    spread = int(await stores.get_param(session, "combat.damage_spread_pct", 10))
    base = roll_damage(loadout.damage, rng, spread_pct=spread)
    damage = max(1, round(base * float(action.get("damage_mult", 1.0))))

    ship = await expeditions.get_ship(session, character.id)
    ship.current_hull = max(0, ship.current_hull - damage)

    if action["kind"] == "ability" and int(action.get("cooldown", 0)) > 0:
        cooldowns[action["code"]] = turn_no + int(action["cooldown"])
    state["cooldowns"] = cooldowns

    if action["kind"] == "ability":
        summary = t(
            "sum_enemy_ability", lang, name=loadout.name, ability=action.get("name", ""), damage=damage
        )
    else:
        summary = t("sum_enemy_attack", lang, name=loadout.name, damage=damage)
    await _write_turn(
        session, battle, actor="enemy", action=action, summary=summary,
        log=[f"базовый урон {base}", f"множитель {action.get('damage_mult', 1.0)}", f"итог {damage}"],
    )
    return summary


async def _finish_win(
    session: AsyncSession,
    character: Character,
    battle: Battle,
    loadout: EnemyLoadout,
    rng: random.Random,
) -> str:
    """Победа (ТЗ): награды атомарно, опыт только за победу."""
    lang = character.lang
    battle.status = "won"
    ship = await expeditions.get_ship(session, character.id)
    ship.status = "expedition" if battle.expedition_id else "dock"

    from app.quests import track

    await track(session, character, "battle_won", {"enemy": loadout.code})

    gained: list[str] = []
    template = await session.get(EnemyTemplate, battle.enemy_template_id)
    if template is not None and template.loot_table_id is not None:
        rows = (
            await session.execute(
                sa.select(LootEntry.qty_min, LootEntry.qty_max, LootEntry.weight, LootEntry.chance,
                          ItemTemplate.code)
                .join(ItemTemplate, ItemTemplate.id == LootEntry.item_template_id)
                .where(LootEntry.table_id == template.loot_table_id)
            )
        ).all()
        entries = [
            {"qty_min": r.qty_min, "qty_max": r.qty_max, "weight": r.weight,
             "chance": float(r.chance), "code": r.code}
            for r in rows
        ]
        for grant in roll_loot(entries, rng):
            given = await characters.grant_items(
                session, character, code=grant["code"], qty=grant["qty"], allow_loss=True
            )
            gained.append(f"{given}× {grant['code']}")

    character.xp_pool += loadout.xp
    rewards = [f"+{loadout.xp} опыта"] + gained
    ship_inv, inv_rows = await characters.list_inventory(session, character.id)
    free = (ship_inv.size_limit or 0) - len(inv_rows)
    final_message = "\n".join(
        [
            t("victory_title", lang),
            t("victory_reward", lang, list=", ".join(rewards)),
            t("victory_space", lang, free=max(0, free), size=ship_inv.size_limit or 0),
        ]
    )
    battle.state["outcome_message"] = final_message
    loot = t("sum_victory_loot", lang, list=", ".join(gained)) if gained else ""
    summary = t("sum_victory", lang, xp=loadout.xp, loot=loot)
    await _write_turn(
        session, battle, actor="player", action={"kind": "victory"},
        summary=summary, log=["награды начислены атомарно с победой"],
    )
    await session.flush()
    return summary


async def _finish_loss(
    session: AsyncSession,
    character: Character,
    battle: Battle,
    rng: random.Random,
) -> str:
    """Поражение (ТЗ): автовозврат, поломки, потеря доли добычи экспедиции."""
    lang = character.lang
    battle.status = "lost"
    repair_delay = int(await stores.get_param(session, "expedition.return_delay_sec", 30))
    battle.state["outcome_message"] = t("defeat_title", lang, delay=repair_delay)
    summary = t("sum_defeat", lang)
    await _write_turn(
        session, battle, actor="enemy", action={"kind": "defeat"},
        summary=summary, log=["прочность игрока достигла нуля"],
    )
    if battle.expedition_id is not None:
        expedition = await session.get(Expedition, battle.expedition_id)
        if expedition is not None and expedition.status == "active":
            await expeditions._apply_defeat(session, character, expedition, rng)
    await session.flush()
    return summary


async def _player_attack_round(
    session: AsyncSession,
    character: Character,
    battle: Battle,
    loadout: EnemyLoadout,
    rng: random.Random,
    *,
    lang: str = "ru",
) -> str:
    state = battle.state
    spread = int(await stores.get_param(session, "combat.damage_spread_pct", 10))
    base = roll_damage(int(battle.player_snapshot.get("attack", 5)), rng, spread_pct=spread)
    damage = apply_incoming_modifiers(base, loadout.passives, loadout.weaknesses)
    state["enemy_hull"] = max(0, int(state.get("enemy_hull", loadout.hull)) - damage)
    summary = t("sum_player_attack", lang, damage=damage)
    await _write_turn(
        session, battle, actor="player", action={"kind": "attack"}, summary=summary,
        log=[f"базовая атака {base}", f"после пассивов/слабостей {damage}"],
    )
    return summary


async def _next_turn(state: dict[str, Any]) -> None:
    state["turn_no"] = int(state.get("turn_no", 1)) + 1
    state["consumables_used_turn"] = 0


async def player_attack(
    session: AsyncSession, character: Character, *, rng: random.Random | None = None
) -> Battle:
    rng = rng or random.Random()
    battle = await _require_active_battle(session, character)
    loadout = await _battle_loadout(session, battle)

    parts = [await _player_attack_round(session, character, battle, loadout, rng, lang=character.lang)]
    if int(battle.state["enemy_hull"]) <= 0:
        parts.append(await _finish_win(session, character, battle, loadout, rng))
    else:
        parts.append(await _enemy_round(session, character, battle, loadout, rng, lang=character.lang))
        ship = await expeditions.get_ship(session, character.id)
        if ship.current_hull <= 0:
            parts.append(await _finish_loss(session, character, battle, rng))
        else:
            await _next_turn(battle.state)
    await session.flush()
    return battle


async def player_use_item(
    session: AsyncSession, character: Character, code: str, *, rng: random.Random | None = None
) -> Battle:
    rng = rng or random.Random()
    battle = await _require_active_battle(session, character)
    loadout = await _battle_loadout(session, battle)

    allowed = list(await stores.get_param(session, "combat.allowed_consumables", []))
    if code not in allowed:
        raise DomainError("not_allowed_in_combat", f"«{code}» нельзя использовать в бою.")
    state = battle.state
    limit = int(await stores.get_param(session, "combat.consumables_per_turn", 2))
    if int(state.get("consumables_used_turn", 0)) >= limit:
        raise DomainError("consumable_limit", f"Лимит расходников за ход: {limit}.")

    lang = character.lang
    template = await session.scalar(sa.select(ItemTemplate).where(ItemTemplate.code == code))
    if template is None:
        raise DomainError("unknown_item", f"Неизвестный предмет: {code}")
    removed = await characters.remove_items(session, character, code=code, qty=1)
    if removed == 0:
        raise DomainError("no_item", f"Нет «{code}» в инвентаре.")

    ship = await expeditions.get_ship(session, character.id)
    heal = int((template.params or {}).get("heal", 0))
    if heal > 0:
        cap = max_hull(ship.base_max_hull, ship.malfunctions)
        before = ship.current_hull
        ship.current_hull = min(cap, before + heal)
        summary = t("sum_use_heal", lang, name=template.name, healed=ship.current_hull - before)
    else:
        summary = t("sum_use_plain", lang, name=template.name)
    state["consumables_used_turn"] = int(state.get("consumables_used_turn", 0)) + 1
    await _write_turn(
        session, battle, actor="player", action={"kind": "use", "code": code},
        summary=summary, log=[f"heal={heal}"],
    )

    # расходник не отнимает ход: противник отвечает
    parts_enemy = await _enemy_round(session, character, battle, loadout, rng, lang=character.lang)
    ship = await expeditions.get_ship(session, character.id)
    if ship.current_hull <= 0:
        await _finish_loss(session, character, battle, rng)
    else:
        await _next_turn(state)
    battle.state["last_enemy_summary"] = parts_enemy
    await session.flush()
    return battle


async def player_retreat(
    session: AsyncSession, character: Character, *, rng: random.Random | None = None
) -> Battle:
    """Отступление (ТЗ): одна попытка за бой; успех = противник исчезает, наград нет."""
    rng = rng or random.Random()
    lang = character.lang
    battle = await _require_active_battle(session, character)
    if battle.retreat_used:
        raise DomainError("retreat_spent", "Попытка отступления уже израсходована.")
    loadout = await _battle_loadout(session, battle)

    result = roll_retreat(
        chance_pct=int(await stores.get_param(session, "combat.retreat_chance_pct", 60)),
        damage_chance_pct=int(
            await stores.get_param(session, "combat.retreat_damage_chance_pct", 30)
        ),
        damage_amount=int(await stores.get_param(session, "combat.retreat_damage_amount", 10)),
        malfunction_chance_pct=int(
            await stores.get_param(session, "combat.retreat_malfunction_chance_pct", 10)
        ),
        rng=rng,
    )
    battle.retreat_used = True

    if not result.success:
        summary = t("sum_retreat_fail", lang)
        await _write_turn(
            session, battle, actor="player", action={"kind": "retreat", "success": False},
            summary=summary, log=["попытка отступления израсходована"],
        )
        enemy_summary = await _enemy_round(session, character, battle, loadout, rng, lang=character.lang)
        ship = await expeditions.get_ship(session, character.id)
        if ship.current_hull <= 0:
            await _finish_loss(session, character, battle, rng)
        else:
            await _next_turn(battle.state)
        battle.state["last_enemy_summary"] = enemy_summary
        await session.flush()
        return battle

    battle.status = "retreated"
    ship = await expeditions.get_ship(session, character.id)
    notes = [t("sum_retreat_ok", lang)]
    if result.damage > 0:
        ship.current_hull = max(1, ship.current_hull - result.damage)
        notes.append(t("sum_retreat_damage", lang, damage=result.damage))
    if result.malfunction:
        ship.malfunctions += 1
        notes.append(t("sum_retreat_mal", lang))
    ship.status = "expedition" if battle.expedition_id else "dock"
    await _write_turn(
        session, battle, actor="player", action={"kind": "retreat", "success": True},
        summary=" ".join(notes), log=notes,
    )
    await session.flush()
    return battle


async def player_ability(
    session: AsyncSession, character: Character, combat_cmd: str, *, rng: random.Random | None = None
) -> Battle:
    """Активная способность установленного модуля (ТЗ): множитель урона, кулдаун."""
    rng = rng or random.Random()
    from app.modules import ability_for_cmd

    battle = await _require_active_battle(session, character)
    ability = await ability_for_cmd(session, character, combat_cmd)
    if ability is None:
        raise DomainError("no_ability", f"Нет установленной способности «{combat_cmd}».")
    loadout = await _battle_loadout(session, battle)
    state = battle.state
    turn_no = int(state.get("turn_no", 1))
    cooldowns: dict[str, int] = dict(state.get("player_cooldowns", {}))
    ready_at = int(cooldowns.get(str(ability.get("combat_cmd")), 0))
    if ready_at > turn_no:
        raise DomainError(
            "on_cooldown", f"Способность «{ability.get('name', combat_cmd)}» на кулдауне."
        )

    spread = int(await stores.get_param(session, "combat.damage_spread_pct", 10))
    base = roll_damage(int(battle.player_snapshot.get("attack", 5)), rng, spread_pct=spread)
    damage = apply_incoming_modifiers(
        max(1, round(base * float(ability.get("damage_mult", 1.0)))),
        loadout.passives,
        loadout.weaknesses,
    )
    state["enemy_hull"] = max(0, int(state.get("enemy_hull", loadout.hull)) - damage)
    cooldown = int(ability.get("cooldown", 0))
    if cooldown > 0:
        cooldowns[str(ability.get("combat_cmd"))] = turn_no + cooldown
    state["player_cooldowns"] = cooldowns

    summary = f"«{ability.get('name', combat_cmd)}»: -{damage} прочности противнику."
    await _write_turn(
        session, battle, actor="player",
        action={"kind": "ability", "code": ability.get("combat_cmd")},
        summary=summary, log=[f"база {base}", f"множитель {ability.get('damage_mult', 1.0)}"],
    )
    if int(state["enemy_hull"]) <= 0:
        await _finish_win(session, character, battle, loadout, rng)
    else:
        await _enemy_round(session, character, battle, loadout, rng, lang=character.lang)
        ship = await expeditions.get_ship(session, character.id)
        if ship.current_hull <= 0:
            await _finish_loss(session, character, battle, rng)
        else:
            await _next_turn(state)
    await session.flush()
    return battle


async def _require_active_battle(session: AsyncSession, character: Character) -> Battle:
    battle = await get_active_battle(session, character.id)
    if battle is None:
        raise DomainError("no_battle", "Нет активного боя.")
    return battle


async def _battle_loadout(session: AsyncSession, battle: Battle) -> EnemyLoadout:
    template = await session.get(EnemyTemplate, battle.enemy_template_id)
    assert template is not None
    return await _loadout(session, template)


# --- представление --------------------------------------------------------------


async def _hp_block(session: AsyncSession, battle: Battle, lang: str) -> str:
    """HP игрока и противника с графическими полосами (порядок: игрок, противник)."""
    snap = battle.enemy_snapshot
    state = battle.state
    ship = await expeditions.get_ship(session, battle.character_id)
    cap = max_hull(ship.base_max_hull, ship.malfunctions)
    bar_p, pct_p = hp_bar(ship.current_hull, cap)
    bar_e, pct_e = hp_bar(int(state.get("enemy_hull", 0)), int(snap.get("max_hull", 0)))
    return "\n".join(
        [
            t("hp_player", lang, cur=ship.current_hull, max=cap),
            f"{bar_p} {pct_p}%",
            t("hp_enemy", lang, cur=int(state.get("enemy_hull", 0)), max=int(snap.get("max_hull", 0))),
            f"{bar_e} {pct_e}%",
        ]
    )


async def describe_battle(session: AsyncSession, battle: Battle, *, lang: str = "ru") -> str:
    """Вступительный экран боя: характеристики сторон. Это ещё не ход 1 —
    бой начинается с первого действия игрока."""
    snap = battle.enemy_snapshot
    passives = snap.get("passives") or {}
    weaknesses = snap.get("weaknesses") or {}
    lines = [
        t("intro_fight", lang, name=snap.get("name", "?"), category=snap.get("category", "?")),
    ]
    if passives:
        lines.append(t("battle_passives", lang, list=", ".join(passives)))
    if weaknesses:
        lines.append(t("battle_weaknesses", lang, list=", ".join(weaknesses)))
    lines.append(await _hp_block(session, battle, lang))
    return "\n".join(lines)


async def describe_round_result(session: AsyncSession, battle: Battle, *, lang: str = "ru") -> str:
    """Итог раунда: сначала действия (сводки журнала), ниже — HP сторон и номер хода.
    Без заголовка боя, без подсказок отступления/команд."""
    # только раунд, который только что прошёл (2 строки: игрок + противник)
    turns = await battle_journal(session, battle, limit=2)
    lines = [f"[{tr.actor}] {tr.summary}" for tr in turns]
    lines.append(await _hp_block(session, battle, lang))
    if battle.status == "active":
        lines.append(t("battle_turn", lang, n=int(battle.state.get("turn_no", 1))))
    return "\n".join(lines)


async def describe_battle_outcome(session: AsyncSession, battle: Battle, lang: str) -> str:
    """Отдельное финальное сообщение: победа (награда + место) или поражение (ремонт)."""
    outcome = battle.state.get("outcome_message")
    if outcome:
        return str(outcome)
    return await describe_round_result(session, battle, lang=lang)


async def battle_journal(session: AsyncSession, battle: Battle, *, limit: int = 5) -> list[BattleTurn]:
    """Подробный журнал по запросу (ТЗ) — последние ходы."""
    return list(
        (
            await session.scalars(
                sa.select(BattleTurn)
                .where(BattleTurn.battle_id == battle.id)
                .order_by(BattleTurn.id.desc())
                .limit(limit)
            )
        ).all()
    )[::-1]
