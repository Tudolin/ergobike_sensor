"use strict";

// ---- helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cssv = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const nf1 = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const nf0 = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const dtf = new Intl.DateTimeFormat("pt-BR", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const dlong = new Intl.DateTimeFormat("pt-BR", { weekday: "long", day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" });
const dshort = new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "2-digit" });
const dday = new Intl.DateTimeFormat("pt-BR", { day: "numeric", month: "short", year: "numeric" });

const fmt = {
  dur(s) {
    s = Math.max(0, Math.round(s || 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`;
  },
  durWords(s) {
    s = Math.round(s || 0);
    const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
    return h ? `${h}h ${String(m).padStart(2, "0")}min` : `${m}min`;
  },
  km: (v) => nf2.format(v || 0),
  rpm: (v) => nf0.format(v || 0),
  int: (v) => nf0.format(v || 0),
  dec: (v) => nf1.format(v || 0),
  date: (iso) => dtf.format(new Date(iso)),
  dateLong: (iso) => { const t = dlong.format(new Date(iso)); return t[0].toUpperCase() + t.slice(1); },
  day: (iso) => dday.format(new Date(iso)),
};

async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* empty body */ }
    throw new Error(msg);
  }
  return r.json();
}

function toast(msg) {
  $$(".toast").forEach((t) => t.remove());
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  document.body.append(t);
  setTimeout(() => t.remove(), 2600);
}

function modal(html) {
  const bg = document.createElement("div");
  bg.className = "modal-bg";
  bg.innerHTML = `<div class="modal" role="dialog" aria-modal="true">${html}</div>`;
  document.body.append(bg);
  bg.addEventListener("click", (e) => { if (e.target === bg) bg.remove(); });
  return bg;
}

function defaultTitle(d = new Date()) {
  const h = d.getHours();
  const p = h < 5 ? "da madrugada" : h < 12 ? "da manhã" : h < 18 ? "da tarde" : "da noite";
  return `Pedal ${p}`;
}

const ZONES = [
  { id: "Z1", name: "Leve", lo: 0, hi: 60 },
  { id: "Z2", name: "Moderada", lo: 60, hi: 75 },
  { id: "Z3", name: "Ritmo", lo: 75, hi: 90 },
  { id: "Z4", name: "Forte", lo: 90, hi: 105 },
  { id: "Z5", name: "Sprint", lo: 105, hi: Infinity },
];
const zoneOf = (rpm) => ZONES.find((z) => rpm >= z.lo && rpm < z.hi) || ZONES[0];
const zoneVar = (id) => `var(--${id.toLowerCase()})`;

// ---- icons
const I = (d, extra = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ${extra}>${d}</svg>`;
const ICON = {
  ride: I('<circle cx="5.5" cy="17" r="3.5"/><circle cx="18.5" cy="17" r="3.5"/><path d="M15 6h2l3 11M5.5 17 9 9h7l-4.5 8M9 9 8 6H6"/>'),
  list: I('<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r="1"/><circle cx="3.5" cy="12" r="1"/><circle cx="3.5" cy="18" r="1"/>'),
  chart: I('<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>'),
  gear: I('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
  play: I('<path d="M7 4.5v15l12-7.5z" fill="currentColor"/>'),
  pause: I('<rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor"/><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor"/>'),
  stop: I('<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>'),
  back: I('<path d="M15 18l-6-6 6-6"/>'),
  download: I('<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>'),
  trash: I('<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/>'),
  trophy: I('<path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0zM17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3"/>', 'width="12" height="12"'),
  wave: I('<path d="M2 12h3l2-6 4 12 3-9 2 3h6"/>'),
};

// ---- state and routing
const S = { live: null, settings: null, charts: [], view: null, liveMode: null };

const NAV = [
  ["treino", "Treino", ICON.ride],
  ["atividades", "Atividades", ICON.list],
  ["progresso", "Progresso", ICON.chart],
  ["ajustes", "Ajustes", ICON.gear],
];

function renderNav(route) {
  const top = route.split("/")[0] === "atividade" ? "atividades" : route.split("/")[0];
  $("#nav").innerHTML = NAV.map(([id, label, icon]) =>
    `<a href="#/${id}" class="${top === id ? "active" : ""}">${icon}<span>${label}</span></a>`).join("");
}

function destroyCharts() {
  S.charts.forEach((c) => c.destroy());
  S.charts = [];
}

const ROUTES = {
  treino: viewTreino,
  atividades: viewFeed,
  atividade: viewDetail,
  progresso: viewProgress,
  ajustes: viewSettings,
};

async function route() {
  const hash = location.hash.replace(/^#\/?/, "") || "treino";
  const [name, arg] = hash.split("/");
  renderNav(hash);
  destroyCharts();
  S.view = name;
  S.liveMode = null;
  const fn = ROUTES[name] || viewTreino;
  const el = $("#view");
  try {
    await fn(el, arg);
  } catch (e) {
    el.innerHTML = `<div class="card empty"><h3>Algo deu errado</h3><p>${esc(e.message)}</p></div>`;
  }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", route);

// ---- live updates
function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (e) => {
    S.live = JSON.parse(e.data);
    updateSideStatus();
    if (S.view === "treino") updateTreino();
    if (S.view === "ajustes") updateMeter();
  };
  ws.onclose = () => setTimeout(connect, 1000);
}

function sensorPill(l) {
  if (!l) return `<span class="pill"><span class="dot"></span>Conectando…</span>`;
  if (!l.source_ok) return `<span class="pill"><span class="dot err"></span>Sensor indisponível</span>`;
  const sim = l.simulated ? " · simulador" : "";
  return l.pedaling
    ? `<span class="pill"><span class="dot live"></span>Pedalada detectada${sim}</span>`
    : `<span class="pill"><span class="dot ok"></span>Sensor pronto${sim}</span>`;
}

function updateSideStatus() {
  const l = S.live, el = $("#side-status");
  if (l && l.state !== "idle") {
    const rec = l.state === "running";
    el.innerHTML = `<a href="#/treino" class="pill"><span class="dot ${rec ? "rec" : ""}"></span>${rec ? "Gravando" : "Pausado"} · <span class="num">${fmt.dur(l.elapsed_s)}</span></a>`;
  } else {
    el.innerHTML = sensorPill(l);
  }
}

// ---- charts
function chartTheme() {
  return { ink: cssv("--ink"), ink2: cssv("--ink-2"), muted: cssv("--muted"), grid: cssv("--grid"),
    series: cssv("--series"), soft: cssv("--series-soft"), surface: cssv("--surface"), font: cssv("--font") };
}

// dashed average line with a direct label (no legend needed for a single series)
const avgLinePlugin = {
  id: "avgLine",
  afterDatasetsDraw(chart, _args, opts) {
    if (!opts || !opts.value) return;
    const { ctx, chartArea: a, scales: { y } } = chart;
    const py = y.getPixelForValue(opts.value);
    if (py < a.top || py > a.bottom) return;
    ctx.save();
    ctx.strokeStyle = opts.color; ctx.setLineDash([5, 4]); ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(a.left, py); ctx.lineTo(a.right, py); ctx.stroke();
    ctx.setLineDash([]);
    const label = `média ${fmt.rpm(opts.value)} rpm`;
    ctx.font = `600 12px ${opts.font}`;
    const w = ctx.measureText(label).width + 12;
    ctx.fillStyle = opts.bg; ctx.fillRect(a.right - w, py - 20, w, 16);
    ctx.fillStyle = opts.color; ctx.textAlign = "right"; ctx.textBaseline = "bottom";
    ctx.fillText(label, a.right - 6, py - 5);
    ctx.restore();
  },
};

// vertical line under the cursor
const crosshairPlugin = {
  id: "crosshair",
  afterDraw(chart) {
    const act = chart.tooltip && chart.tooltip.getActiveElements();
    if (!act || !act.length) return;
    const { ctx, chartArea: a } = chart;
    const x = act[0].element.x;
    ctx.save();
    ctx.strokeStyle = cssv("--muted"); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, a.top); ctx.lineTo(x, a.bottom); ctx.stroke();
    ctx.restore();
  },
};

function cadenceChart(canvas, points, avg, opts = {}) {
  const th = chartTheme();
  const chart = new Chart(canvas, {
    type: "line",
    data: { datasets: [{
      data: points, parsing: false, borderColor: th.series, backgroundColor: th.soft,
      borderWidth: 2, pointRadius: 0, pointHoverRadius: 5, pointHoverBackgroundColor: th.series,
      pointHoverBorderColor: th.surface, pointHoverBorderWidth: 2, fill: "origin", tension: 0.25,
    }] },
    options: {
      animation: false, maintainAspectRatio: false, normalized: true,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      layout: { padding: { top: 8 } },
      scales: {
        x: { type: "linear", min: opts.xmin, max: opts.xmax, grid: { display: false },
          border: { color: th.grid },
          ticks: { color: th.muted, maxTicksLimit: 8, callback: (v) => fmt.dur(v), font: { family: th.font } } },
        y: { min: 0, suggestedMax: 110, grid: { color: th.grid }, border: { display: false },
          ticks: { color: th.muted, maxTicksLimit: 6, font: { family: th.font } } },
      },
      plugins: {
        legend: { display: false },
        avgLine: { value: avg, color: th.ink2, bg: th.surface, font: th.font },
        tooltip: {
          backgroundColor: th.surface, titleColor: th.muted, bodyColor: th.ink, borderColor: th.grid,
          borderWidth: 1, padding: 10, displayColors: false, bodyFont: { weight: "600", size: 14 },
          callbacks: { title: (it) => fmt.dur(it[0].parsed.x), label: (it) => `${fmt.rpm(it.parsed.y)} rpm` },
        },
      },
    },
    plugins: [avgLinePlugin, crosshairPlugin],
  });
  S.charts.push(chart);
  return chart;
}

function barChart(canvas, labels, values, fmtValue, unit) {
  const th = chartTheme();
  const chart = new Chart(canvas, {
    type: "bar",
    data: { labels, datasets: [{ data: values, backgroundColor: th.series, borderRadius: 4,
      borderSkipped: "bottom", maxBarThickness: 36, hoverBackgroundColor: th.series }] },
    options: {
      animation: false, maintainAspectRatio: false,
      scales: {
        x: { grid: { display: false }, border: { color: th.grid }, ticks: { color: th.muted, font: { family: th.font } } },
        y: { beginAtZero: true, grid: { color: th.grid }, border: { display: false },
          ticks: { color: th.muted, maxTicksLimit: 5, callback: fmtValue, font: { family: th.font } } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: th.surface, titleColor: th.muted, bodyColor: th.ink, borderColor: th.grid,
          borderWidth: 1, padding: 10, displayColors: false, bodyFont: { weight: "600", size: 14 },
          callbacks: { title: (it) => `Semana de ${it[0].label}`, label: (it) => `${fmtValue(it.parsed.y)} ${unit}` },
        },
      },
    },
  });
  S.charts.push(chart);
  return chart;
}

function sparkline(values) {
  if (!values || values.length < 2) return `<svg class="spark"></svg>`;
  const w = 220, h = 64, max = Math.max(110, ...values);
  const pts = values.map((v, i) => [(i / (values.length - 1)) * w, h - 3 - (v / max) * (h - 6)]);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
    <path class="area" d="${line}L${w},${h}L0,${h}Z"/><path class="line" d="${line}" vector-effect="non-scaling-stroke"/></svg>`;
}

function metric(label, value, unit = "", id = "") {
  return `<div class="metric"><div class="label">${label}</div>
    <div class="value"${id ? ` id="${id}"` : ""}>${value}${unit ? `<small>${unit}</small>` : ""}</div></div>`;
}

function zonesBar(zones) {
  const total = zones.reduce((a, z) => a + z.seconds, 0);
  const bar = total
    ? zones.map((z) => `<span style="flex-grow:${z.seconds};background:${zoneVar(z.id)}" title="${z.id} ${z.name}: ${fmt.dur(z.seconds)}"></span>`).join("")
    : `<span style="flex-grow:1;background:var(--surface-2)"></span>`;
  const legend = zones.map((z) => `<div><b><i class="sw" style="background:${zoneVar(z.id)}"></i>${z.id} ${fmt.dur(z.seconds)}</b>
    <span>${z.name} · ${z.hi ? `${z.lo}–${z.hi}` : `${z.lo}+`}</span></div>`).join("");
  return `<div class="zone-bar">${bar}</div><div class="zone-legend">${legend}</div>`;
}

// ---- ride screen
async function viewTreino(el) {
  S.liveMode = null;
  el.innerHTML = "";
  updateTreino(true);
}

function updateTreino(force = false) {
  const l = S.live, el = $("#view");
  const mode = !l ? "loading" : l.state === "idle" ? "idle" : "active";
  if (force || mode !== S.liveMode) {
    destroyCharts();
    S.liveMode = mode;
    if (mode === "active") renderActive(el); else renderIdle(el);
  }
  if (mode === "active") patchActive(l);
  if (mode === "idle") {
    const p = $("#sensor-pill");
    if (p) p.innerHTML = sensorPill(l);
    const b = $("#start");
    if (b) b.disabled = !l.source_ok;
  }
}

async function renderIdle(el) {
  const name = S.settings?.name ? `, ${esc(S.settings.name)}` : "";
  const h = new Date().getHours();
  const hello = h < 12 ? "Bom dia" : h < 18 ? "Boa tarde" : "Boa noite";
  const l = S.live;
  el.innerHTML = `
    <div class="page-head"><div><h1>${hello}${name}</h1><div class="sub">Pronto para pedalar?</div></div></div>
    ${l && !l.source_ok ? `<div class="banner">Não consegui abrir a entrada de áudio: ${esc(l.source_error || "")}<a href="#/ajustes">Ajustar sensor</a></div>` : ""}
    <div class="card start-wrap">
      <div id="sensor-pill">${sensorPill(l)}</div>
      <button class="start-btn" id="start" ${l && l.source_ok ? "" : "disabled"}>${ICON.play}Iniciar</button>
      <div class="sub" style="color:var(--muted)">A cadência aparece assim que o ímã passar pelo sensor.</div>
    </div>
    <div class="grid cols-2" style="margin-top:16px" id="idle-cards"></div>`;
  $("#start").onclick = startWorkout;
  const [stats, acts] = await Promise.all([api("/api/stats?weeks=1"), api("/api/activities?limit=1")]);
  if (S.view !== "treino" || S.liveMode !== "idle") return;
  const wk = stats.weeks[0];
  const goal = (S.settings?.weekly_goal_min || 150) * 60;
  const pct = Math.min(100, Math.round((100 * wk.moving_s) / goal));
  const last = acts[0];
  $("#idle-cards").innerHTML = `
    <div class="card"><h2>Esta semana</h2>
      <div class="week-card"><div class="ring" style="--p:${pct}"><div><div><b>${pct}%</b><br><span>da meta</span></div></div></div>
      <div class="metrics compact" style="grid-template-columns:repeat(2,auto);gap:12px 28px">
        ${metric("Tempo", fmt.durWords(wk.moving_s))}${metric("Meta", fmt.durWords(goal))}
        ${metric("Distância", fmt.km(wk.distance_km), "km")}${metric("Treinos", wk.count)}
      </div></div></div>
    <div class="card"><h2>Última atividade</h2>${last ? `
      <a href="#/atividade/${last.id}" style="display:block">
        <div class="act-title">${esc(last.title)}</div><div class="act-date">${fmt.date(last.started_at)}</div>
        <div class="metrics compact" style="margin:12px 0 6px">${metric("Distância", fmt.km(last.distance_km), "km")}
          ${metric("Tempo", fmt.dur(last.moving_s))}${metric("Cadência", fmt.rpm(last.avg_rpm), "rpm")}</div>
        ${sparkline(last.sparkline)}</a>` : `<div class="empty" style="padding:24px">Nenhuma atividade ainda.</div>`}
    </div>`;
}

function renderActive(el) {
  el.innerHTML = `
    <div class="page-head"><div><h1>Treino</h1></div><div id="rec-pill"></div></div>
    <div class="card hero">
      <div><div class="hero-rpm" id="rpm">0</div>
        <div class="hero-unit">rpm <span class="zone-chip" id="zone"></span></div></div>
      <div class="metrics">
        ${metric("Tempo", "0:00", "", "m-time")}
        ${metric("Distância", "0,00", "km", "m-dist")}
        ${metric("Velocidade", "0,0", "km/h", "m-speed")}
        ${metric("Cadência média", "0", "rpm", "m-avg")}
        ${metric("Voltas", "0", "", "m-revs")}
        ${metric("Calorias (estim.)", "0", "kcal", "m-kcal")}
      </div>
    </div>
    <div class="card" style="margin-top:16px"><div class="card-head"><h2>Cadência <span class="hint">últimos 10 min</span></h2>
      <span class="sub num" id="moving" style="color:var(--muted);font-size:13px"></span></div>
      <div class="chart-box"><canvas id="live-chart" aria-label="Cadência ao vivo"></canvas></div></div>
    <div class="card" style="margin-top:16px"><h2>Tempo em cada zona</h2><div id="zones"></div></div>
    <div class="controls" style="margin-top:24px">
      <button class="btn round" id="pause" title="Pausar"></button>
      <button class="btn primary" id="finish" style="height:52px;padding:0 28px">${ICON.stop} Finalizar</button>
    </div>`;
  S.liveChart = cadenceChart($("#live-chart"), [], 0);
  $("#pause").onclick = () => api(`/api/workout/${S.live.state === "running" ? "pause" : "resume"}`, { method: "POST" });
  $("#finish").onclick = finishDialog;
}

function patchActive(l) {
  const rpm = l.rpm || 0, z = zoneOf(rpm), wheel = S.settings?.wheel_m || 0;
  $("#rpm").textContent = fmt.rpm(rpm);
  $("#zone").innerHTML = rpm ? `<i style="background:${zoneVar(z.id)}"></i>${z.id} · ${z.name}` : "parado";
  const set = (id, v) => { const e = $(`#${id}`); if (e) e.firstChild.nodeValue = v; };
  set("m-time", fmt.dur(l.elapsed_s));
  set("m-dist", wheel ? fmt.km(l.distance_km) : "—");
  set("m-speed", wheel ? fmt.dec(l.speed_kmh) : "—");
  set("m-avg", fmt.rpm(l.avg_rpm));
  set("m-revs", fmt.int(l.revs));
  set("m-kcal", fmt.int(l.kcal));
  $("#moving").textContent = `em movimento ${fmt.dur(l.moving_s)}`;
  const rec = l.state === "running";
  $("#rec-pill").innerHTML = `<span class="pill"><span class="dot ${rec ? "rec" : ""}"></span>${rec ? "Gravando" : "Pausado"}</span>`;
  const pb = $("#pause");
  if (pb.dataset.s !== l.state) {
    pb.dataset.s = l.state;
    pb.innerHTML = rec ? ICON.pause : ICON.play;
    pb.title = rec ? "Pausar" : "Retomar";
  }
  $("#zones").innerHTML = zonesBar(l.zones);
  const c = S.liveChart;
  if (c) {
    c.data.datasets[0].data = l.live.map(([x, y]) => ({ x, y }));
    c.options.scales.x.min = Math.max(0, l.elapsed_s - 600);
    c.options.scales.x.max = Math.max(60, l.elapsed_s);
    c.options.plugins.avgLine.value = l.avg_rpm;
    c.update("none");
  }
}

async function startWorkout() {
  try {
    await api("/api/workout/start", { method: "POST" });
  } catch (e) { toast(e.message); }
}

function finishDialog() {
  const l = S.live;
  const m = modal(`
    <h3>Finalizar treino</h3>
    <div class="summary">${metric("Tempo", fmt.dur(l.elapsed_s))}${metric("Cadência", fmt.rpm(l.avg_rpm), "rpm")}${metric("Calorias", fmt.int(l.kcal), "kcal")}</div>
    <div class="field"><label for="f-title">Título</label><input class="input" id="f-title" placeholder="${defaultTitle()}"></div>
    <div class="field"><label for="f-notes">Como foi?</label><textarea class="input" id="f-notes" placeholder="Anotações (opcional)"></textarea></div>
    <div class="actions" style="justify-content:space-between">
      <button class="btn ghost danger" id="f-discard">Descartar</button>
      <div class="actions"><button class="btn" id="f-cancel">Continuar</button><button class="btn primary" id="f-save">Salvar</button></div>
    </div>`);
  $("#f-title", m).focus();
  $("#f-cancel", m).onclick = () => m.remove();
  $("#f-discard", m).onclick = async () => {
    if (!confirm("Descartar este treino? Ele não será salvo.")) return;
    await api("/api/workout/finish", { method: "POST", body: JSON.stringify({ save: false }) });
    m.remove(); toast("Treino descartado");
  };
  $("#f-save", m).onclick = async () => {
    const r = await api("/api/workout/finish", { method: "POST", body: JSON.stringify({
      save: true, title: $("#f-title", m).value.trim() || null, notes: $("#f-notes", m).value.trim() || null }) });
    m.remove();
    if (r.session_id) { toast("Atividade salva"); location.hash = `#/atividade/${r.session_id}`; }
    else toast("Nenhuma pedalada registrada: nada foi salvo");
  };
}

// ---- activities
async function viewFeed(el) {
  const acts = await api("/api/activities?limit=100");
  el.innerHTML = `<div class="page-head"><div><h1>Atividades</h1><div class="sub">${acts.length} treino${acts.length === 1 ? "" : "s"}</div></div></div>
    <div class="feed">${acts.length ? acts.map(actCard).join("") : `
      <div class="card empty">${ICON.ride}<h3>Nenhuma atividade ainda</h3><p>Seu primeiro treino aparece aqui.</p>
      <a class="btn primary" href="#/treino">Começar um treino</a></div>`}</div>`;
  $$(".act", el).forEach((c) => (c.onclick = () => (location.hash = `#/atividade/${c.dataset.id}`)));
}

function actCard(a) {
  const dist = a.wheel_m ? metric("Distância", fmt.km(a.distance_km), "km") : metric("Voltas", fmt.int(a.revs));
  return `<article class="card act" data-id="${a.id}" tabindex="0">
    <div><div class="act-head"><div class="avatar">${ICON.ride}</div>
      <div><div class="act-title">${esc(a.title || "Pedal")}</div><div class="act-date">${fmt.date(a.started_at)}</div></div></div>
      <div class="metrics">${dist}${metric("Tempo", fmt.dur(a.moving_s))}${metric("Cadência média", fmt.rpm(a.avg_rpm), "rpm")}</div></div>
    ${sparkline(a.sparkline)}</article>`;
}

async function viewDetail(el, id) {
  const [a, stats] = await Promise.all([api(`/api/activities/${id}`), api("/api/stats?weeks=1")]);
  const pr = Object.fromEntries(stats.records.map((r) => [r.label, r]));
  const hasDist = a.wheel_m > 0;
  const speed = a.moving_s ? (a.distance_km / a.moving_s) * 3600 : 0;
  const maxSplit = Math.max(1, ...a.splits.rows.map((r) => r.avg_rpm));
  const totalZ = a.zones.reduce((s, z) => s + z.seconds, 0) || 1;
  el.innerHTML = `
    <a class="back" href="#/atividades">${ICON.back} Atividades</a>
    <div class="page-head" style="margin-bottom:16px"><div style="flex:1;min-width:260px">
      <input class="title-input" id="title" value="${esc(a.title || "Pedal")}" aria-label="Título">
      <div class="sub">${fmt.dateLong(a.started_at)}</div></div>
      <div class="actions">
        <a class="btn" href="/api/activities/${a.id}/export.tcx" title="Para enviar ao Strava, Garmin Connect etc.">${ICON.download} TCX</a>
        <a class="btn" href="/api/activities/${a.id}/export.csv">${ICON.download} CSV</a>
        <button class="btn ghost danger" id="del" title="Excluir">${ICON.trash}</button>
      </div></div>
    <div class="card"><div class="stat-grid">
      ${metric("Distância", hasDist ? fmt.km(a.distance_km) : "—", hasDist ? "km" : "")}
      ${metric("Tempo em movimento", fmt.dur(a.moving_s))}
      ${metric("Cadência média", fmt.rpm(a.avg_rpm), "rpm")}
      ${metric("Calorias (estim.)", fmt.int(a.kcal), "kcal")}
      ${metric("Tempo total", fmt.dur(a.duration_s))}
      ${metric("Cadência máx", fmt.rpm(a.max_rpm), "rpm")}
      ${metric("Velocidade média", hasDist ? fmt.dec(speed) : "—", hasDist ? "km/h" : "")}
      ${metric("Voltas", fmt.int(a.revs))}
    </div>
    <textarea class="input" id="notes" placeholder="Adicione uma anotação…" style="margin-top:16px;min-height:60px">${esc(a.notes || "")}</textarea></div>

    <div class="card" style="margin-top:16px"><h2>Cadência</h2>
      <div class="chart-box tall"><canvas id="c-detail" aria-label="Cadência ao longo do treino"></canvas></div></div>

    <div class="grid cols-2" style="margin-top:16px">
      <div class="card"><h2>Zonas de cadência</h2>
        ${a.zones.map((z) => `<div class="zrow"><div><b>${z.id}</b> <span class="muted">${z.name}</span></div>
          <div class="track"><div class="fill" style="width:${(100 * z.seconds) / totalZ}%;background:${zoneVar(z.id)}"></div></div>
          <div class="r">${fmt.dur(z.seconds)}</div><div class="r muted">${nf0.format(z.pct)}%</div></div>`).join("")}
        <div class="sub" style="color:var(--muted);font-size:12.5px;margin-top:8px">Faixas em rpm: Z1 &lt;60 · Z2 60–75 · Z3 75–90 · Z4 90–105 · Z5 105+</div></div>
      <div class="card"><h2>Melhores esforços <span class="hint">cadência média</span></h2>
        ${Object.keys(a.bests).length ? `<div class="bests">${Object.entries(a.bests).map(([k, v]) => {
          const isPr = pr[k] && pr[k].id === a.id;
          return `<div class="best"><div class="label">${k}${isPr ? `<span class="badge">${ICON.trophy} recorde</span>` : ""}</div>
            <div class="value">${fmt.rpm(v)}<small> rpm</small></div></div>`; }).join("")}</div>`
          : `<div class="sub" style="color:var(--muted)">Treinos a partir de 1 min aparecem aqui.</div>`}</div>
    </div>

    <div class="card" style="margin-top:16px"><h2>Parciais <span class="hint">por ${a.splits.unit}</span></h2>
      <table class="tbl"><thead><tr><th>${a.splits.unit === "km" ? "Km" : "Bloco"}</th><th class="r">Tempo</th>
        <th class="r">Voltas</th><th class="r">Cadência</th><th class="barcell"></th></tr></thead>
      <tbody>${a.splits.rows.map((r) => `<tr><td>${r.n}${r.partial ? ` <span style="color:var(--muted)">(${fmt.km(r.km)} km)</span>` : ""}</td>
        <td class="r">${fmt.dur(r.seconds)}</td><td class="r">${fmt.int(r.revs)}</td><td class="r"><b>${fmt.rpm(r.avg_rpm)}</b> rpm</td>
        <td class="barcell"><div class="hbar" style="width:${(100 * r.avg_rpm) / maxSplit}%"></div></td></tr>`).join("")}</tbody></table></div>`;

  const pts = a.series.t.map((x, i) => ({ x, y: a.series.rpm[i] }));
  cadenceChart($("#c-detail"), pts, a.avg_rpm, { xmin: 0, xmax: a.duration_s });

  const save = async (body) => { await api(`/api/activities/${a.id}`, { method: "PATCH", body: JSON.stringify(body) }); toast("Salvo"); };
  const title = $("#title");
  title.onkeydown = (e) => { if (e.key === "Enter") title.blur(); };
  title.onchange = () => title.value.trim() && save({ title: title.value });
  const notes = $("#notes");
  notes.onchange = () => save({ notes: notes.value });
  $("#del").onclick = async () => {
    if (!confirm("Excluir esta atividade? Não dá para desfazer.")) return;
    await api(`/api/activities/${a.id}`, { method: "DELETE" });
    toast("Atividade excluída");
    location.hash = "#/atividades";
  };
}

// ---- progress
async function viewProgress(el) {
  const st = await api("/api/stats?weeks=12");
  const goal = (S.settings?.weekly_goal_min || 150) * 60;
  const wk = st.weeks[st.weeks.length - 1];
  const pct = Math.min(100, Math.round((100 * wk.moving_s) / goal));
  const t = st.totals;
  el.innerHTML = `
    <div class="page-head"><div><h1>Progresso</h1><div class="sub">Últimas 12 semanas</div></div></div>
    <div class="grid cols-3">
      <div class="card span-2"><h2>Esta semana</h2><div class="week-card">
        <div class="ring" style="--p:${pct}"><div><div><b>${pct}%</b><br><span>de ${fmt.durWords(goal)}</span></div></div></div>
        <div class="metrics" style="flex:1">${metric("Tempo", fmt.durWords(wk.moving_s))}${metric("Distância", fmt.km(wk.distance_km), "km")}
          ${metric("Treinos", wk.count)}</div></div></div>
      <div class="card"><h2>Sequência</h2>
        <div class="metric"><div class="value" style="font-size:44px">${st.streak_weeks}<small>semana${st.streak_weeks === 1 ? "" : "s"}</small></div>
        <div class="label" style="margin-top:4px">seguidas com pelo menos um treino</div></div></div>
    </div>
    <div class="card" style="margin-top:16px"><div class="card-head"><h2>Volume semanal</h2>
      <div class="segmented" id="seg"><button data-k="time" class="on">Tempo</button><button data-k="dist">Distância</button><button data-k="count">Treinos</button></div></div>
      <div class="chart-box short"><canvas id="c-weeks" aria-label="Volume semanal"></canvas></div></div>
    <div class="card" style="margin-top:16px"><h2>Calendário <span class="hint">últimas 16 semanas</span></h2>
      <div class="heat" id="heat"></div>
      <div class="heat-legend">menos <i style="background:var(--heat-0)"></i><i style="background:var(--heat-1)"></i><i style="background:var(--heat-2)"></i><i style="background:var(--heat-3)"></i><i style="background:var(--heat-4)"></i> mais</div></div>
    <div class="grid cols-2" style="margin-top:16px">
      <div class="card"><h2>Recordes pessoais</h2><div class="rec-list">
        ${st.records.length ? st.records.map((r) => `<a href="#/atividade/${r.id}"><span class="t">Melhor ${r.label}</span>
          <span class="v">${fmt.rpm(r.rpm)} <small>rpm</small></span><span class="d">${esc(r.title)} · ${fmt.day(r.started_at)}</span></a>`).join("") : ""}
        ${st.longest ? `<a href="#/atividade/${st.longest.id}"><span class="t">Treino mais longo</span><span class="v">${fmt.dur(st.longest.moving_s)}</span>
          <span class="d">${esc(st.longest.title)} · ${fmt.day(st.longest.started_at)}</span></a>` : ""}
        ${st.farthest && st.farthest.distance_km ? `<a href="#/atividade/${st.farthest.id}"><span class="t">Maior distância</span><span class="v">${fmt.km(st.farthest.distance_km)} <small>km</small></span>
          <span class="d">${esc(st.farthest.title)} · ${fmt.day(st.farthest.started_at)}</span></a>` : ""}
        ${!st.longest ? `<div class="sub" style="color:var(--muted)">Complete um treino para registrar recordes.</div>` : ""}
      </div></div>
      <div class="card"><h2>Total</h2><div class="metrics" style="grid-template-columns:repeat(2,1fr)">
        ${metric("Treinos", fmt.int(t.count))}${metric("Tempo", fmt.durWords(t.moving_s))}
        ${metric("Distância", fmt.km(t.distance_km), "km")}${metric("Voltas", fmt.int(t.revs))}
        ${metric("Calorias (estim.)", fmt.int(t.kcal), "kcal")}</div></div>
    </div>`;

  const labels = st.weeks.map((w) => dshort.format(new Date(`${w.week}T12:00`)));
  const series = {
    time: [st.weeks.map((w) => w.moving_s / 60), (v) => nf0.format(v), "min"],
    dist: [st.weeks.map((w) => w.distance_km), (v) => nf1.format(v), "km"],
    count: [st.weeks.map((w) => w.count), (v) => nf0.format(v), "treinos"],
  };
  let chart = barChart($("#c-weeks"), labels, ...series.time);
  $$("#seg button").forEach((b) => (b.onclick = () => {
    $$("#seg button").forEach((x) => x.classList.toggle("on", x === b));
    S.charts = S.charts.filter((c) => c !== chart);
    chart.destroy();
    chart = barChart($("#c-weeks"), labels, ...series[b.dataset.k]);
  }));

  // calendar: one column per week (Mon..Sun), level = time ridden that day
  const today = new Date(); today.setHours(12, 0, 0, 0);
  const start = new Date(today); start.setDate(start.getDate() - ((today.getDay() + 6) % 7) - 15 * 7);
  const cells = [];
  for (let d = new Date(start), i = 0; i < 16 * 7; i++, d.setDate(d.getDate() + 1)) {
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const s = st.days[key] || 0;
    const lvl = s === 0 ? 0 : s < 900 ? 1 : s < 1800 ? 2 : s < 3600 ? 3 : 4;
    const future = d > today;
    cells.push(`<i data-l="${lvl}" class="${future ? "future" : ""}" title="${future ? "" : `${dday.format(d)}: ${s ? fmt.durWords(s) : "sem treino"}`}"></i>`);
  }
  $("#heat").innerHTML = cells.join("");
}

// ---- settings
const meterPos = (v) => Math.min(100, Math.sqrt(Math.max(0, v) / 2) * 100);  // escala raiz: 0..2

async function viewSettings(el) {
  const [cfg, devices] = await Promise.all([api("/api/settings"), api("/api/devices")]);
  S.settings = cfg;
  const busy = S.live && S.live.state !== "idle";
  const devOpts = devices.map((d) => `<option value="${d.index ?? ""}" ${d.index === cfg.device ? "selected" : ""}>${esc(d.index ?? "")}${d.index != null ? " · " : ""}${esc(d.name)}</option>`).join("");
  el.innerHTML = `
    <div class="page-head"><div><h1>Ajustes</h1></div></div>
    <div class="grid cols-2">
      <div class="card"><h2>Perfil</h2><div class="form">
        <div class="field"><label for="s-name">Nome</label><input class="input" id="s-name" value="${esc(cfg.name)}"></div>
        <div class="form-row">
          <div class="field"><label for="s-weight">Peso (kg)</label><input class="input" id="s-weight" type="number" min="30" max="250" step="0.5" value="${cfg.weight_kg}">
            <span class="help">Usado na estimativa de calorias.</span></div>
          <div class="field"><label for="s-goal">Meta semanal (min)</label><input class="input" id="s-goal" type="number" min="10" max="2000" step="10" value="${cfg.weekly_goal_min}"></div>
        </div>
        <div class="field"><label for="s-wheel">Distância por volta do pedal (m)</label>
          <input class="input" id="s-wheel" type="number" min="0" max="20" step="0.1" value="${cfg.wheel_m}">
          <span class="help">Converte voltas em km. ~6 m equivale a uma marcha média numa bike de rua; 0 esconde distância e velocidade.</span></div>
      </div></div>

      <div class="card"><h2>Sensor</h2><div class="form">
        ${busy ? `<div class="callout">Finalize o treino para alterar o sensor.</div>` : ""}
        <div class="field"><label for="s-dev">Entrada de áudio</label><select class="input" id="s-dev" ${busy ? "disabled" : ""}>${devOpts}</select>
          <span class="help">Use a entrada de microfone onde o cabo "SENSOR" está ligado.</span></div>
        <div class="field"><label>Sinal agora <span id="lvl-num" class="num" style="color:var(--muted);font-weight:500"></span></label>
          <div class="meter"><div class="lvl" id="lvl"></div><div class="thr" id="thr"></div></div>
          <div class="meter-scale"><span>0</span><span>degrau do reed · linha laranja = limiar</span><span>2</span></div></div>
        <div class="form-row">
          <div class="field"><label for="s-thr">Limiar do degrau</label><input class="input" id="s-thr" type="number" min="0.01" max="2" step="0.01" value="${cfg.threshold}" ${busy ? "disabled" : ""}></div>
          <div class="field"><label for="s-edge">Borda contada</label><select class="input" id="s-edge" ${busy ? "disabled" : ""}>
            ${[["close", "Fechamento"], ["open", "Abertura"], ["both", "Ambas"]].map(([v, t]) => `<option value="${v}" ${cfg.edge === v ? "selected" : ""}>${t}</option>`).join("")}</select></div>
        </div>
        <div class="field"><label for="s-ppr">Pulsos por volta</label><input class="input" id="s-ppr" type="number" min="1" max="8" value="${cfg.pulses_per_rev}" ${busy ? "disabled" : ""}></div>
        <div><button class="btn" id="cal" ${busy ? "disabled" : ""}>${ICON.wave} Calibrar (10 s)</button></div>
        <div id="cal-out"></div>
      </div></div>
    </div>
    <div class="actions" style="margin-top:20px;justify-content:flex-end"><button class="btn primary" id="save">Salvar ajustes</button></div>`;

  $("#s-thr").oninput = updateMeter;
  updateMeter();
  $("#save").onclick = async () => {
    const dev = $("#s-dev").value;
    const body = {
      name: $("#s-name").value.trim(), weight_kg: +$("#s-weight").value, weekly_goal_min: +$("#s-goal").value,
      wheel_m: +$("#s-wheel").value,
    };
    if (!busy) Object.assign(body, { device: dev === "" ? null : +dev, threshold: +$("#s-thr").value,
      edge: $("#s-edge").value, pulses_per_rev: +$("#s-ppr").value });
    try {
      S.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify(body) });
      toast("Ajustes salvos");
    } catch (e) { toast(e.message); }
  };
  $("#cal").onclick = async () => {
    const out = $("#cal-out"), btn = $("#cal");
    btn.disabled = true;
    let left = 10;
    out.innerHTML = `<div class="callout">Pedale em ritmo normal… <b id="cal-left">${left}</b> s</div>`;
    const tick = setInterval(() => { const e = $("#cal-left"); if (e) e.textContent = Math.max(0, --left); }, 1000);
    try {
      const r = await api("/api/calibrate?seconds=10", { method: "POST" });
      const best = r[r.suggest_polarity];
      out.innerHTML = r.suggest_threshold == null
        ? `<div class="callout">Nenhum degrau claro. Confira o cabo e a entrada escolhida e tente de novo pedalando.</div>`
        : `<div class="callout">Detectei <b>${best.count}</b> voltas · degrau típico <b>${nf2.format(best.median)}</b> · ruído <b>${r.noise.toFixed(3)}</b>
            (margem ${nf0.format(best.median / Math.max(r.noise, 1e-6))}×).<br>Sugestão: limiar <b>${r.suggest_threshold}</b>, borda <b>${r.suggest_polarity === "close" ? "fechamento" : "abertura"}</b>.
            <div style="margin-top:10px"><button class="btn primary" id="apply">Aplicar e salvar</button></div></div>`;
      const ap = $("#apply");
      if (ap) ap.onclick = () => { $("#s-thr").value = r.suggest_threshold; $("#s-edge").value = r.suggest_polarity; updateMeter(); $("#save").click(); };
    } catch (e) {
      out.innerHTML = `<div class="callout">${esc(e.message)}</div>`;
    } finally { clearInterval(tick); btn.disabled = false; }
  };
}

function updateMeter() {
  const lvl = $("#lvl"), thr = $("#thr"), inp = $("#s-thr");
  if (!lvl || !inp) return;
  const v = S.live?.step_level || 0;
  lvl.style.width = `${meterPos(v)}%`;
  thr.style.left = `${meterPos(+inp.value)}%`;
  $("#lvl-num").textContent = nf2.format(v);
}

// ---- boot
(async function init() {
  try { S.settings = await api("/api/settings"); } catch (_) { S.settings = {}; }
  connect();
  route();
})();

// ---- desktop shell
// called by desktop.py when the window is closed mid-ride
window.ergobikeAskClose = function () {
  if ($(".modal-bg.ask-close")) return;
  const l = S.live || {};
  const m = modal(`
    <h3>Treino em andamento</h3>
    <div class="summary">${metric("Tempo", fmt.dur(l.elapsed_s))}${metric("Cadência", fmt.rpm(l.avg_rpm), "rpm")}${metric("Calorias", fmt.int(l.kcal), "kcal")}</div>
    <p style="margin:0;color:var(--ink-2)">O que fazer com ele antes de fechar o app?</p>
    <div class="actions" style="justify-content:space-between">
      <button class="btn ghost danger" id="c-discard">Descartar e fechar</button>
      <div class="actions"><button class="btn" id="c-stay">Continuar treinando</button><button class="btn primary" id="c-save">Salvar e fechar</button></div>
    </div>`);
  m.classList.add("ask-close");
  $("#c-stay", m).onclick = () => m.remove();
  $("#c-save", m).onclick = () => window.pywebview.api.close_app(true);
  $("#c-discard", m).onclick = () => window.pywebview.api.close_app(false);
};
