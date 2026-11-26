"""Веб-интерфейс админ-панели: /admin-panel.

Ключ вводится на странице и хранится в localStorage; каждый запрос несёт
X-Admin-Key — сервер проверяет роль и либо применяет изменение, либо отклоняет.
UI подсвечивает разделы, недоступные роли ключа, но сервер — окончательный судья.
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["admin-frontend"])

PAGE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Админ-панель · Battle for the Galaxy</title>
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; background: #0b1020; color: #e7ecf5; min-height: 100vh; }
header { padding: 16px 20px; background: #101736; border-bottom: 1px solid #232c4d; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
header h1 { font-size: 18px; margin-right: auto; }
.keybox { display: flex; gap: 8px; align-items: center; }
input, select, textarea { background: #141b31; border: 1px solid #2a3560; color: #e7ecf5; border-radius: 8px; padding: 9px 12px; font-size: 14px; }
input:focus, select:focus { outline: none; border-color: #4d7cff; }
button { background: linear-gradient(90deg,#4d7cff,#9b5bff); color: #fff; border: none; border-radius: 8px; padding: 9px 16px; font-size: 14px; font-weight: 600; cursor: pointer; }
button.ghost { background: #1b2444; border: 1px solid #2a3560; }
button:disabled { opacity: .35; cursor: not-allowed; }
.rolebadge { font-size: 12px; padding: 4px 10px; border-radius: 20px; background: #223; border: 1px solid #35406e; }
.role-admin { color: #ffd479; } .role-moderator { color: #7ad0ff; } .role-content_manager { color: #9dff9d; }
nav { display: flex; gap: 6px; padding: 12px 20px 0; flex-wrap: wrap; }
nav button { border-radius: 10px 10px 0 0; background: #131a33; border: 1px solid #232c4d; border-bottom: none; }
nav button.active { background: #182142; color: #9fb4ff; }
main { padding: 20px; max-width: 860px; }
section { display: none; }
section.active { display: block; }
.card { background: #141b31; border: 1px solid #232c4d; border-radius: 12px; padding: 16px; margin-bottom: 14px; }
.card h2 { font-size: 13px; color: #9fb4ff; text-transform: uppercase; letter-spacing: .6px; margin-bottom: 12px; }
.formrow { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.formrow > * { flex: 1 1 140px; }
.result { background: #0d1330; border: 1px solid #232c4d; border-radius: 8px; padding: 12px; font-size: 13px; white-space: pre-wrap; word-break: break-all; margin-top: 10px; max-height: 320px; overflow: auto; }
.result.ok { border-color: #2f6f4f; } .result.err { border-color: #7a3030; color: #ff9d9d; }
.hit { display: flex; gap: 10px; align-items: center; padding: 8px 0; border-bottom: 1px dashed #1d2745; font-size: 14px; flex-wrap: wrap; }
.hit:last-child { border-bottom: none; }
.hit .id { color: #8b96ad; font-size: 12px; }
.mono { font-family: ui-monospace, Consolas, monospace; }
.note { color: #8b96ad; font-size: 13px; margin-top: 6px; }
.lbl { flex: 1 1 240px; color: #9fb4ff; font-size: 13px; align-self: center; }
</style>
</head>
<body>
<header>
  <h1>⚙ Админ-панель</h1>
  <span id="role" class="rolebadge">нет ключа</span>
  <div class="keybox">
    <input id="key" type="password" placeholder="API-ключ (X-Admin-Key)" style="width:260px">
    <button onclick="saveKey()" class="ghost">Применить ключ</button>
  </div>
</header>
<nav id="tabs"></nav>
<main>
  <section data-tab="config"><div class="card"><h2>Конфигурация баланса (hot reload)</h2>
    <div class="formrow"><input id="cfg-key" placeholder="ключ, напр. expedition.return_delay_sec"><button class="ghost" onclick="cfgGet()">Прочитать</button></div>
    <div class="formrow"><textarea id="cfg-value" rows="2" placeholder='значение (JSON), напр. 45 или "text"'></textarea></div>
    <button onclick="cfgSet()">Сохранить</button>
    <div class="note">Примеры ключей: inventory.base_size, combat.retreat_chance_pct, market.reference_price_quant, auction.commission_pct, module.level_thresholds</div>
  </div>
  <div id="config-sections"></div></section>

  <section data-tab="moderation"><div class="card"><h2>Модерация игроков</h2>
    <div class="formrow"><input id="find-name" placeholder="имя персонажа"><button class="ghost" onclick="findChar()">Найти</button></div>
    <div id="hits"></div>
  </div>
  <div class="card" id="ban-card" style="display:none"><h2>Действия по выбранному игроку</h2>
    <div class="note" id="chosen"></div>
    <div class="formrow" style="margin-top:8px">
      <input id="ban-reason" placeholder="причина бана">
      <input id="ban-hours" type="number" placeholder="часы (пусто = навсегда)" min="1">
    </div>
    <div class="formrow">
      <button onclick="banChar()">Забанить</button>
      <button class="ghost" onclick="unbanChar()">Разбанить</button>
    </div>
  </div>
  <div class="card"><h2>Эффекты (белый список)</h2>
    <div class="formrow">
      <select id="effect-code"></select>
      <input id="effect-params" placeholder='параметры JSON, напр. {"power": 2}'>
    </div>
    <button onclick="applyEffect()">Наложить эффект</button>
    <div class="note">Сначала найдите игрока — эффект применится к выбранному.</div>
  </div></section>

  <section data-tab="content"><div class="card"><h2>Публикация контента (зона контент-менеджера)</h2>
    <div class="formrow">
      <select id="ct-table">
        <option value="event_templates">event_templates (события)</option>
        <option value="quest_templates">quest_templates (задания)</option>
        <option value="recipe_templates">recipe_templates (рецепты)</option>
      </select>
      <input id="ct-code" placeholder="код, напр. quiet_drift">
      <select id="ct-status">
        <option value="published">published</option>
        <option value="draft">draft</option>
        <option value="archived">archived</option>
      </select>
    </div>
    <button onclick="setContent()">Применить статус</button>
  </div>
  <div class="card"><h2>Изменить или удалить контент</h2>
    <div class="formrow">
      <select id="ce-kind" onchange="contentEditSelect()" title="Тип контента">
        <option value="event">Событие</option>
        <option value="quest">Задание</option>
        <option value="recipe">Рецепт</option>
        <option value="ship">Корабль</option>
      </select>
      <select id="ce-code" onchange="contentEditFill()" title="Контент"></select>
    </div>
    <div id="ce-form"></div>
    <div class="formrow" id="ce-buttons" style="display:none">
      <button class="ghost" onclick="contentCancel('edit')">Отменить изменения</button>
      <button class="ghost" onclick="contentDelete()">Удалить контент</button>
      <button onclick="contentSave('edit')">Сохранить изменения</button>
    </div>
  </div>
  <div class="card"><h2>Добавить контент</h2>
    <div class="formrow"><button class="ghost" onclick="contentAddStart()">Добавить</button></div>
    <div class="formrow" id="ca-kind-row" style="display:none">
      <select id="ca-kind" onchange="contentAddForm()" title="Тип контента">
        <option value="event">Событие</option>
        <option value="quest">Задание</option>
        <option value="recipe">Рецепт</option>
        <option value="ship">Корабль</option>
      </select>
    </div>
    <div id="ca-form"></div>
    <div class="formrow" id="ca-buttons" style="display:none">
      <button class="ghost" onclick="contentCancel('add')">Отменить изменения</button>
      <button onclick="contentSave('add')">Сохранить изменения</button>
    </div>
  </div></section>

  <section data-tab="items">
  <div class="card"><h2>Предметы</h2>
    <div class="formrow">
      <button class="ghost" onclick="itemsMode('edit')">Изменить параметры предмета</button>
      <button class="ghost" onclick="itemsMode('create')">Добавить новый предмет</button>
    </div>
    <div class="formrow" id="item-select-row" style="display:none">
      <select id="item-select" onchange="fillItemForm()"></select>
    </div>
  </div>
  <div class="card" id="item-form" style="display:none"><h2 id="item-form-title">Предмет</h2>
    <div class="formrow">
      <input id="it-code" placeholder="Код предмета (a-z, 0-9, _)">
      <input id="it-name" placeholder="Название предмета">
    </div>
    <div class="formrow"><textarea id="it-desc" rows="2" placeholder="Описание"></textarea></div>
    <div class="formrow">
      <select id="it-category" title="Тип предмета"></select>
      <select id="it-rarity" title="Редкость"></select>
      <input id="it-stack" type="number" min="1" placeholder="Лимит стека" title="Лимит стека">
    </div>
    <div class="formrow" style="align-items:center">
      <label><input type="checkbox" id="it-unique"> Уникальный</label>
      <label><input type="checkbox" id="it-sellable" checked> Продаваемый</label>
      <label><input type="checkbox" id="it-tradable" checked> Обмениваемый</label>
      <label><input type="checkbox" id="it-losable"> Теряется при поражении</label>
    </div>
    <div class="formrow">
      <input id="it-drop-weight" type="number" min="0" max="100" placeholder="Вероятность выпадения, %" title="Вероятность выпадения в экспедиции, %">
      <input id="it-qty-min" type="number" min="1" placeholder="Количество: от" title="Количество выпадаемых предметов: от">
      <input id="it-qty-max" type="number" min="1" placeholder="Количество: до" title="Количество выпадаемых предметов: до">
    </div>
    <div class="note">Вероятность (0–100%) и диапазон количества — бонусный дроп после события экспедиции; 0% — предмет не выпадает.</div>
    <div class="formrow" style="margin-top:8px"><input id="it-params" placeholder='Параметры JSON, напр. {"heal": 15}'></div>
    <div class="formrow">
      <button class="ghost" onclick="cancelItem()">Отменить</button>
      <button onclick="saveItem()">Сохранить</button>
    </div>
  </div></section>

  <section data-tab="tools">
  <div class="card"><h2>Проверка работоспособности</h2>
    <div class="formrow">
      <button class="ghost" onclick="healthCheck('vk')">Проверить ВК</button>
      <button class="ghost" onclick="healthCheck('tg')">Проверить TG</button>
      <button class="ghost" onclick="healthCheck('server')">Проверить сервер</button>
      <button onclick="healthCheck('all')">Проверить всё</button>
    </div>
    <div id="health-status" class="result">Загрузка…</div>
  </div>
  <div class="card"><h2>Расписание проверок</h2>
    <div class="formrow">
      <input id="health-interval" type="number" min="0" placeholder="Интервал, секунд (0 — выкл)" title="Интервал автоматической проверки, секунд">
      <button onclick="healthSchedule()">Задать расписание</button>
      <button class="ghost" onclick="loadHealth()">Обновить статус</button>
    </div>
    <div class="note">Плановые проверки выполняет worker. Результат последней проверки (дата и время МСК + статусы) — в блоке выше.</div>
  </div></section>

  <section data-tab="analytics">
  <div class="card"><h2>Нагрузка на сервер</h2>
    <button class="ghost" onclick="toggleSection('srvload', loadServerLoad, this)">Показать</button>
    <div id="srvload" class="result" style="display:none"></div>
  </div>
  <div class="card"><h2>Аналитика игрового мира</h2>
    <button class="ghost" onclick="toggleSection('world', loadWorldAnalytics, this)">Показать</button>
    <div id="world" class="result" style="display:none"></div>
  </div>
  <div class="card"><h2>Журнал аудита</h2>
    <button class="ghost" onclick="toggleSection('auditlog', loadAuditLog, this)">Показать</button>
    <div id="auditlog" class="result" style="display:none"></div>
    <div class="note">Последние 50 действий администрации. Время — московское.</div>
  </div></section>
</main>
<div id="out"></div>

<script>
const TABS = [
  ["config", "Конфигурация", ["admin"]],
  ["moderation", "Модерация", ["admin", "moderator"]],
  ["content", "Контент", ["admin", "content_manager"]],
  ["items", "Предметы", ["admin"]],
  ["analytics", "Аналитика", ["admin"]],
  ["tools", "Инструменты", ["admin"]],
];
let ROLE = null, CHOSEN = null;
const $ = (id) => document.getElementById(id);
const KEY = () => $("key").value.trim();
const H = () => ({ "X-Admin-Key": KEY(), "Content-Type": "application/json" });

function out(text, ok) {
  const el = document.createElement("div");
  el.className = "result " + (ok ? "ok" : "err");
  el.textContent = typeof text === "string" ? text : JSON.stringify(text, null, 2);
  $("out").prepend(el);
}
async function api(method, path, body, silent) {
  const r = await fetch(path, { method, headers: H(), body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = data.detail || ("HTTP " + r.status);
    if (!silent) out(msg, false);
    throw new Error(msg);
  }
  return data;
}
function renderTabs() {
  $("tabs").innerHTML = "";
  TABS.forEach(([id, label, roles]) => {
    const b = document.createElement("button");
    b.textContent = label;
    const allowed = ROLE && roles.includes(ROLE);
    b.disabled = !allowed;
    b.title = allowed ? "" : "Недоступно для роли: " + ROLE;
    b.onclick = () => {
      document.querySelectorAll("nav button").forEach(x => x.classList.remove("active"));
      document.querySelectorAll("section").forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      document.querySelector('section[data-tab="' + id + '"]').classList.add("active");
    };
    $("tabs").appendChild(b);
  });
  const first = TABS.find(t => ROLE && t[2].includes(ROLE));
  if (first) $("tabs").children[TABS.indexOf(first)].click();
}
async function saveKey() {
  localStorage.setItem("admin-key", KEY());
  try {
    const me = await api("GET", "/admin/whoami");
    ROLE = me.role;
    $("role").textContent = me.login + " · " + me.role;
    $("role").className = "rolebadge role-" + me.role;
    renderTabs();
    out("Ключ принят: " + me.login + " (" + me.role + ")", true);
    loadEffects();
    if (me.role === "admin") { loadItems(); loadHealth(); loadConfigSections(); }
    if (me.role === "admin" || me.role === "content_manager") loadContentList();
  } catch (e) { ROLE = null; $("role").textContent = "ключ не принят"; renderTabs(); }
}
async function loadEffects() {
  try {
    const d = await api("GET", "/admin/effects");
    $("effect-code").innerHTML = d.effects.map(e => '<option value="' + e.code + '">' + e.code + "</option>").join("");
  } catch (e) {}
}
async function cfgGet() { out(await api("GET", "/admin/config/" + $("cfg-key").value.trim()), true); }
// --- структурированные вкладки конфигурации ---------------------------------------
let CONFIG_SECTIONS = [], OPEN_SECTIONS = new Set();
function csId(sid, key) { return "cs-" + sid + "-" + key.replace(/\./g, "_"); }
async function loadConfigSections() {
  try {
    const d = await api("GET", "/admin/config-sections");
    CONFIG_SECTIONS = d.sections;
    renderConfigSections();
  } catch (e) {}
}
function renderConfigSections() {
  const root = $("config-sections");
  root.innerHTML = "";
  CONFIG_SECTIONS.forEach(s => {
    const card = document.createElement("div");
    card.className = "card";
    let fields = "";
    s.fields.forEach(f => {
      const id = csId(s.id, f.key);
      if (f.type === "json") {
        fields += '<div class="formrow"><span class="lbl">' + f.label + '</span><textarea id="' + id + '" rows="2" class="mono" style="flex:2">' + (f.value === null ? "" : JSON.stringify(f.value)) + "</textarea></div>";
      } else {
        fields += '<div class="formrow"><span class="lbl">' + f.label + '</span><input id="' + id + '" type="' + (f.type === "str" ? "text" : "number") + '" value="' + (f.value === null ? "" : f.value) + '" style="flex:1"></div>';
      }
    });
    const open = OPEN_SECTIONS.has(s.id);
    card.innerHTML =
      '<div class="formrow" style="align-items:center;margin-bottom:0">' +
      '<h2 style="flex:3;margin:0">' + s.title + ' <span class="note">(' + s.fields.length + ")</span></h2>" +
      '<button class="ghost" style="flex:0 0 auto" onclick="toggleCard(this, \'' + s.id + '\')">' + (open ? "Скрыть" : "Показать") + "</button></div>" +
      '<div class="card-body" style="display:' + (open ? "block" : "none") + ';margin-top:12px">' + fields +
      '<div class="formrow"><button class="ghost" onclick="configSectionCancel()">Отменить изменения</button>' +
      '<button onclick="configSectionSave(\'' + s.id + '\')">Сохранить изменения</button></div></div>';
    root.appendChild(card);
  });
}
function toggleCard(btn, sid) {
  const body = btn.closest(".card").querySelector(".card-body");
  const hidden = body.style.display === "none";
  body.style.display = hidden ? "block" : "none";
  btn.textContent = hidden ? "Скрыть" : "Показать";
  if (hidden) OPEN_SECTIONS.add(sid); else OPEN_SECTIONS.delete(sid);
}
function configSectionCancel() {
  renderConfigSections();
  out("Изменения отменены", true);
}
async function configSectionSave(sid) {
  const s = CONFIG_SECTIONS.find(x => x.id === sid);
  const values = {};
  for (const f of s.fields) values[f.key] = $(csId(sid, f.key)).value.trim();
  try {
    const d = await api("PUT", "/admin/config-sections/" + sid, { values }, true);
    CONFIG_SECTIONS = d.sections;
    renderConfigSections();
    out("Контент сохранен", true);
  } catch (e) {
    out("Ошибка при сохранении. Некорректные значения полей. " + e.message, false);
  }
}
async function cfgSet() {
  let v = $("cfg-value").value.trim();
  try { v = JSON.parse(v); } catch (e) {}
  out(await api("PUT", "/admin/config/" + $("cfg-key").value.trim(), { value: v }), true);
}
async function findChar() {
  const d = await api("GET", "/admin/lookup?name=" + encodeURIComponent($("find-name").value.trim()));
  $("hits").innerHTML = d.results.map(r =>
    '<div class="hit"><b>' + r.name + '</b><span class="id mono">' + r.character_id + '</span>' +
    '<button class="ghost" onclick="choose(\'' + r.character_id + '\',\'' + r.account_id + '\',\'' + r.name + '\')">Выбрать</button></div>'
  ).join("") || '<div class="note">Никого не найдено.</div>';
}
function choose(cid, aid, name) {
  CHOSEN = { cid, aid, name };
  $("ban-card").style.display = "block";
  $("chosen").textContent = name + " · character_id: " + cid;
}
async function banChar() {
  await api("POST", "/admin/accounts/" + CHOSEN.aid + "/ban", {
    reason: $("ban-reason").value || "нарушение",
    hours: $("ban-hours").value ? Number($("ban-hours").value) : null,
  });
  out("Забанен: " + CHOSEN.name, true);
}
async function unbanChar() {
  await api("POST", "/admin/accounts/" + CHOSEN.aid + "/unban");
  out("Разбанен: " + CHOSEN.name, true);
}
async function applyEffect() {
  if (!CHOSEN) { out("Сначала найдите и выберите игрока", false); return; }
  let params = {};
  try { params = $("effect-params").value.trim() ? JSON.parse($("effect-params").value) : {}; } catch (e) {}
  await api("POST", "/admin/characters/" + CHOSEN.cid + "/effects", {
    code: $("effect-code").value, params,
  });
  out("Эффект " + $("effect-code").value + " наложен на " + CHOSEN.name, true);
}
async function setContent() {
  out(await api("POST", "/admin/content/" + $("ct-table").value + "/" + $("ct-code").value.trim() + "/status", { status: $("ct-status").value }), true);
}
// --- редактор контента: события / задания / рецепты ------------------------------
const CONTENT_FIELDS = {
  event: [
    ["code", "Код (a-z, 0-9, _)", "text"],
    ["name", "Название", "text"],
    ["description", "Описание", "textarea"],
    ["kind", "Тип события", "select", ["automatic", "interactive"]],
    ["fuel_cost", "Стоимость топлива", "number"],
    ["danger_min", "Опасность: от (1-10)", "number"],
    ["danger_max", "Опасность: до (1-10)", "number"],
    ["can_refuse", "Можно отказаться", "checkbox"],
    ["is_unique_per_expedition", "Уникальное за экспедицию", "checkbox"],
    ["status", "Статус", "select", ["published", "draft", "archived"]],
    ["script", 'Сценарий JSON: {"choices": [...]} для interactive, {"variants": [...]} для automatic', "json"],
  ],
  quest: [
    ["code", "Код (a-z, 0-9, _)", "text"],
    ["name", "Название", "text"],
    ["description", "Описание", "textarea"],
    ["kind", "Тип задания", "select", ["tutorial", "story"]],
    ["chain_order", "Порядок в цепочке", "number"],
    ["objectives", 'Цели JSON: [{"type": "event_resolved", "count": 2}]', "json"],
    ["rewards", 'Награды JSON: {"quant": 100, "xp": 10, "items": [{"code": "...", "qty": 1}]}', "json"],
    ["status", "Статус", "select", ["published", "draft", "archived"]],
  ],
  recipe: [
    ["code", "Код (a-z, 0-9, _)", "text"],
    ["name", "Название", "text"],
    ["result_item_code", "Код предмета результата", "text"],
    ["result_qty", "Количество результата", "number"],
    ["materials", 'Материалы JSON: [{"code": "scrap_metal", "qty": 3}]', "json"],
    ["duration_sec", "Длительность крафта, сек", "number"],
    ["cancel_return_pct", "Возврат материалов при отмене, %", "number"],
    ["status", "Статус", "select", ["published", "draft", "archived"]],
  ],
  ship: [
    ["code", "Код (a-z, 0-9, _)", "text"],
    ["name", "Название", "text"],
    ["faction", "Фракция", "faction"],
    ["max_hull", "Прочность корпуса", "number"],
    ["fuel_capacity", "Вместимость топлива", "number"],
    ["attack", "Атака", "number"],
    ["defense", "Защита", "number"],
    ["module_slots", "Слотов под модули", "number"],
  ],
};
const CONTENT_DEFAULTS = {
  event: { kind: "automatic", fuel_cost: 1, danger_min: 1, danger_max: 1, can_refuse: true, is_unique_per_expedition: false, status: "published", script: { variants: [{ weight: 1, outcome: { message: "..." } }] } },
  quest: { kind: "tutorial", chain_order: 0, objectives: [{ type: "event_resolved", count: 1 }], rewards: { quant: 100 }, status: "published" },
  recipe: { result_qty: 1, materials: [{ code: "scrap_metal", qty: 1 }], duration_sec: 60, cancel_return_pct: 50, status: "published" },
  ship: { faction: "", max_hull: 100, fuel_capacity: 50, attack: 5, defense: 5, module_slots: 3 },
};
let CONTENT = null;
async function loadContentList() {
  try {
    CONTENT = await api("GET", "/admin/content");
    contentEditSelect();
  } catch (e) {}
}
function contentEditSelect() {
  const kind = $("ce-kind").value;
  const list = CONTENT ? (CONTENT[kind + "s"] || []) : [];
  $("ce-code").innerHTML = list.map(i => '<option value="' + i.code + '">' + i.name + " (" + i.code + ")</option>").join("");
  contentEditFill();
}
function contentEditFill() {
  const kind = $("ce-kind").value;
  const item = CONTENT ? (CONTENT[kind + "s"] || []).find(x => x.code === $("ce-code").value) : null;
  if (!item) { $("ce-form").innerHTML = '<div class="note">Контента этого типа нет.</div>'; $("ce-buttons").style.display = "none"; return; }
  renderContentForm("ce-form", "ce", kind, item);
  $("ce-buttons").style.display = "flex";
}
function contentAddStart() {
  $("ca-kind-row").style.display = "flex";
  contentAddForm();
}
function contentAddForm() {
  const kind = $("ca-kind").value;
  renderContentForm("ca-form", "ca", kind, CONTENT_DEFAULTS[kind]);
  $("ca-buttons").style.display = "flex";
}
function renderContentForm(containerId, prefix, kind, item) {
  const html = CONTENT_FIELDS[kind].map(([key, label, type, options]) => {
    const id = prefix + "-" + key;
    const value = item[key];
    if (type === "checkbox") {
      return '<label style="display:block;margin:6px 0"><input type="checkbox" id="' + id + '"' + (value ? " checked" : "") + "> " + label + "</label>";
    }
    if (type === "select") {
      return '<div class="formrow"><select id="' + id + '" title="' + label + '">' +
        options.map(o => '<option value="' + o + '"' + (o === value ? " selected" : "") + ">" + o + "</option>").join("") +
        '</select><span class="note" style="flex:2">' + label + "</span></div>";
    }
    if (type === "faction") {
      const factions = (CONTENT && CONTENT.factions) || [];
      const opts = '<option value="">— без фракции —</option>' + factions.map(f =>
        '<option value="' + f.code + '"' + (f.code === value ? " selected" : "") + ">" + f.name + " (" + f.code + ")</option>"
      ).join("");
      return '<div class="formrow"><select id="' + id + '" title="' + label + '">' + opts + '</select><span class="note" style="flex:2">' + label + "</span></div>";
    }
    if (type === "textarea") {
      return '<div class="formrow"><textarea id="' + id + '" rows="2" placeholder="' + label + '">' + (value || "") + "</textarea></div>";
    }
    if (type === "json") {
      const text = value === undefined ? "" : JSON.stringify(value);
      return '<div class="formrow"><textarea id="' + id + '" rows="3" class="mono" placeholder="' + label.replace(/"/g, "&quot;") + '">' + text + "</textarea></div>";
    }
    const val = value === undefined || value === null ? "" : value;
    return '<div class="formrow"><input id="' + id + '" type="' + (type === "number" ? "number" : "text") + '" value="' + val + '" placeholder="' + label + '" title="' + label + '"' + (key === "code" && prefix === "ce" ? " disabled" : "") + "></div>";
  }).join("");
  $(containerId).innerHTML = html;
}
function collectContentForm(prefix, kind) {
  const data = {};
  for (const [key, label, type] of CONTENT_FIELDS[kind]) {
    const el = $(prefix + "-" + key);
    if (type === "checkbox") { data[key] = el.checked; continue; }
    if (type === "number") {
      if (el.value.trim() === "") throw new Error("поле «" + label + "» не заполнено");
      data[key] = Number(el.value);
      continue;
    }
    if (type === "json") {
      const raw = el.value.trim();
      if (!raw) { data[key] = CONTENT_FIELDS[kind].find(f => f[0] === key)[2] === "json" ? (Array.isArray(CONTENT_DEFAULTS[kind][key]) ? [] : {}) : {}; continue; }
      try { data[key] = JSON.parse(raw); } catch (e) { throw new Error("поле «" + label + "»: некорректный JSON"); }
      continue;
    }
    data[key] = el.value.trim();
  }
  return data;
}
async function contentSave(mode) {
  const kind = $(mode === "edit" ? "ce-kind" : "ca-kind").value;
  let body;
  try { body = collectContentForm(mode === "edit" ? "ce" : "ca", kind); }
  catch (e) { out("Ошибка при сохранении. Некорректные значения полей. " + e.message, false); return; }
  try {
    if (mode === "edit") await api("PUT", "/admin/content/" + kind + "/" + encodeURIComponent(body.code), body, true);
    else await api("POST", "/admin/content/" + kind, body, true);
    out("Контент сохранен", true);
    await loadContentList();
    if (mode === "edit") { $("ce-code").value = body.code; contentEditFill(); }
  } catch (e) { out("Ошибка при сохранении. Некорректные значения полей. " + e.message, false); }
}
async function contentDelete() {
  const kind = $("ce-kind").value, code = $("ce-code").value;
  if (!code) { out("Контент не выбран", false); return; }
  try {
    await api("DELETE", "/admin/content/" + kind + "/" + encodeURIComponent(code), null, true);
    out("Контент удален", true);
    await loadContentList();
  } catch (e) { out("Ошибка: " + e.message, false); }
}
function contentCancel(mode) {
  if (mode === "edit") {
    $("ce-form").innerHTML = "";
    $("ce-buttons").style.display = "none";
  } else {
    $("ca-form").innerHTML = "";
    $("ca-buttons").style.display = "none";
    $("ca-kind-row").style.display = "none";
  }
  out("Изменения отменены", true);
}
// --- предметы -----------------------------------------------------------------
let ITEM_MODE = null, ITEMS = [];
async function loadItems() {
  try {
    const d = await api("GET", "/admin/items");
    ITEMS = d.items;
    $("item-select").innerHTML = ITEMS.map(i => '<option value="' + i.code + '">' + i.name + " (" + i.code + ")</option>").join("");
    $("it-category").innerHTML = d.categories.map(c => '<option value="' + c + '">' + c + "</option>").join("");
    $("it-rarity").innerHTML = d.rarities.map(r => '<option value="' + r + '">' + r + "</option>").join("");
  } catch (e) {}
}
function itemsMode(mode) {
  ITEM_MODE = mode;
  $("item-select-row").style.display = mode === "edit" ? "flex" : "none";
  $("item-form").style.display = "block";
  $("item-form-title").textContent = mode === "edit" ? "Изменить параметры предмета" : "Новый предмет";
  $("it-code").disabled = mode === "edit";
  if (mode === "edit") fillItemForm(); else clearItemForm();
}
function clearItemForm() {
  ["it-code", "it-name", "it-desc", "it-params"].forEach(id => { $(id).value = ""; });
  $("it-category").selectedIndex = 0;
  $("it-rarity").selectedIndex = 0;
  $("it-stack").value = 1;
  $("it-unique").checked = false;
  $("it-sellable").checked = true;
  $("it-tradable").checked = true;
  $("it-losable").checked = false;
  $("it-drop-weight").value = 0;
  $("it-qty-min").value = 1;
  $("it-qty-max").value = 1;
}
function fillItemForm() {
  const it = ITEMS.find(x => x.code === $("item-select").value);
  if (!it) return;
  $("it-code").value = it.code;
  $("it-name").value = it.name;
  $("it-desc").value = it.description;
  $("it-category").value = it.category;
  $("it-rarity").value = it.rarity;
  $("it-stack").value = it.stack_limit;
  $("it-unique").checked = it.is_unique;
  $("it-sellable").checked = it.sellable;
  $("it-tradable").checked = it.tradable;
  $("it-losable").checked = it.losable_on_defeat;
  $("it-drop-weight").value = it.drop_weight;
  $("it-qty-min").value = it.drop_qty_min;
  $("it-qty-max").value = it.drop_qty_max;
  $("it-params").value = Object.keys(it.params || {}).length ? JSON.stringify(it.params) : "";
}
function collectItemForm() {
  let params = {};
  const raw = $("it-params").value.trim();
  if (raw) {
    try { params = JSON.parse(raw); } catch (e) { throw new Error("параметры: некорректный JSON"); }
  }
  return {
    code: $("it-code").value.trim(),
    name: $("it-name").value.trim(),
    description: $("it-desc").value.trim(),
    category: $("it-category").value,
    rarity: $("it-rarity").value,
    stack_limit: Number($("it-stack").value || 1),
    is_unique: $("it-unique").checked,
    sellable: $("it-sellable").checked,
    tradable: $("it-tradable").checked,
    losable_on_defeat: $("it-losable").checked,
    drop_weight: Number($("it-drop-weight").value || 0),
    drop_qty_min: Number($("it-qty-min").value || 1),
    drop_qty_max: Number($("it-qty-max").value || 1),
    params,
  };
}
async function saveItem() {
  let body;
  try { body = collectItemForm(); } catch (e) { out("Ошибка: " + e.message, false); return; }
  try {
    if (ITEM_MODE === "create") await api("POST", "/admin/items", body, true);
    else await api("PUT", "/admin/items/" + encodeURIComponent(body.code), body, true);
    out("Успешное сохранение предмета", true);
    await loadItems();
    if (ITEM_MODE === "edit") $("item-select").value = body.code;
  } catch (e) { out("Ошибка: " + e.message, false); }
}
function cancelItem() {
  $("item-form").style.display = "none";
  $("item-select-row").style.display = "none";
  ITEM_MODE = null;
  out("Изменения отменены", true);
}
// --- аналитика: три раздела с кнопкой «Показать» --------------------------------
async function toggleSection(id, loader, btn) {
  const el = $(id);
  if (el.style.display === "none") {
    await loader();
    el.style.display = "block";
    btn.textContent = "Скрыть";
  } else {
    el.style.display = "none";
    btn.textContent = "Показать";
  }
}
function fmtUptime(sec) {
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return (h ? h + " ч " : "") + (m ? m + " мин " : "") + s + " с";
}
async function loadServerLoad() {
  const d = await api("GET", "/admin/server-load");
  $("srvload").textContent = [
    "Загрузка CPU процессом API: " + d.cpu_percent + "% (ядер на машине: " + d.cpu_cores + ")",
    "Память процесса API: " + (d.memory_mb === null ? "н/д" : d.memory_mb + " МБ"),
    "Аптайм API: " + fmtUptime(d.uptime_sec),
    "Размер базы данных: " + d.db_size_mb + " МБ",
    "Подключений к БД: " + d.db_connections,
    "Таймеров в очереди: " + d.timers_pending,
    "Сообщений outbox: ожидают " + d.outbox_pending + ", с ошибкой " + d.outbox_failed,
  ].join("\n");
}
async function loadWorldAnalytics() {
  const d = await api("GET", "/admin/analytics");
  const lines = [
    "Всего персонажей: " + d.total_characters,
    "Экспедиций за сутки: " + d.expeditions_today,
    "Боёв за сутки: " + d.battles_today,
    "Оборот рынка за сутки: " + d.market_volume_quant_today + " квантов",
    "Воронка обучения (завершили задание):",
  ];
  (d.tutorial_funnel || []).forEach(f => lines.push("  — «" + f.name + "»: " + f.completed));
  if (!(d.tutorial_funnel || []).length) lines.push("  — пока никто не завершил ни одного задания");
  $("world").textContent = lines.join("\n");
}
async function loadAuditLog() {
  const d = await api("GET", "/admin/audit-log");
  $("auditlog").textContent = (d.entries || [])
    .map(e => e.time_msk + " · " + e.actor + " · " + e.summary)
    .join("\n") || "Журнал пуст.";
}
// --- инструменты: проверка работоспособности ------------------------------------
const HEALTH_LABELS = { vk: "ВК", tg: "Telegram", server: "Сервер" };
function renderHealth(d) {
  const lines = [];
  lines.push("Расписание: " + (d.interval_sec > 0 ? "каждые " + d.interval_sec + " с" : "выключено"));
  if (d.last) {
    lines.push("Последняя проверка (МСК): " + d.last.checked_at_msk);
    (d.targets || ["vk", "tg", "server"]).forEach(t => {
      const r = (d.last.results || {})[t];
      if (!r) { lines.push(HEALTH_LABELS[t] + ": нет данных"); return; }
      lines.push(
        HEALTH_LABELS[t] + ": " + (r.ok ? "✓ работает" : "✗ НЕ работает") +
        (r.latency_ms !== null && r.latency_ms !== undefined ? " (" + r.latency_ms + " мс)" : "") +
        (r.detail ? " — " + r.detail : "")
      );
    });
  } else {
    lines.push("Проверок ещё не было.");
  }
  $("health-status").textContent = lines.join("\n");
  $("health-interval").value = d.interval_sec || 0;
}
async function loadHealth() {
  try { renderHealth(await api("GET", "/admin/health")); } catch (e) {}
}
async function healthCheck(target) {
  $("health-status").textContent = "Проверка…";
  try {
    renderHealth(await api("POST", "/admin/health/check", { target }, true));
  } catch (e) {
    out("Ошибка: " + e.message, false);
    loadHealth();
  }
}
async function healthSchedule() {
  try {
    renderHealth(await api("PUT", "/admin/health/schedule", { interval_sec: Number($("health-interval").value || 0) }, true));
    out("Расписание сохранено", true);
  } catch (e) { out("Ошибка: " + e.message, false); }
}
$("key").value = localStorage.getItem("admin-key") || "";
if ($("key").value) saveKey(); else renderTabs();
</script>
</body>
</html>"""


@router.get("/admin-panel", response_class=HTMLResponse)
async def admin_panel_page() -> str:
    return PAGE
