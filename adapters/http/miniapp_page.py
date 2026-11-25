"""Статичный фронтенд VK Mini App: одностраничное приложение без сборки.

Раздаётся с того же сервера, что и API — launch params из URL iframe
пересылаются в заголовке X-VK-Launch-Params (верификация подписи на сервере).
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["miniapp-frontend"])

PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Battle for the Galaxy</title>
<script src="https://unpkg.com/@vkontakte/vk-bridge/dist/browser.min.js"></script>
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
  background: #0b1020; color: #e7ecf5; min-height: 100vh; padding: 16px;
}
h1 { font-size: 20px; margin-bottom: 4px; }
.sub { color: #8b96ad; font-size: 13px; margin-bottom: 16px; }
.card {
  background: #141b31; border: 1px solid #232c4d; border-radius: 12px;
  padding: 14px; margin-bottom: 12px;
}
.card h2 { font-size: 14px; color: #9fb4ff; margin-bottom: 10px; text-transform: uppercase; letter-spacing: .5px; }
.row { display: flex; justify-content: space-between; padding: 4px 0; font-size: 14px; }
.row span:last-child { color: #cdd7ee; }
.bar { height: 6px; background: #232c4d; border-radius: 3px; overflow: hidden; margin-top: 4px; }
.bar > div { height: 100%; background: linear-gradient(90deg,#4d7cff,#9b5bff); }
.item { display: flex; justify-content: space-between; font-size: 14px; padding: 5px 0; border-bottom: 1px dashed #1d2745; }
.item:last-child { border-bottom: none; }
.qty { color: #9fb4ff; }
.status-exp { color: #ffd479; }
.err { color: #ff7a7a; font-size: 14px; }
.refresh { margin-top: 8px; width: 100%; padding: 12px; border: none; border-radius: 10px;
  background: linear-gradient(90deg,#4d7cff,#9b5bff); color: #fff; font-size: 15px; font-weight: 600; }
</style>
</head>
<body>
<h1>Battle for the Galaxy</h1>
<div class="sub" id="sub">загрузка…</div>
<div id="content"></div>
<button class="refresh" onclick="loadAll()">Обновить</button>

<script>
const LP = location.search.replace(/^[?]/, "");
const H = { "X-VK-Launch-Params": LP };
if (typeof vkBridge !== "undefined") { vkBridge.send("VKWebAppInit"); }

async function api(path) {
  const r = await fetch(path, { headers: H });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || ("HTTP " + r.status));
  return r.json();
}

function card(title, rowsHtml) {
  return `<div class="card"><h2>${title}</h2>${rowsHtml}</div>`;
}
function row(k, v) { return `<div class="row"><span>${k}</span><span>${v}</span></div>`; }

async function loadAll() {
  const c = document.getElementById("content");
  try {
    const me = await api("/api/me");
    document.getElementById("sub").textContent = me.name + " · пул опыта: " + me.xp_pool;
    let html = "";
    if (me.ship) {
      const s = me.ship;
      html += card("Корабль",
        row("Прочность", s.hull + (s.malfunctions ? " (поломки: " + s.malfunctions + ")" : "")) +
        row("Топливо", s.fuel) +
        row("Статус", s.status === "dock" ? "космопорт" : s.status === "expedition" ? "экспедиция" : s.status === "returning" ? '<span class="status-exp">возвращение</span>' : s.status));
    }
    if (me.expedition) {
      html += card("Экспедиция", row("Глубина", me.expedition.depth) + row("Статус", me.expedition.status));
    }
    c.innerHTML = html;
    const inv = await api("/api/inventory?kind=ship");
    c.innerHTML += card("Инвентарь (" + inv.items.length + (inv.size_limit ? "/" + inv.size_limit : "") + ")",
      inv.items.map(i => `<div class="item"><span>${i.name}</span><span class="qty">×${i.qty}</span></div>`).join("") || row("", "пусто"));
    const mods = await api("/api/mods");
    c.innerHTML += card("Модули",
      mods.modules.map(m => `<div class="item"><span>${m.name} (ур. ${m.level})</span><span class="qty">${m.installed ? "слот " + m.slot : "в инвентаре"}</span></div>`).join("") || row("", "нет модулей"));
    const q = await api("/api/quests");
    if (q.quests.length) {
      c.innerHTML += card("Задания", q.quests.map(quest =>
        `<div class="item"><span>${quest.name}</span><span class="qty">${quest.progress.map(p => p.have + "/" + p.need).join(", ")}</span></div>`
      ).join(""));
    }
    if (q.faction_events.length) {
      c.innerHTML += card("Фракционные события", q.faction_events.map(e =>
        `<div class="item"><span>${e.name}</span><span class="qty">${e.progress}/${e.goal}</span></div>`
      ).join(""));
    }
  } catch (e) {
    c.innerHTML = card("Ошибка", `<div class="err">${e.message}</div>`);
    document.getElementById("sub").textContent = "откройте через VK Mini App";
  }
}
loadAll();
</script>
</body>
</html>"""


@router.get("/app", response_class=HTMLResponse)
async def miniapp_page() -> str:
    return PAGE
