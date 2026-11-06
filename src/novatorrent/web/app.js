import { nova, qb, setCsrf } from "./api.js";
import { lineChart, segBar, spark } from "./charts.js";
import { icon } from "./icons.js";
import { renderSearch } from "./search.js";
import { renderSettings } from "./settings.js";
import {
  bytes, closeDialogs, copyText, date, dialog, eta, getLang, h, loadI18n, menuAt, setLang, speed, t, toast,
} from "./ui.js";

const root = document.getElementById("root");
const S = {
  torrents: new Map(), categories: {}, tags: [], server: {}, rid: 0, selected: new Set(), cur: null, loaded: false,
  filter: { status: "all", category: null, tag: null, tracker: null }, search: "",
  sort: { key: "added_on", dir: -1 }, tab: "general", view: "torrents", role: "admin", anchor: null, prefs: {},
  speedHist: [], torHist: new Map(), timer: null, sideOpen: false, detailOpen: false, collapsed: {},
};
const ALL_COLS = ["name", "size", "progress", "state", "dlspeed", "upspeed", "eta", "ratio", "category",
  "tags", "added_on", "num_seeds", "num_leechs", "save_path"];
const NUM_COLS = new Set(["size", "dlspeed", "upspeed", "eta", "ratio", "num_seeds", "num_leechs"]);
const SM_HIDE = new Set(["size", "ratio", "tags", "added_on", "num_seeds", "num_leechs", "save_path", "upspeed", "eta", "category"]);
let cols = ["name", "size", "progress", "state", "dlspeed", "upspeed", "eta", "ratio", "category"];
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k) || "null") ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage blocked */ } },
};
cols = store.get("cols", cols);
S.collapsed = store.get("side", {});
const mq = matchMedia("(max-width: 800px)");
const mobile = () => mq.matches;
const readOnly = () => S.role !== "admin";

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("theme", theme); } catch { /* ignore */ }
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "light" ? "#ffffff" : "#11141b");
}
let savedTheme = null;
try { savedTheme = localStorage.getItem("theme"); } catch { /* ignore */ }
applyTheme(savedTheme === "light" ? "light" : "dark"); // modern dark by default
const savedH = store.get("detailH", null);
if (savedH) document.documentElement.style.setProperty("--detail-h", savedH);

const logo = () => h("span", { class: "logo" }, icon("down", 17));
const brand = () => h("span", { class: "brand" }, logo(), t("app.title"));
function btn(label, fn, cls = "", title, ic) {
  return h("button", { class: cls, title: title || label, "aria-label": title || label, onclick: fn }, ic ? icon(ic, 16) : null, label || null);
}
const iconBtn = (ic, label, fn, cls = "ghost") => h("button", { class: cls + " icon-btn", title: label, "aria-label": label, onclick: fn }, icon(ic, 17));

// ---------------------------------------------------------------- login
function showLogin(msg) {
  clearInterval(S.timer);
  const user = h("input", { name: "u", autocomplete: "username", required: true });
  const pass = h("input", { name: "p", type: "password", autocomplete: "current-password", required: true });
  const err = h("div", { class: "bad", role: "alert" }, msg || "");
  root.replaceChildren(h("div", { class: "login" }, h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      const r = await fetch("/api/v2/auth/login", {
        method: "POST", credentials: "same-origin",
        body: new URLSearchParams({ username: user.value, password: pass.value }),
      });
      const txt = await r.text();
      if (txt === "Ok.") start();
      else err.textContent = r.status === 403 ? t("login.locked") : t("login.failed");
    },
  }, brand(), h("h2", {}, t("login.title")),
  h("label", {}, t("login.user")), user, h("label", {}, t("login.pass")), pass, err,
  h("button", { class: "primary" }, t("login.submit")))));
  user.focus();
}

// ---------------------------------------------------------------- data
async function poll() {
  try {
    const d = await qb("sync/maindata?rid=" + S.rid);
    if (d.full_update) { S.torrents.clear(); S.categories = {}; S.tags = []; S.server = {}; }
    S.rid = d.rid; S.loaded = true;
    for (const [k, v] of Object.entries(d.torrents || {})) S.torrents.set(k, { ...(S.torrents.get(k) || { hash: k }), ...v });
    for (const k of d.torrents_removed || []) { S.torrents.delete(k); S.selected.delete(k); if (S.cur === k) S.cur = null; }
    Object.assign(S.categories, d.categories || {});
    for (const c of d.categories_removed || []) delete S.categories[c];
    if (d.tags) S.tags = [...new Set([...S.tags, ...d.tags])];
    if (d.tags_removed) S.tags = S.tags.filter((x) => !d.tags_removed.includes(x));
    Object.assign(S.server, d.server_state || {});
    S.speedHist.push([S.server.dl_info_speed || 0, S.server.up_info_speed || 0]);
    if (S.speedHist.length > 180) S.speedHist.shift();
    if (S.cur && S.torrents.has(S.cur)) {
      const x = S.torrents.get(S.cur), hist = S.torHist.get(S.cur) || [];
      hist.push([x.dlspeed, x.upspeed]); if (hist.length > 120) hist.shift(); S.torHist.set(S.cur, hist);
    }
    if (S.view === "torrents") { renderSide(); renderToolbar(); renderTable(); renderDetail(); }
    if (S.view === "stats") updateStats();
    renderStrip();
    const dl = S.server.dl_info_speed || 0, ul = S.server.up_info_speed || 0;
    document.title = dl || ul ? `↓ ${speed(dl) || "0"} ↑ ${speed(ul) || "0"} · ${t("app.title")}` : t("app.title");
  } catch (e) {
    if (e.status === 403) showLogin();
  }
}

// ---------------------------------------------------------------- layout
const VIEWS = [["torrents", "list", "nav.torrents"], ["search", "search", "nav.search", true], ["stats", "activity", "nav.stats"],
  ["logs", "file", "nav.logs"], ["settings", "sliders", "nav.settings"]];

function shell() {
  const drop = h("div", { class: "dropzone", id: "drop" }, h("div", { class: "box" }, icon("cloud", 40), t("drop.title")));
  root.replaceChildren(h("div", { class: "app" },
    h("header", { class: "topbar", id: "top" }),
    h("div", { class: "strip", id: "strip" }),
    h("div", { class: "main", id: "main" })), h("nav", { class: "bottomnav", id: "bottomnav" }), drop);
  renderTop();
  renderStrip();
  showView(S.view === "torrents" || !S.view ? "torrents" : S.view);
}

function goto(v) { S.sideOpen = false; showView(v); }

function renderTop() {
  const top = document.getElementById("top");
  const ro = readOnly();
  const views = VIEWS.filter((v) => !(v[3] && ro));
  const searchInput = h("input", { type: "search", id: "search", placeholder: t("tb.search"), value: S.search, "aria-label": t("tb.search"),
    oninput: (e) => {
      S.search = e.target.value;
      if (S.view !== "torrents") { showView("torrents"); const i = document.getElementById("search"); i?.focus(); i?.setSelectionRange(i.value.length, i.value.length); } else { renderTable(); renderToolbar(); }
    } });
  const sbox = h("div", { class: "searchbox" }, icon("search", 15), searchInput);
  top.replaceChildren(
    h("button", { class: "ghost icon-btn mobile-only", "aria-label": t("ui.filters"), title: t("ui.filters"), onclick: () => { S.sideOpen = !S.sideOpen; if (S.view !== "torrents") showView("torrents"); renderSide(); } }, icon("menu", 18)),
    brand(),
    h("nav", { class: "nav", "aria-label": "Main" }, views.map(([id, ic, key]) =>
      h("button", { class: S.view === id ? "on" : "", "aria-current": S.view === id ? "page" : null, onclick: () => goto(id) }, icon(ic, 16), t(key)))),
    h("span", { class: "spacer" }),
    sbox,
    h("button", { class: "ghost icon-btn mobile-only", "aria-label": t("tb.search"), onclick: () => { sbox.classList.toggle("open"); if (sbox.classList.contains("open")) searchInput.focus(); } }, icon("search", 18)),
    h("div", { class: "top-actions" },
      ro ? null : h("button", { class: "primary", onclick: addDialog, title: t("tb.add") + " (a)" }, icon("plus", 16), h("span", { class: "hide-sm" }, t("tb.add"))),
      iconBtn(document.documentElement.dataset.theme === "dark" ? "sun" : "moon", t("tb.theme"), () => { applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"); renderTop(); renderStrip(); if (S.view === "stats") buildStats(); }),
      h("button", { class: "ghost sm hide-sm", title: t("tb.lang"), "aria-label": t("tb.lang"), onclick: () => { setLang(getLang() === "en" ? "nl" : "en"); shell(); poll(); } }, icon("globe", 15), getLang().toUpperCase()),
      h("span", { class: "hide-sm" }, iconBtn("help", t("tb.help"), helpDialog)),
      iconBtn("logout", t("nav.logout"), async () => { await qb("auth/logout", {}, { quiet: true }); showLogin(); })));
  const bn = document.getElementById("bottomnav");
  if (bn) bn.replaceChildren(...views.map(([id, ic, key]) => h("button", { class: S.view === id ? "on" : "", onclick: () => goto(id) }, icon(ic, 20), t(key))));
}

function renderStrip() {
  const el = document.getElementById("strip");
  if (!el) return;
  const s = S.server, tors = [...S.torrents.values()];
  const dl = S.speedHist.map((v) => v[0]).slice(-60), ul = S.speedHist.map((v) => v[1]).slice(-60);
  const free = s.free_space_on_disk, min = (S.prefs.nova_min_free_space_mb || 0) * 2 ** 20, warn = (S.prefs.nova_warn_free_space_mb || 0) * 2 ** 20;
  const freeCls = free != null && free < min ? "bad" : warn && free != null && free < warn ? "warn" : "ok";
  const vpnDown = s.vpn_ok === false;
  const active = tors.filter((x) => x.dlspeed > 0 || x.upspeed > 0).length;
  const stat = (extra, ...kids) => h("div", { class: "stat" + (extra ? " extra" : "") }, ...kids);
  el.replaceChildren(
    stat(false, h("div", {}, h("div", { class: "lbl" }, icon("down", 14, "dl-ic"), t("bar.down")), h("div", { class: "val" }, speed(s.dl_info_speed) || "0 B/s")), spark(dl, "--s1")),
    stat(false, h("div", {}, h("div", { class: "lbl" }, icon("up", 14, "ul-ic"), t("bar.up")), h("div", { class: "val" }, speed(s.up_info_speed) || "0 B/s")), spark(ul, "--s2")),
    stat(true, h("div", {}, h("div", { class: "lbl" }, icon("list", 14), t("bar.torrents")), h("div", { class: "val" }, tors.length),
      h("div", { class: "sub" }, `${active} ${t("st.active").toLowerCase()} · ${s.total_peer_connections ?? 0} ${t("bar.peers").toLowerCase()}`))),
    stat(true, h("div", { style: "min-width:0" },
      h("div", { class: "lbl" }, icon("shield", 14), t("bar.vpn")),
      h("div", { class: "val" }, h("span", { class: "chip-status " + (vpnDown ? "bad" : "ok") }, h("i", { class: "dot" }), vpnDown ? t("bar.vpn_down") : t("bar.vpn_ok"))),
      h("div", { class: "sub " + (freeCls === "ok" ? "" : freeCls), title: s.vpn_reason || "" },
        vpnDown && s.vpn_reason ? s.vpn_reason : [icon("disk", 13), ` ${t("bar.free")}: ${bytes(free)}`, freeCls === "ok" ? "" : ` · ${t("bar.low")}`]))));
}

function showView(v) {
  S.view = v;
  const main = document.getElementById("main");
  main.classList.toggle("single", v !== "torrents");
  renderTop();
  const page = (id) => h("div", { class: "page", id });
  if (v === "torrents") {
    main.replaceChildren(h("aside", { class: "side", id: "side" }),
      h("div", { class: "center" }, h("div", { class: "toolbar", id: "toolbar" }), h("div", { class: "tablewrap", id: "tablewrap" }), h("div", { class: "detail", id: "detail" })));
    renderSide(); renderToolbar(); renderTable(); renderDetail(true);
  } else if (v === "search") {
    main.replaceChildren(page("searchpage")); renderSearch(main.firstChild, { categories: S.categories });
  } else if (v === "settings") {
    main.replaceChildren(page("settingspage")); renderSettings(main.firstChild, { role: S.role });
  } else if (v === "stats") {
    main.replaceChildren(page("statspage")); buildStats();
  } else if (v === "logs") {
    main.replaceChildren(page("logspage")); renderLogs();
  }
}

// ---------------------------------------------------------------- sidebar + filters
const STATUS = [["all", "layers"], ["downloading", "down"], ["seeding", "up"], ["completed", "checkCircle"], ["paused", "pauseCircle"],
  ["active", "zap"], ["inactive", "clock"], ["stalled", "alert"], ["errored", "xCircle"]];
function trackerHost(x) { try { return new URL(x).hostname; } catch { return ""; } }
const MATCHES = {
  all: () => true, downloading: (x) => /DL$|metaDL|MetaDL/.test(x.state) && !x.state.startsWith("paused"),
  seeding: (x) => /UP$|uploading/.test(x.state) && !x.state.startsWith("paused"), completed: (x) => x.progress >= 1,
  paused: (x) => x.state.startsWith("paused"), active: (x) => x.dlspeed > 0 || x.upspeed > 0,
  inactive: (x) => x.dlspeed === 0 && x.upspeed === 0, stalled: (x) => x.state.startsWith("stalled"),
  errored: (x) => ["error", "missingFiles"].includes(x.state),
};

function renderSide() {
  const side = document.getElementById("side");
  if (!side) return;
  side.classList.toggle("open", S.sideOpen);
  const list = [...S.torrents.values()];
  const item = (ic, label, count, on, fn) => h("div", { class: "item" + (on ? " on" : ""), role: "button", tabindex: 0,
    onclick: () => { fn(); S.sideOpen = false; renderSide(); renderTable(); },
    onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(); renderSide(); renderTable(); } } },
  icon(ic, 16), h("span", { class: "lbl", title: label }, label), h("span", { class: "count" }, count));
  const group = (id, title, ...items) => {
    const closed = !!S.collapsed[id];
    return [h("div", { class: "grp", role: "button", tabindex: 0, "aria-expanded": String(!closed), onclick: () => { S.collapsed[id] = !closed; store.set("side", S.collapsed); renderSide(); } },
      h("span", {}, title), icon(closed ? "chevronRight" : "chevronDown", 13)), closed ? null : items];
  };
  const trackers = {};
  list.forEach((x) => { const hn = trackerHost(x.tracker); if (hn) trackers[hn] = (trackers[hn] || 0) + 1; });
  side.replaceChildren(...[
    group("status", t("flt.status"), STATUS.map(([s, ic]) => item(ic, t("st." + s), list.filter(MATCHES[s]).length, S.filter.status === s, () => { S.filter.status = s; }))),
    group("category", t("flt.category"),
      item("folder", t("flt.all"), list.length, S.filter.category === null, () => { S.filter.category = null; }),
      item("folder", t("flt.none"), list.filter((x) => !x.category).length, S.filter.category === "", () => { S.filter.category = ""; }),
      Object.keys(S.categories).sort().map((c) => item("folder", c, list.filter((x) => x.category === c).length, S.filter.category === c, () => { S.filter.category = c; }))),
    group("tag", t("flt.tag"),
      item("tag", t("flt.all"), list.length, S.filter.tag === null, () => { S.filter.tag = null; }),
      S.tags.map((g) => item("tag", g, list.filter((x) => (x.tags || "").split(", ").includes(g)).length, S.filter.tag === g, () => { S.filter.tag = g; }))),
    group("tracker", t("flt.tracker"),
      item("globe", t("flt.all"), list.length, S.filter.tracker === null, () => { S.filter.tracker = null; }),
      Object.entries(trackers).map(([k, n]) => item("globe", k, n, S.filter.tracker === k, () => { S.filter.tracker = k; })))].flat(3).filter(Boolean));
}

function visible() {
  const q = S.search.toLowerCase(), f = S.filter;
  let list = [...S.torrents.values()].filter((x) => (MATCHES[f.status] || MATCHES.all)(x)
    && (f.category === null || x.category === f.category)
    && (f.tag === null || (x.tags || "").split(", ").includes(f.tag))
    && (f.tracker === null || trackerHost(x.tracker) === f.tracker)
    && (!q || (x.name || "").toLowerCase().includes(q)));
  const { key, dir } = S.sort;
  list.sort((a, b) => (a[key] > b[key] ? 1 : a[key] < b[key] ? -1 : 0) * dir);
  return list;
}

// ---------------------------------------------------------------- state badge / progress
function kindOf(st) {
  if (st === "error" || st === "missingFiles") return "bad";
  if (st.startsWith("paused")) return "pause";
  if (st.startsWith("queued") || st.startsWith("checking")) return "queue";
  if (st === "stalledDL") return "warn";
  if (st === "stalledUP") return "idle";
  if (st === "uploading" || st === "forcedUP") return "up";
  if (/^(downloading|forcedDL|metaDL|forcedMetaDL|allocating|moving)$/.test(st)) return "dl";
  return "idle";
}
const badge = (x) => h("span", { class: "badge " + kindOf(x.state) }, t("state." + x.state));
function pbar(x) {
  const k = kindOf(x.state), cls = k === "bad" ? "bad" : k === "pause" ? "paused" : x.progress >= 1 ? "done" : "";
  const pct = (x.progress * 100).toFixed(x.progress >= 1 ? 0 : 1) + "%";
  return h("div", { class: "pbar " + cls, role: "progressbar", "aria-valuenow": Math.round(x.progress * 100), "aria-valuemin": 0, "aria-valuemax": 100, "aria-label": x.name },
    h("div", { class: "track" }, h("i", { style: `width:${(x.progress * 100).toFixed(1)}%` })), h("span", {}, pct));
}
const rate = (v, cls, ic) => (v > 0 ? h("span", { class: "rate " + cls }, icon(ic, 12), speed(v)) : "");

// ---------------------------------------------------------------- table / cards
let shownOrder = [];
function cell(c, x) {
  switch (c) {
    case "name": return h("td", { class: "name", title: x.name }, x.name);
    case "size": return h("td", { class: "num" }, bytes(x.size));
    case "progress": return h("td", {}, pbar(x));
    case "state": return h("td", {}, badge(x));
    case "dlspeed": return h("td", { class: "num" }, rate(x.dlspeed, "dl", "down"));
    case "upspeed": return h("td", { class: "num" }, rate(x.upspeed, "ul", "up"));
    case "eta": return h("td", { class: "num" }, x.progress >= 1 ? "" : eta(x.eta));
    case "ratio": return h("td", { class: "num" }, (x.ratio ?? 0).toFixed(2));
    case "category": return h("td", {}, x.category ? h("span", { class: "chip" }, icon("folder", 12), x.category) : "");
    case "added_on": return h("td", {}, date(x.added_on));
    default: return h("td", { class: NUM_COLS.has(c) ? "num" : "" }, x[c] ?? "");
  }
}
function emptyState(total) {
  if (!S.loaded) return h("div", { class: "pad" }, [0, 1, 2, 3].map(() => h("div", { class: "skeleton", style: "margin:16px 0" })));
  if (total === 0) {
    return h("div", { class: "empty" }, h("div", { class: "glyph" }, icon("inbox", 30)), h("h3", {}, t("empty.title")), h("p", {}, t("empty.text")),
      readOnly() ? null : h("div", { class: "actions" }, h("button", { class: "primary", onclick: addDialog }, icon("plus", 16), t("tb.add"))));
  }
  return h("div", { class: "empty" }, h("div", { class: "glyph" }, icon("filter", 28)), h("h3", {}, t("empty.filtered")),
    h("button", { onclick: () => { S.filter = { status: "all", category: null, tag: null, tracker: null }; S.search = ""; const i = document.getElementById("search"); if (i) i.value = ""; renderSide(); renderTable(); } }, t("empty.clear")));
}
function contextMenu(e, hash) {
  e.preventDefault();
  if (!S.selected.has(hash)) { S.selected = new Set([hash]); S.cur = hash; S.anchor = hash; renderTable(); renderToolbar(); renderDetail(true); }
  const x = S.torrents.get(hash);
  const copy = [{ label: t("ui.copy_hash"), icon: "copy", fn: async () => toast((await copyText(hash)) ? t("common.copied") : t("common.error"), false) }];
  if (x?.magnet_uri) copy.push({ label: t("ui.copy_magnet"), icon: "link", fn: async () => toast((await copyText(x.magnet_uri)) ? t("common.copied") : t("common.error"), false) });
  menuAt(e.clientX, e.clientY, readOnly() ? copy : [...actionItems(), "-", ...copy]);
}
function renderTable() {
  const wrap = document.getElementById("tablewrap");
  if (!wrap) return;
  const list = visible();
  shownOrder = list.map((x) => x.hash);
  const st = wrap.scrollTop;
  if (!list.length) { wrap.replaceChildren(emptyState(S.torrents.size)); return; }
  if (mobile()) {
    wrap.replaceChildren(h("div", { class: "tcards" }, list.map((x) => h("div", { class: "tcard" + (S.selected.has(x.hash) ? " sel" : ""), "data-h": x.hash,
      onclick: (e) => { selectRow(x.hash, e); S.detailOpen = true; renderDetail(true); } },
    h("div", { class: "t1" }, h("div", { class: "nm" }, x.name), badge(x)), pbar(x),
    h("div", { class: "t3" }, h("span", {}, bytes(x.size)), rate(x.dlspeed, "dl", "down"), rate(x.upspeed, "ul", "up"), x.progress < 1 && x.eta < 8640000 ? h("span", {}, "ETA " + eta(x.eta)) : null)))));
  } else {
    const head = h("tr", {}, cols.map((c) => h("th", { class: (SM_HIDE.has(c) ? "hide-sm " : "") + (NUM_COLS.has(c) ? "num" : ""), scope: "col", tabindex: 0,
      "aria-sort": S.sort.key === c ? (S.sort.dir > 0 ? "ascending" : "descending") : null,
      onclick: () => { S.sort = { key: c, dir: S.sort.key === c ? -S.sort.dir : 1 }; renderTable(); } },
    t("col." + c), S.sort.key === c ? icon(S.sort.dir > 0 ? "chevronUp" : "chevronDown", 12, "sortic") : null)));
    const body = list.map((x) => h("tr", { class: S.selected.has(x.hash) ? "sel" : "", "data-h": x.hash, onclick: (e) => selectRow(x.hash, e), oncontextmenu: (e) => contextMenu(e, x.hash) },
      cols.map((c) => { const td = cell(c, x); if (SM_HIDE.has(c)) td.classList.add("hide-sm"); return td; })));
    wrap.replaceChildren(h("table", {}, h("thead", {}, head), h("tbody", {}, body)));
  }
  wrap.scrollTop = st;
}
function selectRow(hash, e) {
  if (e.shiftKey && S.anchor) {
    const a = shownOrder.indexOf(S.anchor), b = shownOrder.indexOf(hash);
    shownOrder.slice(Math.min(a, b), Math.max(a, b) + 1).forEach((k) => S.selected.add(k));
  } else if (e.ctrlKey || e.metaKey) {
    S.selected.has(hash) ? S.selected.delete(hash) : S.selected.add(hash); S.anchor = hash;
  } else { S.selected = new Set([hash]); S.anchor = hash; }
  S.cur = hash; renderTable(); renderToolbar(); renderDetail(true);
}

// ---------------------------------------------------------------- toolbar + actions
function actionItems() {
  return [
    { label: t("tb.resume"), icon: "play", fn: () => act("resume") }, { label: t("tb.pause"), icon: "pause", fn: () => act("pause") }, "-",
    { label: t("tb.recheck"), icon: "refresh", fn: () => act("recheck") }, { label: t("tb.reannounce"), icon: "rss", fn: () => act("reannounce") }, "-",
    { label: t("tb.top"), icon: "chevronUp", fn: () => act("topPrio") }, { label: t("tb.up"), icon: "up", fn: () => act("increasePrio") },
    { label: t("tb.down"), icon: "down", fn: () => act("decreasePrio") }, { label: t("tb.bottom"), icon: "chevronDown", fn: () => act("bottomPrio") }, "-",
    { label: t("tb.category"), icon: "folder", fn: categoryDialog }, { label: t("tb.tags"), icon: "tag", fn: tagDialog }, "-",
    { label: t("tb.delete"), icon: "trash", danger: true, fn: delDialog },
  ];
}
function renderToolbar() {
  const el = document.getElementById("toolbar");
  if (!el) return;
  const n = S.selected.size, ro = readOnly(), none = n === 0;
  el.replaceChildren(
    h("button", { class: "ghost mobile-only", onclick: () => { S.sideOpen = !S.sideOpen; renderSide(); } }, icon("filter", 16), t("ui.filters")),
    ...(ro ? [] : [
      h("button", { disabled: none, onclick: () => act("resume"), title: t("tb.resume") + " (r)" }, icon("play", 15), h("span", { class: "hide-sm" }, t("tb.resume"))),
      h("button", { disabled: none, onclick: () => act("pause"), title: t("tb.pause") + " (p)" }, icon("pause", 15), h("span", { class: "hide-sm" }, t("tb.pause"))),
      h("button", { class: "danger", disabled: none, onclick: delDialog, title: t("tb.delete") + " (Del)" }, icon("trash", 15), h("span", { class: "hide-sm" }, t("tb.delete"))),
      h("button", { class: "icon-btn", disabled: none, "aria-label": t("tb.recheck"), title: t("tb.recheck"), onclick: (e) => { const r = e.currentTarget.getBoundingClientRect(); menuAt(r.left, r.bottom + 4, actionItems()); } }, icon("more", 16))]),
    h("span", { class: "sep hide-sm" }),
    h("span", { class: "sel" }, n ? `${n} ${t("ui.selected")}` : `${visible().length} ${t("ui.shown")}`),
    h("span", { class: "spacer" }),
    h("button", { class: "ghost sm hide-sm", onclick: colsDialog }, icon("sliders", 14), t("tb.columns")));
}
async function act(name, extra = {}) {
  if (!S.selected.size) return;
  try { await qb("torrents/" + name, { hashes: [...S.selected].join("|"), ...extra }); setTimeout(poll, 300); }
  catch { toast(t("common.error"), true); }
}
function delDialog() {
  if (!S.selected.size) return;
  const cb = h("input", { type: "checkbox" });
  dialog(t("tb.delete"), h("div", {}, h("p", {}, t("del.confirm") + ` (${S.selected.size})`),
    h("label", { class: "field row" }, cb, t("del.files"))),
  [{ label: t("common.cancel") }, { label: t("tb.delete"), cls: "danger", run: async () => {
    await act("delete", { deleteFiles: cb.checked ? "true" : "false" });
    S.selected.clear(); S.cur = null; renderToolbar(); renderDetail(true);
  } }]);
}
function categoryDialog() {
  if (!S.selected.size) return;
  const sel = h("select", {}, h("option", { value: "" }, t("flt.none")), Object.keys(S.categories).map((c) => h("option", { value: c }, c)));
  const nw = h("input", { placeholder: "+ " + t("add.category") });
  dialog(t("tb.category"), h("div", {}, h("div", { class: "field" }, sel), h("div", { class: "field" }, nw)),
    [{ label: t("common.cancel") }, { label: t("common.save"), cls: "primary", run: async () => {
      const name = nw.value.trim() || sel.value;
      if (name && !S.categories[name]) await qb("torrents/createCategory", { category: name, savePath: "" });
      await act("setCategory", { category: name });
    } }]);
}
function tagDialog() {
  if (!S.selected.size) return;
  const inp = h("input", { placeholder: "tag1,tag2" });
  dialog(t("tb.tags"), h("div", { class: "field" }, inp),
    [{ label: t("common.cancel") }, { label: t("common.save"), cls: "primary", run: () => act("addTags", { tags: inp.value }) }]);
}
function colsDialog() {
  const boxes = ALL_COLS.map((c) => h("label", { class: "field row" }, h("input", { type: "checkbox", checked: cols.includes(c), "data-c": c }), t("col." + c)));
  dialog(t("tb.columns"), h("div", {}, boxes), [{ label: t("common.save"), cls: "primary", run: () => {
    cols = ALL_COLS.filter((c) => boxes.some((b) => b.firstChild.dataset.c === c && b.firstChild.checked));
    if (!cols.includes("name")) cols.unshift("name");
    store.set("cols", cols);
    renderTable();
  } }]);
}
function addDialog() {
  const links = h("textarea", { placeholder: "magnet:?xt=urn:btih:…" });
  const files = h("input", { type: "file", multiple: true, accept: ".torrent" });
  const cat = h("select", {}, h("option", { value: "" }, t("flt.none")), Object.keys(S.categories).map((c) => h("option", { value: c }, c)));
  const path = h("input", {});
  const tags = h("input", {});
  const paused = h("input", { type: "checkbox" });
  const seq = h("input", { type: "checkbox" });
  dialog(t("add.title"), h("div", {},
    h("div", { class: "field" }, h("label", {}, t("add.links")), links),
    h("div", { class: "field" }, h("label", {}, t("add.file")), files),
    h("div", { class: "grid2" }, h("div", { class: "field" }, h("label", {}, t("add.category")), cat), h("div", { class: "field" }, h("label", {}, t("add.tags")), tags)),
    h("div", { class: "field" }, h("label", {}, t("add.savepath")), path),
    h("label", { class: "field row" }, paused, t("add.paused")),
    h("label", { class: "field row" }, seq, t("add.sequential"))),
  [{ label: t("add.cancel") }, { label: t("add.submit"), cls: "primary", run: async () => {
    const fd = new FormData();
    fd.set("urls", links.value); fd.set("category", cat.value); fd.set("savepath", path.value); fd.set("tags", tags.value);
    fd.set("paused", paused.checked ? "true" : "false"); fd.set("sequentialDownload", seq.checked ? "true" : "false");
    for (const f of files.files) fd.append("torrents", f, f.name);
    try {
      const r = await qb("torrents/add", fd);
      if (r !== "Ok.") { toast(t("add.failed"), true); return false; }
      toast(t("add.ok")); poll();
    } catch { toast(t("add.failed"), true); return false; }
  } }]);
}
function helpDialog() {
  const rows = [["a", "help.a"], ["p", "help.p"], ["r", "help.r"], ["Del", "help.del"], ["/", "help.slash"],
    ["Ctrl+A", "help.ctrla"], ["j / k", "help.jk"], ["Esc", "help.esc"], ["?", "help.q"]];
  dialog(t("help.title"), h("div", { class: "kv" }, rows.map(([k, v]) => [h("div", {}, h("kbd", {}, k)), h("div", {}, t(v))])),
    [{ label: t("common.close") }]);
}

// ---------------------------------------------------------------- drag & drop
(function dragAndDrop() {
  let depth = 0;
  const zone = () => document.getElementById("drop");
  const hasPayload = (e) => [...(e.dataTransfer?.types || [])].some((x) => x === "Files" || x === "text/uri-list" || x === "text/plain");
  addEventListener("dragenter", (e) => { if (readOnly() || !hasPayload(e)) return; depth++; zone()?.classList.add("on"); });
  addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) zone()?.classList.remove("on"); });
  addEventListener("dragover", (e) => { if (!readOnly() && hasPayload(e)) e.preventDefault(); });
  addEventListener("drop", async (e) => {
    depth = 0; zone()?.classList.remove("on");
    if (readOnly() || !hasPayload(e)) return;
    e.preventDefault();
    const fd = new FormData();
    const files = [...e.dataTransfer.files].filter((f) => f.name.toLowerCase().endsWith(".torrent"));
    files.forEach((f) => fd.append("torrents", f, f.name));
    const text = (e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain") || "").trim();
    const magnets = text.split(/\s+/).filter((x) => x.toLowerCase().startsWith("magnet:"));
    if (magnets.length) fd.set("urls", magnets.join("\n"));
    if (!files.length && !magnets.length) { toast(t("add.failed"), true); return; }
    try { toast((await qb("torrents/add", fd)) === "Ok." ? t("add.ok") : t("add.failed"), false); poll(); } catch { toast(t("add.failed"), true); }
  });
})();

// ---------------------------------------------------------------- detail panel
const TABS = ["general", "trackers", "peers", "files", "speed"];
let detailTimer = 0;
function makeResizer(detail) {
  const r = h("div", { class: "resizer", role: "separator", "aria-orientation": "horizontal", title: "↕" });
  r.addEventListener("pointerdown", (e) => {
    e.preventDefault(); r.setPointerCapture(e.pointerId); r.classList.add("drag");
    const y0 = e.clientY, h0 = detail.offsetHeight;
    const mv = (ev) => document.documentElement.style.setProperty("--detail-h", Math.min(Math.max(h0 + (y0 - ev.clientY), 140), innerHeight - 260) + "px");
    const up = () => {
      r.classList.remove("drag"); r.removeEventListener("pointermove", mv); r.removeEventListener("pointerup", up);
      store.set("detailH", document.documentElement.style.getPropertyValue("--detail-h").trim());
    };
    r.addEventListener("pointermove", mv); r.addEventListener("pointerup", up);
  });
  return r;
}
const copyRow = (v) => h("span", { class: "copy" }, v ?? "", v ? h("button", { class: "ghost sm icon-btn", "aria-label": t("ui.copy"), title: t("ui.copy"),
  onclick: async () => toast((await copyText(String(v))) ? t("common.copied") : t("common.error"), false) }, icon("copy", 13)) : null);
const tile = (k, v) => h("div", { class: "tile" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v));

async function renderDetail(force) {
  const el = document.getElementById("detail");
  if (!el) return;
  const x = S.cur && S.torrents.get(S.cur);
  el.classList.toggle("collapsed", mobile() && !(x && S.detailOpen));
  const tabs = h("div", { class: "tabs", role: "tablist" }, TABS.map((k) => h("button", { class: S.tab === k ? "on" : "", role: "tab", "aria-selected": String(S.tab === k),
    onclick: () => { S.tab = k; renderDetail(true); } }, t("det." + k))));
  const resizer = makeResizer(el);
  if (!x) { el.replaceChildren(resizer, h("div", { class: "dhead" }, h("span", { class: "muted" }, t("det.none"))), tabs, h("div", { class: "dbody" })); return; }
  const now = Date.now();
  if (!force && now - detailTimer < 2500 && S.tab !== "speed") return;
  detailTimer = now;
  const head = h("div", { class: "dhead" }, h("span", { class: "nm", title: x.name }, x.name), badge(x), h("span", { class: "spacer" }),
    h("button", { class: "ghost icon-btn mobile-only", "aria-label": t("common.close"), onclick: () => { S.detailOpen = false; renderDetail(true); } }, icon("x", 17)));
  let body;
  try {
    if (S.tab === "general") {
      const p = await qb("torrents/properties?hash=" + x.hash);
      const kv = [[t("det.hash"), copyRow(x.hash)], [t("det.path"), copyRow(p.save_path)],
        [t("det.pieces"), `${p.pieces_have}/${p.pieces_num} × ${bytes(p.piece_size)}`], [t("det.seedtime"), eta(p.seeding_time)],
        [t("det.connections"), p.nb_connections], [t("col.added_on"), date(p.addition_date)], [t("det.comment"), p.comment],
        [t("det.private"), p.isPrivate ? t("common.yes") : t("common.no")]];
      body = h("div", { class: "pad" },
        h("div", { class: "tiles" }, tile(t("col.size"), bytes(p.total_size)), tile(t("det.downloaded"), bytes(p.total_downloaded)),
          tile(t("det.uploaded"), bytes(p.total_uploaded)), tile(t("col.ratio"), (p.share_ratio ?? 0).toFixed(2)),
          tile(t("col.eta"), x.progress >= 1 ? "—" : eta(x.eta)), tile(`${t("col.num_seeds")} / ${t("col.num_leechs")}`, `${x.num_seeds ?? 0} / ${x.num_leechs ?? 0}`)),
        h("div", { class: "kv" }, kv.map(([k, v]) => [h("div", {}, k), h("div", { style: "word-break:break-all" }, v ?? "")])));
    } else if (S.tab === "trackers") {
      const tr = await qb("torrents/trackers?hash=" + x.hash);
      const names = ["trk.disabled", "trk.notcontacted", "trk.working", "trk.updating", "trk.notworking"];
      const url = h("input", { placeholder: "udp://…", style: "min-width:260px" });
      body = h("div", { class: "pad" }, h("table", {}, h("thead", {}, h("tr", {}, ["trk.url", "trk.status", "trk.msg"].map((k) => h("th", {}, t(k))))),
        h("tbody", {}, tr.map((r) => h("tr", {}, h("td", {}, r.url), h("td", {}, t(names[r.status] || names[1])), h("td", {}, r.msg),
          readOnly() || r.url.startsWith("**") ? null : h("td", {}, btn(t("trk.remove"), async () => {
            await qb("torrents/removeTrackers", { hash: x.hash, urls: r.url }); renderDetail(true);
          }, "sm danger")))))),
      readOnly() ? null : h("div", { class: "actions", style: "justify-content:flex-start" }, url, btn(t("trk.add"), async () => {
        if (url.value) { await qb("torrents/addTrackers", { hash: x.hash, urls: url.value }); renderDetail(true); }
      })));
    } else if (S.tab === "peers") {
      const ps = await nova("peers?hash=" + x.hash);
      body = h("div", { class: "pad" }, h("table", {}, h("thead", {}, h("tr", {}, ["peer.ip", "peer.client", "peer.progress", "peer.down", "peer.up"].map((k) => h("th", {}, t(k))))),
        h("tbody", {}, ps.map((p) => h("tr", {}, h("td", {}, `${p.ip}:${p.port}`), h("td", {}, p.client), h("td", {}, (p.progress * 100).toFixed(0) + "%"),
          h("td", { class: "num" }, speed(p.dl_speed)), h("td", { class: "num" }, speed(p.up_speed)),
          readOnly() ? null : h("td", {}, btn(t("peer.ban"), async () => { await nova("ban", "POST", { ip: p.ip }); toast(p.ip); }, "sm")))))));
    } else if (S.tab === "files") {
      const fs = await qb("torrents/files?hash=" + x.hash);
      body = h("div", { class: "pad" }, h("table", {}, h("thead", {}, h("tr", {}, ["file.name", "file.size", "file.progress", "file.prio"].map((k) => h("th", {}, t(k))))),
        h("tbody", {}, fs.map((f) => h("tr", {}, h("td", { title: f.name }, f.name), h("td", { class: "num" }, bytes(f.size)), h("td", { class: "num" }, (f.progress * 100).toFixed(1) + "%"),
          h("td", {}, h("select", { disabled: readOnly(), onchange: async (e) => { await qb("torrents/filePrio", { hash: x.hash, id: f.index, priority: e.target.value }); } },
            [0, 1, 6, 7].map((p) => h("option", { value: p, selected: f.priority === p }, t("prio." + p))))))))));
    } else {
      const hist = S.torHist.get(x.hash) || [];
      body = h("div", { class: "pad" }, lineChart({ series: [{ name: t("bar.down"), colorVar: "--s1", values: hist.map((v) => v[0]) }, { name: t("bar.up"), colorVar: "--s2", values: hist.map((v) => v[1]) }],
        secondsPerPoint: 1.5, fmt: (v) => speed(v) || "0", minMax: 4096, height: 180, labels: chartLabels() }));
    }
  } catch { body = h("div", { class: "pad bad" }, t("common.error")); }
  el.replaceChildren(resizer, head, tabs, h("div", { class: "dbody" }, body));
}
const chartLabels = () => ({ now: t("stats.now"), waiting: t("stats.waiting"), aria: t("stats.aria"), table: t("stats.table"), chart: t("stats.chart"), time: t("stats.time") });

// ---------------------------------------------------------------- stats + logs
const GROUPS = [["downloading", "--dl", (x) => kindOf(x.state) === "dl"], ["seeding", "--ok", (x) => kindOf(x.state) === "up" || (kindOf(x.state) === "idle" && x.progress >= 1)],
  ["paused", "--idle", (x) => kindOf(x.state) === "pause" || kindOf(x.state) === "queue"], ["stalled", "--warn", (x) => kindOf(x.state) === "warn"], ["errored", "--bad", (x) => kindOf(x.state) === "bad"]];
let statsAt = 0, statsHover = false;
async function buildStats() {
  const el = document.getElementById("statspage");
  if (!el) return;
  statsAt = 0;
  el.replaceChildren(h("h2", {}, t("stats.title")), h("div", { id: "stats-body", class: "center-col" }));
  await updateStats(true);
}
async function updateStats(force) {
  const el = document.getElementById("stats-body");
  if (!el || statsHover) return;
  if (!force && Date.now() - statsAt < 4000) return;
  statsAt = Date.now();
  let st = {};
  try { st = await nova("stats"); } catch { return; }
  const parts = GROUPS.map(([k, c, fn]) => ({ name: t("st." + k), value: [...S.torrents.values()].filter(fn).length, color: `var(${c})` }));
  const chart = lineChart({ series: [{ name: t("bar.down"), colorVar: "--s1", values: S.speedHist.map((v) => v[0]) }, { name: t("bar.up"), colorVar: "--s2", values: S.speedHist.map((v) => v[1]) }],
    secondsPerPoint: 1.5, fmt: (v) => speed(v) || "0", minMax: 4096, labels: chartLabels() });
  const box = h("section", {}, h("h3", {}, t("stats.graph")), chart);
  box.addEventListener("pointerenter", () => { statsHover = true; });
  box.addEventListener("pointerleave", () => { statsHover = false; });
  el.replaceChildren(
    h("div", { class: "cards4" }, tile(t("stats.torrents"), st.torrents), tile(t("stats.alltime_dl"), bytes(st.alltime_dl)), tile(t("stats.alltime_ul"), bytes(st.alltime_ul)),
      tile(t("stats.ratio"), st.global_ratio), tile(t("stats.free"), bytes(st.free_space))),
    box, h("section", {}, h("h3", {}, t("stats.by_state")), segBar(parts)));
}
async function renderLogs() {
  const el = document.getElementById("logspage");
  if (!el) return;
  let logs = [];
  try { logs = await nova("logs"); } catch { /* shown as empty */ }
  const lv = { 1: "", 2: "", 4: "warn", 8: "bad" };
  const lines = logs.slice(-300).reverse().map((l) => `${new Date(l.timestamp).toLocaleTimeString()}  ${l.message}`);
  el.replaceChildren(h("div", { class: "chart-head" }, h("h2", { style: "margin:0" }, t("logs.title")), h("div", { class: "actions", style: "margin:0" },
    btn(t("ui.copy"), async () => toast((await copyText(lines.join("\n"))) ? t("common.copied") : t("common.error"), false), "ghost sm", null, "copy"),
    btn(t("logs.refresh"), renderLogs, "sm", null, "refresh"))),
  h("section", { class: "center-col" }, logs.length ? h("pre", {}, logs.slice(-300).reverse().map((l) =>
    h("div", { class: lv[l.type] || "" }, `${new Date(l.timestamp).toLocaleTimeString()}  ${l.message}`))) : h("p", { class: "muted" }, t("logs.empty"))));
}

// ---------------------------------------------------------------- keyboard
document.addEventListener("keydown", (e) => {
  const tag = (e.target.tagName || "").toLowerCase();
  if (e.key === "Escape") { closeDialogs(); S.sideOpen = false; document.activeElement?.blur?.(); return; }
  if (["input", "textarea", "select"].includes(tag) || document.querySelector(".dialog-bg") || S.view !== "torrents") return;
  if (e.ctrlKey && e.key.toLowerCase() === "a") { e.preventDefault(); shownOrder.forEach((k) => S.selected.add(k)); renderTable(); renderToolbar(); return; }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const ro = readOnly();
  if (e.key === "a" && !ro) { e.preventDefault(); addDialog(); }
  else if (e.key === "p" && !ro) act("pause");
  else if (e.key === "r" && !ro) act("resume");
  else if (e.key === "Delete" && !ro) delDialog();
  else if (e.key === "/") { e.preventDefault(); document.getElementById("search")?.focus(); }
  else if (e.key === "?") helpDialog();
  else if (e.key === "j" || e.key === "k") {
    const i = shownOrder.indexOf(S.cur), n = Math.min(Math.max(i + (e.key === "j" ? 1 : -1), 0), shownOrder.length - 1);
    if (shownOrder[n]) { S.cur = shownOrder[n]; S.selected = new Set([S.cur]); renderTable(); renderToolbar(); renderDetail(true); }
  }
});
addEventListener("nova:prefs", (e) => { Object.assign(S.prefs, e.detail); renderStrip(); });
mq.addEventListener("change", () => { if (document.getElementById("main")) { renderStrip(); if (S.view === "torrents") { renderTable(); renderDetail(true); } } });

// ---------------------------------------------------------------- start
async function start() {
  try {
    const s = await nova("session");
    setCsrf(s.csrf); S.role = s.role;
  } catch { return showLogin(); }
  try { S.prefs = await qb("app/preferences"); } catch { S.prefs = {}; }
  S.rid = 0; S.loaded = false;
  shell();
  await poll();
  clearInterval(S.timer);
  S.timer = setInterval(() => { if (!document.hidden) poll(); }, 1500);
}
(async () => { await loadI18n(); start(); })();
