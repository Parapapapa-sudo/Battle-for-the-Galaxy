"""Статичный фронтенд VK Mini App: одностраничное приложение без сборки.

Раздаётся с того же сервера, что и API — launch params из URL iframe
пересылаются в заголовке X-VK-Launch-Params (верификация подписи на сервере).
Шапка-разделы: Корабль (инфо/инвентарь/оснащение), Магазин, Журнал, Статистика.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter(tags=["miniapp-frontend"])


@router.get("/fonts/{filename}")
async def font_file(filename: str) -> FileResponse:
    """Шрифты мини-аппа (assets/fonts/Arounder.woff2|.ttf)."""
    import re
    from pathlib import Path

    if not re.fullmatch(r"[A-Za-z0-9_-]+\.(woff2|ttf|otf)", filename):
        raise HTTPException(status_code=404, detail="not found")
    path = Path("assets/fonts") / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=604800"})


@router.get("/icons/{group}/{filename}")
async def icon_file(group: str, filename: str) -> FileResponse:
    """Раздача иконок предметов/товаров (assets/icons/<группа>/<код>.png)."""
    import re

    from app.images import ICONS_DIR

    if not re.fullmatch(r"[a-z0-9_]+", group) or not re.fullmatch(
        r"[a-z0-9_]+\.(png|jpg|webp)", filename
    ):
        raise HTTPException(status_code=404, detail="not found")
    path = ICONS_DIR / group / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})

PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Battle for the Galaxy</title>
<script src="https://unpkg.com/@vkontakte/vk-bridge/dist/browser.min.js"></script>
<style>
@font-face {
  font-family: "Arounder";
  src: url("/fonts/Arounder.woff2") format("woff2"),
       url("/fonts/Arounder.otf") format("opentype"),
       url("/fonts/Arounder.ttf") format("truetype");
  font-display: swap;
}
:root { color-scheme: dark; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Arounder", -apple-system, "Segoe UI", Roboto, sans-serif;
  background: #0b1020; color: #e7ecf5; min-height: 100vh; }
header { position: sticky; top: 0; z-index: 5;
  background: rgba(16, 23, 54, 0.72);
  backdrop-filter: blur(16px) saturate(150%); -webkit-backdrop-filter: blur(16px) saturate(150%);
  border-bottom: 1px solid rgba(255, 255, 255, 0.08); padding: 10px 12px 0; }
nav { display: flex; gap: 4px; }
nav button { flex: 1; background: none; border: none; color: #8b96ad; font-size: 12px;
  padding: 8px 2px 10px; cursor: pointer; border-bottom: 2px solid transparent; }
nav button.active { color: #9fb4ff; border-bottom-color: #4d7cff; }
nav button .ico { display: block; font-size: 18px; margin-bottom: 2px; }
main { padding: 14px; max-width: 640px; margin: 0 auto; }
.subtabs { display: flex; gap: 6px; margin-bottom: 12px; }
.subtabs button { flex: 1;
  background: rgba(20, 27, 49, 0.38);
  backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
  border: 1px solid rgba(255, 255, 255, 0.10); color: #8b96ad;
  border-radius: 10px; padding: 8px 4px; font-size: 12px; cursor: pointer; }
.subtabs button.active { background: #182142; color: #9fb4ff; border-color: #4d7cff; }
.card {
  background: rgba(20, 27, 49, 0.42);
  backdrop-filter: blur(14px) saturate(150%);
  -webkit-backdrop-filter: blur(14px) saturate(150%);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 16px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.28), inset 0 1px 0 rgba(255, 255, 255, 0.07);
  padding: 14px; margin-bottom: 12px;
}
.card h2 { font-size: 13px; color: #9fb4ff; margin-bottom: 10px; text-transform: uppercase; letter-spacing: .5px; }
.row { display: flex; justify-content: space-between; padding: 4px 0; font-size: 14px; }
.row span:last-child { color: #cdd7ee; }
.grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
.slot { aspect-ratio: 1;
  background: rgba(13, 19, 48, 0.38);
  backdrop-filter: blur(10px) saturate(140%);
  -webkit-backdrop-filter: blur(10px) saturate(140%);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 12px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08);
  display: flex; align-items: center; justify-content: center; position: relative;
  font-size: 26px; cursor: pointer; }
.slot:active { border-color: #4d7cff; }
.slot .qty { position: absolute; right: 4px; bottom: 2px; font-size: 11px; color: #fff;
  background: #4d7cff; border-radius: 8px; padding: 0 5px; font-weight: 700; }
.slot.empty { cursor: default; opacity: .35; font-size: 18px; }
.slot.free { border-style: dashed; color: #5b6a8f; font-size: 22px; }
.slot.free:active { border-color: #4d7cff; color: #9fb4ff; }
.slot img { width: 78%; height: 78%; object-fit: contain; }
.bigicon img { width: 96px; height: 96px; object-fit: contain; }
.slot .lvl { position: absolute; left: 4px; top: 2px; font-size: 10px; color: #ffd479; }
.bigicon { font-size: 56px; text-align: center; padding: 10px 0 4px; }
.iname { text-align: center; font-size: 18px; font-weight: 700; }
.rarity { text-align: center; font-size: 12px; margin-bottom: 8px; }
.rar-common { color: #8b96ad; } .rar-rare { color: #6db3ff; }
.rar-epic { color: #c07aff; } .rar-unique { color: #ffd479; }
.desc { color: #aeb8d0; font-size: 13px; text-align: center; margin-bottom: 8px; }
.chip { display: inline-block; background: #223; border: 1px solid #35406e; border-radius: 12px;
  font-size: 11px; padding: 2px 8px; margin: 2px; color: #cdd7ee; }
.chips { text-align: center; margin-bottom: 6px; }
.btn { display: block; width: 100%; padding: 12px; margin-top: 8px; border: none; border-radius: 10px;
  background: linear-gradient(90deg,#4d7cff,#9b5bff); color: #fff; font-size: 14px; font-weight: 600; cursor: pointer; }
.btn.ghost { background: rgba(27, 36, 68, 0.5); border: 1px solid rgba(255,255,255,0.12);
  backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); }
.back { background: none; border: none; color: #9fb4ff; font-size: 14px; cursor: pointer; padding: 4px 0 10px; }
.note { color: #8b96ad; font-size: 12px; margin-top: 6px; }
.err { color: #ff7a7a; font-size: 14px; }
.toast { background: #0d1330; border: 1px solid #2f6f4f; border-radius: 8px; padding: 10px;
  font-size: 13px; white-space: pre-wrap; margin-bottom: 12px; }
.quest { border-bottom: 1px dashed #1d2745; padding: 8px 0; }
.quest:last-child { border-bottom: none; }
.quest .qn { font-size: 14px; font-weight: 600; }
.quest .qd { font-size: 12px; color: #8b96ad; margin: 2px 0; }
.quest .qp { font-size: 12px; color: #9fb4ff; }
.badge { font-size: 10px; padding: 1px 7px; border-radius: 10px; margin-left: 6px; }
.badge.avail { background: #2f6f4f; color: #d7ffe7; }
.walletbar { display: flex; gap: 14px; justify-content: center; padding: 8px 12px;
  margin-bottom: 12px; border-radius: 12px;
  background: rgba(20, 27, 49, 0.42); border: 1px solid rgba(255,255,255,0.10);
  backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
  font-size: 14px; font-weight: 600; }
.mat { display: inline-flex; align-items: center; gap: 4px; margin: 2px 8px 2px 0;
  font-size: 12px; color: #cdd7ee; }
.mat.lack { color: #ff9d9d; }
.mat img { width: 18px; height: 18px; object-fit: contain; }
</style>
</head>
<body>
<header>
  <nav id="nav"></nav>
</header>
<main id="content"><div class="card">Загрузка…</div></main>

<script>
const LP = location.search.replace(/^[?]/, "");
const H = { "X-VK-Launch-Params": LP, "Content-Type": "application/json" };
if (typeof vkBridge !== "undefined") { vkBridge.send("VKWebAppInit"); }

const SECTIONS = [
  ["ship", "🚀", "Корабль"],
  ["craft", "🧪", "Крафт"],
  ["shop", "🛒", "Магазин"],
  ["journal", "📜", "Журнал"],
  ["stats", "📊", "Статистика"],
];
const SHIP_TABS = [["info", "Общая информация"], ["inv", "Инвентарь"], ["equip", "Оснащение"]];
const ICONS = { resource: "⛏️", consumable: "💊", module: "🔩", repair_kit: "🧰", special: "💎" };
const RAR = { common: "обычный", rare: "редкий", epic: "эпический", unique: "уникальный" };
let SECTION = "ship", SHIP_TAB = "info", TOAST = "", ME = null;

const $c = () => document.getElementById("content");
const icon = (cat) => ICONS[cat] || "📦";
const pic = (url, cat) => url ? `<img src="${url}" alt="">` : icon(cat);

async function loadMe() {
  if (!ME) {
    ME = await api("/api/me");
    if (ME.app_bg) {
      document.body.style.background =
        "linear-gradient(rgba(11,16,32,0.55), rgba(11,16,32,0.78)), url('" + ME.app_bg + "') center/cover fixed no-repeat #0b1020";
    }
  }
  return ME;
}

async function api(path, opts) {
  const r = await fetch(path, { headers: H, ...opts });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || ("HTTP " + r.status));
  return data;
}
async function act(cmd) {
  try {
    const r = await api("/api/action", { method: "POST", body: JSON.stringify({ cmd }) });
    TOAST = r.message;
  } catch (e) { TOAST = "Ошибка: " + e.message; }
  ME = null;  // состояние могло измениться
  render();
}

function renderNav() {
  document.getElementById("nav").innerHTML = SECTIONS.map(([id, ico, label]) =>
    `<button class="${SECTION === id ? "active" : ""}" onclick="go('${id}')">
       <span class="ico">${ico}</span>${label}</button>`).join("");
}
function go(section) { SECTION = section; SHIP_TAB = "info"; TOAST = ""; render(); }
function goShipTab(tab) { SHIP_TAB = tab; TOAST = ""; render(); }
function toast() { return TOAST ? `<div class="toast">${TOAST}</div>` : ""; }
async function walletBar() {
  const me = await loadMe();
  return `<div class="walletbar"><span>💎 ${me.eon}</span><span>🪙 ${me.quant}</span></div>`;
}
function row(k, v) { return `<div class="row"><span>${k}</span><span>${v}</span></div>`; }
function card(title, html) { return `<div class="card"><h2>${title}</h2>${html}</div>`; }

// --- Корабль: общая информация -------------------------------------------------
async function viewShipInfo() {
  const me = await loadMe();
  let html = await walletBar();
  html += card("👤 Капитан", row("Ник", me.name));
  if (me.ship) {
    const s = me.ship;
    const status = { dock: "космопорт", expedition: "экспедиция", returning: "возвращение", battle: "бой" }[s.status] || s.status;
    html += card("🚀 Корабль",
      row("Прочность", s.hull) + row("Поломки", s.malfunctions) +
      row("Топливо", s.fuel) + row("Статус", status));
  }
  if (me.expedition) {
    html += card("🛰 Экспедиция", row("Глубина", me.expedition.depth) + row("Статус", me.expedition.status));
  }
  html += card("✨ Прогресс", row("Пул опыта", me.xp_pool) + row("Инвентарь (слотов)", me.inventory_size));
  return html;
}

// --- Корабль: инвентарь (слоты) -------------------------------------------------
async function viewInventory() {
  const inv = await api("/api/inventory?kind=ship");
  const total = inv.size_limit || inv.items.length;
  let cells = inv.items.map(i =>
    `<div class="slot" onclick="openItem('${i.code}')" title="${i.name}">
       ${pic(i.icon, i.category)}<span class="qty">${i.qty}</span></div>`).join("");
  for (let k = inv.items.length; k < Math.min(total, Math.max(10, inv.items.length)); k++) {
    cells += `<div class="slot empty">·</div>`;
  }
  const bgStyle = inv.bg
    ? ` style="background: linear-gradient(rgba(11,16,32,0.35), rgba(11,16,32,0.55)), url('${inv.bg}') center/cover no-repeat"`
    : "";
  return `<div class="card"${bgStyle}><h2>📦 Инвентарь (${inv.items.length}/${inv.size_limit || "∞"})</h2><div class="grid">${cells}</div></div>`
    + `<div class="note">Нажмите на предмет, чтобы открыть его карточку.</div>`;
}

// --- Корабль: оснащение ---------------------------------------------------------
async function viewEquipment() {
  const eq = await api("/api/equipment");
  const cell = (m, kind) =>
    `<div class="slot" onclick="${kind ? `openSlotPicker('${kind}','${m.code}')` : `openItem('${m.code}')`}" title="${m.name}">
       ${m.icon ? pic(m.icon, m.kind) : (m.kind === "ability" ? "⚡" : "🔩")}<span class="lvl">ур.${m.level}</span></div>`;
  const free = (kind, count) => Array.from({ length: count }, () =>
    `<div class="slot free" onclick="openSlotPicker('${kind}')" title="Установить модуль">＋</div>`).join("");
  const shipFree = Math.max(0, (eq.ship_slots_total || 0) - eq.ship_slots.length);
  const crewFree = Math.max(0, (eq.crew_slots_total || 0) - eq.crew_skills.length);
  return await walletBar()
    + card("🛠 Оснащение корабля",
      `<div class="grid">${eq.ship_slots.map(m => cell(m, "ship")).join("")}${free("ship", shipFree)}</div>`)
    + card("👥 Команда (навыки)",
      `<div class="grid">${eq.crew_skills.map(m => cell(m, "crew")).join("")}${free("crew", crewFree)}</div>`)
    + (eq.storage.length ? card("🎒 Модули в инвентаре", `<div class="grid">${eq.storage.map(m => cell(m)).join("")}</div>`) : "")
    + `<div class="note">Тап по слоту (пустому или занятому) — подбор модуля из инвентаря: установка и замена.</div>`;
}

// Подбор модуля в слот: инвентарь, отфильтрованный под этот слот.
// Пустой слот — установка; занятый (currentCode) — замена (снять старый + поставить новый).
async function openSlotPicker(kind, currentCode) {
  const eq = await api("/api/equipment");
  const all = [...eq.ship_slots, ...eq.crew_skills, ...eq.storage];
  const current = currentCode ? all.find(m => m.code === currentCode) : null;
  const fit = eq.storage.filter(m => kind === "crew" ? m.kind === "ability" : m.kind !== "ability");
  const cellPick = (m) =>
    `<div class="slot" onclick="pickModule('${m.code}', ${currentCode ? `'${currentCode}'` : "null"})" title="${m.name}">
       ${m.icon ? pic(m.icon, m.kind) : (m.kind === "ability" ? "⚡" : "🔩")}
       <span class="lvl">ур.${m.level}</span></div>`;
  let html = `<button class="back" onclick="render()">← Назад</button>` + toast();
  if (current) {
    html += card("Сейчас в слоте",
      `<div class="quest"><div class="qn">${current.name} <span class="qp">ур. ${current.level}</span></div></div>
       <button class="btn ghost" onclick="openItem('${current.code}')">Карточка модуля</button>
       <button class="btn ghost" onclick="act('uninstall ${current.code}')">Снять без замены</button>
       <div class="note">Выбор модуля ниже заменит текущий.</div>`);
  }
  html += card(kind === "crew" ? "👥 Инвентарь: навыки команды" : "🛠 Инвентарь: модули корабля",
    fit.length
      ? `<div class="grid">${fit.map(cellPick).join("")}</div>`
      : `<div class="note">В инвентаре нет подходящих модулей${kind === "crew" ? " со способностями" : ""}.
         Модули добываются в экспедициях и покупаются.</div>`);
  $c().innerHTML = html;
}

// установка с заменой: сначала снять текущий (если есть), затем поставить выбранный
async function pickModule(code, replaceCode) {
  try {
    if (replaceCode) {
      await api("/api/action", { method: "POST", body: JSON.stringify({ cmd: "uninstall " + replaceCode }) });
    }
    const r = await api("/api/action", { method: "POST", body: JSON.stringify({ cmd: "install " + code }) });
    TOAST = r.message;
  } catch (e) { TOAST = "Ошибка: " + e.message; }
  ME = null;
  render();
}

// --- Карточка предмета / товара (универсальная) ---------------------------------
async function openItem(code) {
  const it = await api("/api/items/" + encodeURIComponent(code));
  let chips = `<span class="chip">${RAR[it.rarity] || it.rarity}</span>`;
  if (it.is_unique) chips += `<span class="chip">уникальный</span>`;
  if (!it.tradable) chips += `<span class="chip">персональный</span>`;
  if (!it.sellable) chips += `<span class="chip">не продаётся</span>`;
  chips += `<span class="chip">стек до ${it.stack_limit}</span>`;
  let html = `<button class="back" onclick="render()">← Назад</button>` + toast();
  html += `<div class="card"><div class="bigicon">${pic(it.icon, it.category)}</div>
    <div class="iname">${it.name}</div>
    <div class="rarity rar-${it.rarity}">${RAR[it.rarity] || it.rarity}</div>
    <div class="desc">${it.description || ""}</div>
    <div class="chips">${chips}</div>
    ${row("У вас", it.owned + " шт.")}
    ${it.module ? row("Уровень модуля", it.module.level + " (" + it.module.xp + " опыта)") : ""}
    ${it.module ? row("Состояние", it.module.installed ? "установлен" : "в инвентаре") : ""}
  </div>`;
  html += card("⚖️ Аукцион", `<div class="note">Лоты по этому предмету появятся здесь. Пока аукцион доступен в чате (команды lots / sell).</div>`);
  if (it.actions.length) {
    html += card("Действия", it.actions.map(a =>
      `<button class="btn" onclick="act('${a.cmd.replace(/'/g, "")}')">${a.label}</button>`).join(""));
  }
  $c().innerHTML = html;
}

// --- Крафт -----------------------------------------------------------------------
async function viewCraft() {
  const d = await api("/api/recipes");
  let html = await walletBar();
  if (d.jobs.length) {
    html += card("🛠 Производство (" + d.jobs.length + "/" + d.slots_total + ")",
      d.jobs.map(j => `<div class="quest"><div class="qn">${j.name} <span class="qp">до ${j.finishes_at}</span></div>
        <button class="btn ghost" onclick="act('craftcancel ${j.idx}')">Отменить</button></div>`).join(""));
  }
  html += d.recipes.map(r => {
    const mats = r.materials.map(m =>
      `<span class="mat ${m.have < m.need ? "lack" : ""}">${m.icon ? `<img src="${m.icon}">` : ""}${m.name} ${Math.min(m.have, m.need)}/${m.need}</span>`
    ).join("");
    return `<div class="card">
      <div style="display:flex;gap:12px;align-items:center">
        <div style="font-size:34px">${r.result.icon ? `<img src="${r.result.icon}" style="width:44px;height:44px;object-fit:contain">` : "🧪"}</div>
        <div style="flex:1">
          <div class="qn" style="font-size:15px;font-weight:700">«${r.name}»</div>
          <div class="note">${r.result.qty}× ${r.result.name} · ${r.duration_sec} с</div>
        </div>
      </div>
      <div style="margin:8px 0">${mats}</div>
      <button class="btn" ${r.can_craft ? "" : "disabled style='opacity:.4'"}
        onclick="act('craft ${r.code}')">${r.can_craft ? "Создать" : "Не хватает материалов"}</button>
    </div>`;
  }).join("");
  return html;
}

// --- Магазин --------------------------------------------------------------------
async function viewShop() {
  const shop = await api("/api/shop");
  const cells = shop.offers.map(o => {
    const ic = o.kind === "pack" ? "💎" : "⭐";
    const price = o.kind === "pack" ? o.price_rub + " ₽" : o.price_eon + " Эонов";
    return `<div class="slot" style="aspect-ratio:auto;padding:10px 4px;flex-direction:column;gap:4px"
      onclick="openOffer('${o.code}','${o.title}','${ic}','${price}','${o.kind}','${o.icon || ""}')">
      <div style="font-size:26px">${o.icon ? `<img src="${o.icon}" style="width:40px;height:40px;object-fit:contain">` : ic}</div>
      <div style="font-size:11px;text-align:center">${o.title}</div>
      <div style="font-size:11px;color:#9fb4ff">${price}</div></div>`;
  }).join("");
  return card("💎 Магазин", `<div class="grid" style="grid-template-columns:repeat(3,1fr)">${cells}</div>`)
    + `<div class="note">Эоны ↔ Кванты также обмениваются на внутреннем аукционе (команда market в чате).
       Паки Эонов оплачиваются в рублях, подписки — за Эоны.</div>`;
}
function openOffer(code, title, ic, price, kind, iconUrl) {
  $c().innerHTML = `<button class="back" onclick="render()">← Назад</button>` + toast()
    + `<div class="card"><div class="bigicon">${iconUrl ? `<img src="${iconUrl}" style="width:96px;height:96px;object-fit:contain">` : ic}</div>
       <div class="iname">${title}</div>
       <div class="rarity" style="color:#9fb4ff">${price}</div>
       <div class="desc">${kind === "pack"
         ? "Пак Эонов. После оплаты Эоны начислятся автоматически. Эоны можно обменять на Кванты на аукционе."
         : "Подписка: расширенный инвентарь и дополнительный слот крафта."}</div></div>`
    + card("Действия", `<button class="btn" onclick="act('buy ${code}')">Купить</button>`);
}

// --- Журнал ----------------------------------------------------------------------
async function viewJournal() {
  const j = await api("/api/journal");
  const quests = j.quests.length
    ? j.quests.map(q => `<div class="quest">
        <div class="qn">${q.name}${q.status === "available" ? '<span class="badge avail">принять в чате</span>' : ""}</div>
        <div class="qd">${q.description}</div>
        ${q.progress ? `<div class="qp">${q.progress}</div>` : ""}</div>`).join("")
    : `<div class="note">Заданий нет.</div>`;
  const ach = j.achievements.length
    ? j.achievements.map(a => `<div class="row"><span>🏅 ${a.code}</span></div>`).join("")
    : `<div class="note">Достижений пока нет — они появятся за подвиги в игре.</div>`;
  const rep = j.reputation.length
    ? j.reputation.map(r => row(r.faction, (r.value > 0 ? "+" : "") + r.value)).join("")
    : `<div class="note">Репутация не заработана.</div>`;
  return card("📜 Задания", quests) + card("🏅 Достижения", ach) + card("⭐ Репутация", rep);
}

// --- Статистика -------------------------------------------------------------------
async function viewStats() {
  const s = await api("/api/stats");
  const winrate = s.battles_total ? Math.round(100 * s.battles_won / s.battles_total) + "%" : "—";
  return card("👤 Капитан",
      row("Имя", s.name) + row("Фракция", s.faction) + row("В игре с", s.created_at))
    + card("📊 Показатели",
      row("Боёв", s.battles_total) + row("Побед", s.battles_won + " (" + winrate + ")") +
      row("Экспедиций", s.expeditions_total) + row("Заданий выполнено", s.quests_completed) +
      row("Пул опыта", s.xp_pool))
    + card("💰 Кошельки", row("💎 Эоны", s.eon) + row("🪙 Кванты", s.quant));
}

// --- роутер ----------------------------------------------------------------------
async function render() {
  renderNav();
  try {
    let html = toast();
    if (SECTION === "ship") {
      html += `<div class="subtabs">` + SHIP_TABS.map(([id, label]) =>
        `<button class="${SHIP_TAB === id ? "active" : ""}" onclick="goShipTab('${id}')">${label}</button>`).join("") + `</div>`;
      html += SHIP_TAB === "info" ? await viewShipInfo()
        : SHIP_TAB === "inv" ? await viewInventory() : await viewEquipment();
    } else if (SECTION === "craft") html += await viewCraft();
    else if (SECTION === "shop") html += await viewShop();
    else if (SECTION === "journal") html += await viewJournal();
    else html += await viewStats();
    $c().innerHTML = html;
  } catch (e) {
    $c().innerHTML = card("Ошибка", `<div class="err">${e.message}</div>
      <button class="btn ghost" onclick="render()">Обновить</button>`);
  }
}
render();
</script>
</body>
</html>"""


@router.get("/app", response_class=HTMLResponse)
async def miniapp_page() -> str:
    return PAGE
