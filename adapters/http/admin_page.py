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
  </div></section>

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
  </div></section>

  <section data-tab="analytics"><div class="card"><h2>Аналитика</h2>
    <button onclick="loadAnalytics()">Загрузить</button>
    <div id="analytics" class="result" style="display:none"></div>
  </div>
  <div class="card"><h2>Журнал аудита</h2>
    <button class="ghost" onclick="alert('Просмотр audit_log — через SQL (psql) или по запросу разработчику')">Где смотреть</button>
  </div></section>
</main>
<div id="out"></div>

<script>
const TABS = [
  ["config", "Конфигурация", ["admin"]],
  ["moderation", "Модерация", ["admin", "moderator"]],
  ["content", "Контент", ["admin", "content_manager"]],
  ["analytics", "Аналитика", ["admin"]],
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
async function api(method, path, body) {
  const r = await fetch(path, { method, headers: H(), body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { out(data.detail || ("HTTP " + r.status), false); throw new Error(data.detail); }
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
  } catch (e) { ROLE = null; $("role").textContent = "ключ не принят"; renderTabs(); }
}
async function loadEffects() {
  try {
    const d = await api("GET", "/admin/effects");
    $("effect-code").innerHTML = d.effects.map(e => '<option value="' + e.code + '">' + e.code + "</option>").join("");
  } catch (e) {}
}
async function cfgGet() { out(await api("GET", "/admin/config/" + $("cfg-key").value.trim()), true); }
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
async function loadAnalytics() {
  const d = await api("GET", "/admin/analytics");
  $("analytics").style.display = "block";
  $("analytics").textContent = JSON.stringify(d, null, 2);
}
$("key").value = localStorage.getItem("admin-key") || "";
if ($("key").value) saveKey(); else renderTabs();
</script>
</body>
</html>"""


@router.get("/admin-panel", response_class=HTMLResponse)
async def admin_panel_page() -> str:
    return PAGE
