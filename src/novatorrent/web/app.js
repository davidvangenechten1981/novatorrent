import { nova, qb, setCsrf } from "./api.js";
import { renderSettings } from "./settings.js";
import {
  bytes, closeDialogs, date, dialog, drawGraph, eta, getLang, h, loadI18n, setLang, speed, t, toast,
} from "./ui.js";

const root = document.getElementById("root");
const S = {
  torrents: new Map(), categories: {}, tags: [], server: {}, rid: 0, selected: new Set(), cur: null,
  filter: { status: "all", category: null, tag: null, tracker: null }, search: "",
  sort: { key: "added_on", dir: -1 }, tab: "general", view: "torrents", role: "admin", anchor: null,
  speedHist: [], torHist: new Map(), timer: null, sideOpen: false,
};
const ALL_COLS = ["name", "size", "progress", "state", "dlspeed", "upspeed", "eta", "ratio", "category",
  "tags", "added_on", "num_seeds", "num_leechs", "save_path"];
const NUM_COLS = new Set(["size", "dlspeed", "upspeed", "eta", "ratio", "num_seeds", "num_leechs"]);
const SM_HIDE = new Set(["size", "ratio", "tags", "added_on", "num_seeds", "num_leechs", "save_path", "upspeed", "eta", "category"]);
let cols = ["name", "size", "progress", "state", "dlspeed", "upspeed", "eta", "ratio", "category"];
try { cols = JSON.parse(localStorage.getItem("cols") || "null") || cols; } catch { /* ignore */ }

const readOnly = () => S.role !== "admin";

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("theme", theme); } catch { /* ignore */ }
}
let savedTheme = null;
try { savedTheme = localStorage.getItem("theme"); } catch { /* ignore */ }
applyTheme(savedTheme || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));

// ---------------------------------------------------------------- login
function showLogin(msg) {
  clearInterval(S.timer);
  const user = h("input", { name: "u", autocomplete: "username", required: true });
  const pass = h("input", { name: "p", type: "password", autocomplete: "current-password", required: true });
  const err = h("div", { class: "bad" }, msg || "");
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
  }, h("h2", {}, t("app.title") + " — " + t("login.title")),
  h("label", {}, t("login.user")), user, h("label", {}, t("login.pass")), pass, err,
  h("button", { class: "primary" }, t("login.submit")))));
  user.focus();
}

// ---------------------------------------------------------------- data
async function poll() {
  try {
    const d = await qb("sync/maindata?rid=" + S.rid);
    if (d.full_update) { S.torrents.clear(); S.categories = {}; S.tags = []; S.server = {}; }
    S.rid = d.rid;
    for (const [k, v] of Object.entries(d.torrents || {})) S.torrents.set(k, { ...(S.torrents.get(k) || { hash: k }), ...v });
    for (const k of d.torrents_removed || []) { S.torrents.delete(k); S.selected.delete(k); }
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
    if (S.view === "torrents") { renderSide(); renderTable(); renderDetail(); }
    if (S.view === "stats") renderStats();
    renderStatus();
  } catch (e) {
    if (e.status === 403) showLogin();
  }
}

// ---------------------------------------------------------------- layout
function shell() {
  root.replaceChildren(h("div", { class: "app" },
    h("div", { class: "top", id: "top" }),
    h("div", { class: "main", id: "main" }),
    h("div", { class: "status", id: "status" })));
  renderTop();
  showView("torrents");
}

function btn(label, fn, cls = "", title) { return h("button", { class: cls, title: title || label, onclick: fn }, label); }

function renderTop() {
  const top = document.getElementById("top");
  const ro = readOnly();
  const search = h("input", { type: "search", id: "search", placeholder: t("tb.search"), value: S.search,
    oninput: (e) => { S.search = e.target.value; renderTable(); } });
  top.replaceChildren(
    btn("☰", () => { S.sideOpen = !S.sideOpen; renderSide(); }, "mobile-only"),
    h("span", { class: "brand" }, t("app.title")),
    btn(t("nav.torrents"), () => showView("torrents")),
    ...(ro ? [] : [btn("+ " + t("tb.add"), addDialog, "primary"), btn(t("tb.resume"), () => act("resume")),
      btn(t("tb.pause"), () => act("pause")), btn(t("tb.delete"), delDialog, "danger"),
      btn("⋯", moreMenu, "", t("tb.recheck"))]),
    search, h("span", { class: "spacer" }),
    btn(t("nav.stats"), () => showView("stats")), btn(t("nav.logs"), () => showView("logs")),
    btn(t("nav.settings"), () => showView("settings")),
    btn("◐", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"), "", t("tb.theme")),
    btn(getLang().toUpperCase(), () => { setLang(getLang() === "en" ? "nl" : "en"); shell(); poll(); }, "", t("tb.lang")),
    btn("?", helpDialog, "", t("tb.help")),
    btn(t("nav.logout"), async () => { await qb("auth/logout", {}, { quiet: true }); showLogin(); }));
}

function moreMenu(ev) {
  document.querySelectorAll(".menu").forEach((m) => m.remove());
  const items = [["tb.recheck", () => act("recheck")], ["tb.reannounce", () => act("reannounce")],
    ["tb.top", () => act("topPrio")], ["tb.up", () => act("increasePrio")], ["tb.down", () => act("decreasePrio")],
    ["tb.bottom", () => act("bottomPrio")], ["tb.category", categoryDialog], ["tb.tags", tagDialog], ["tb.columns", colsDialog]];
  const r = ev.currentTarget.getBoundingClientRect();
  const m = h("div", { class: "menu", style: `left:${r.left}px;top:${r.bottom + 4}px` },
    items.map(([k, fn]) => h("button", { onclick: () => { m.remove(); fn(); } }, t(k))));
  document.body.append(m);
  setTimeout(() => document.addEventListener("click", () => m.remove(), { once: true }), 0);
}

function showView(v) {
  S.view = v;
  const main = document.getElementById("main");
  if (v === "torrents") {
    main.replaceChildren(h("div", { class: "side", id: "side" }),
      h("div", { class: "center" }, h("div", { class: "tablewrap", id: "tablewrap" }), h("div", { class: "detail", id: "detail" })));
    renderSide(); renderTable(); renderDetail();
  } else if (v === "settings") {
    main.replaceChildren(h("div", { class: "page", style: "grid-column:1/-1" }));
    renderSettings(main.firstChild, { role: S.role });
  } else if (v === "stats") {
    main.replaceChildren(h("div", { class: "page", id: "statspage", style: "grid-column:1/-1" })); renderStats();
  } else if (v === "logs") {
    main.replaceChildren(h("div", { class: "page", id: "logspage", style: "grid-column:1/-1" })); renderLogs();
  }
}

// ---------------------------------------------------------------- sidebar + filters
const STATUS = ["all", "downloading", "seeding", "completed", "paused", "active", "inactive", "stalled", "errored"];
function trackerHost(x) { try { return new URL(x).hostname; } catch { return ""; } }

function renderSide() {
  const side = document.getElementById("side");
  if (!side) return;
  side.classList.toggle("open", S.sideOpen);
  const list = [...S.torrents.values()];
  const matches = { all: () => true, downloading: (x) => /DL$|metaDL|MetaDL/.test(x.state) && !x.state.startsWith("paused"),
    seeding: (x) => /UP$|uploading/.test(x.state) && !x.state.startsWith("paused"), completed: (x) => x.progress >= 1,
    paused: (x) => x.state.startsWith("paused"), active: (x) => x.dlspeed > 0 || x.upspeed > 0,
    inactive: (x) => x.dlspeed === 0 && x.upspeed === 0, stalled: (x) => x.state.startsWith("stalled"),
    errored: (x) => ["error", "missingFiles"].includes(x.state) };
  const item = (label, count, on, fn) => h("div", { class: "item" + (on ? " on" : ""), onclick: () => { fn(); S.sideOpen = false; renderSide(); renderTable(); } },
    h("span", {}, label), h("span", { class: "muted" }, count));
  const trackers = {};
  list.forEach((x) => { const hn = trackerHost(x.tracker); if (hn) trackers[hn] = (trackers[hn] || 0) + 1; });
  side.replaceChildren(...[
    h("h4", {}, t("flt.status")),
    STATUS.map((s) => item(t("st." + s), list.filter(matches[s]).length, S.filter.status === s, () => { S.filter.status = s; })),
    h("h4", {}, t("flt.category")),
    item(t("flt.all"), list.length, S.filter.category === null, () => { S.filter.category = null; }),
    item(t("flt.none"), list.filter((x) => !x.category).length, S.filter.category === "", () => { S.filter.category = ""; }),
    Object.keys(S.categories).sort().map((c) => item(c, list.filter((x) => x.category === c).length, S.filter.category === c, () => { S.filter.category = c; })),
    h("h4", {}, t("flt.tag")),
    item(t("flt.all"), list.length, S.filter.tag === null, () => { S.filter.tag = null; }),
    S.tags.map((g) => item(g, list.filter((x) => (x.tags || "").split(", ").includes(g)).length, S.filter.tag === g, () => { S.filter.tag = g; })),
    h("h4", {}, t("flt.tracker")),
    item(t("flt.all"), list.length, S.filter.tracker === null, () => { S.filter.tracker = null; }),
    Object.entries(trackers).map(([k, n]) => item(k, n, S.filter.tracker === k, () => { S.filter.tracker = k; }))].flat());
  S._matches = matches;
}

function visible() {
  const q = S.search.toLowerCase(), f = S.filter;
  const m = S._matches || { all: () => true };
  let list = [...S.torrents.values()].filter((x) => (m[f.status] || m.all)(x)
    && (f.category === null || x.category === f.category)
    && (f.tag === null || (x.tags || "").split(", ").includes(f.tag))
    && (f.tracker === null || trackerHost(x.tracker) === f.tracker)
    && (!q || (x.name || "").toLowerCase().includes(q)));
  const { key, dir } = S.sort;
  list.sort((a, b) => (a[key] > b[key] ? 1 : a[key] < b[key] ? -1 : 0) * dir);
  return list;
}

// ---------------------------------------------------------------- table
let shownOrder = [];
function cell(c, x) {
  switch (c) {
    case "name": return h("td", { title: x.name }, x.name);
    case "size": return h("td", { class: "num" }, bytes(x.size));
    case "progress": return h("td", {}, h("div", { class: "bar" + (x.progress >= 1 ? " done" : "") },
      h("i", { style: `width:${(x.progress * 100).toFixed(1)}%` }), h("span", {}, (x.progress * 100).toFixed(1) + "%")));
    case "state": return h("td", { class: x.state === "error" || x.state === "missingFiles" ? "bad" : "" }, t("state." + x.state));
    case "dlspeed": case "upspeed": return h("td", { class: "num" }, speed(x[c]));
    case "eta": return h("td", { class: "num" }, x.progress >= 1 ? "" : eta(x.eta));
    case "ratio": return h("td", { class: "num" }, (x.ratio ?? 0).toFixed(2));
    case "added_on": return h("td", {}, date(x.added_on));
    default: return h("td", { class: NUM_COLS.has(c) ? "num" : "" }, x[c] ?? "");
  }
}
function renderTable() {
  const wrap = document.getElementById("tablewrap");
  if (!wrap) return;
  const list = visible();
  shownOrder = list.map((x) => x.hash);
  const head = h("tr", {}, cols.map((c) => h("th", { class: SM_HIDE.has(c) ? "hide-sm" : "", onclick: () => {
    S.sort = { key: c, dir: S.sort.key === c ? -S.sort.dir : 1 }; renderTable();
  } }, t("col." + c) + (S.sort.key === c ? (S.sort.dir > 0 ? " ▲" : " ▼") : ""))));
  const body = list.map((x) => {
    const tr = h("tr", { class: (S.selected.has(x.hash) ? "sel " : "") + (S.cur === x.hash ? "cur" : ""), "data-h": x.hash,
      onclick: (e) => selectRow(x.hash, e) }, cols.map((c) => {
      const td = cell(c, x); if (SM_HIDE.has(c)) td.classList.add("hide-sm"); return td;
    }));
    return tr;
  });
  const st = wrap.scrollTop;
  wrap.replaceChildren(h("table", {}, h("thead", {}, head), h("tbody", {}, body)));
  wrap.scrollTop = st;
}
function selectRow(hash, e) {
  if (e.shiftKey && S.anchor) {
    const a = shownOrder.indexOf(S.anchor), b = shownOrder.indexOf(hash);
    shownOrder.slice(Math.min(a, b), Math.max(a, b) + 1).forEach((k) => S.selected.add(k));
  } else if (e.ctrlKey || e.metaKey) {
    S.selected.has(hash) ? S.selected.delete(hash) : S.selected.add(hash); S.anchor = hash;
  } else { S.selected = new Set([hash]); S.anchor = hash; }
  S.cur = hash; renderTable(); renderDetail(true);
}

// ---------------------------------------------------------------- actions
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
    S.selected.clear(); S.cur = null; renderDetail(true);
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
    try { localStorage.setItem("cols", JSON.stringify(cols)); } catch { /* ignore */ }
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
    h("div", { class: "field" }, h("label", {}, t("add.category")), cat),
    h("div", { class: "field" }, h("label", {}, t("add.savepath")), path),
    h("div", { class: "field" }, h("label", {}, t("add.tags")), tags),
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

// ---------------------------------------------------------------- detail panel
const TABS = ["general", "trackers", "peers", "files", "speed"];
let detailTimer = 0;
async function renderDetail(force) {
  const el = document.getElementById("detail");
  if (!el) return;
  const x = S.cur && S.torrents.get(S.cur);
  const tabs = h("div", { class: "tabs" }, TABS.map((k) => h("button", { class: S.tab === k ? "on" : "", onclick: () => { S.tab = k; renderDetail(true); } }, t("det." + k))));
  if (!x) { el.replaceChildren(tabs, h("div", { class: "pad muted" }, t("det.none"))); return; }
  const now = Date.now();
  if (!force && now - detailTimer < 2500 && S.tab !== "speed") return;
  detailTimer = now;
  let body;
  try {
    if (S.tab === "general") {
      const p = await qb("torrents/properties?hash=" + x.hash);
      const kv = [[t("det.hash"), x.hash], [t("det.path"), p.save_path], [t("col.size"), bytes(p.total_size)],
        [t("det.pieces"), `${p.pieces_have}/${p.pieces_num} × ${bytes(p.piece_size)}`], [t("det.downloaded"), bytes(p.total_downloaded)],
        [t("det.uploaded"), bytes(p.total_uploaded)], [t("col.ratio"), (p.share_ratio ?? 0).toFixed(2)],
        [t("det.seedtime"), eta(p.seeding_time)], [t("det.connections"), p.nb_connections], [t("col.added_on"), date(p.addition_date)],
        [t("det.comment"), p.comment], [t("det.private"), p.isPrivate ? t("common.yes") : t("common.no")]];
      body = h("div", { class: "pad kv" }, kv.map(([k, v]) => [h("div", {}, k), h("div", { style: "word-break:break-all" }, v ?? "")]));
    } else if (S.tab === "trackers") {
      const tr = await qb("torrents/trackers?hash=" + x.hash);
      const names = ["trk.disabled", "trk.notcontacted", "trk.working", "trk.updating", "trk.notworking"];
      const url = h("input", { placeholder: "udp://…", style: "min-width:260px" });
      body = h("div", { class: "pad" }, h("table", {}, h("tr", {}, ["trk.url", "trk.status", "trk.msg"].map((k) => h("th", {}, t(k)))),
        tr.map((r) => h("tr", {}, h("td", {}, r.url), h("td", {}, t(names[r.status] || names[1])), h("td", {}, r.msg),
          readOnly() || r.url.startsWith("**") ? null : h("td", {}, btn(t("trk.remove"), async () => {
            await qb("torrents/removeTrackers", { hash: x.hash, urls: r.url }); renderDetail(true);
          }))))),
      readOnly() ? null : h("div", { style: "margin-top:8px" }, url, btn(t("trk.add"), async () => {
        if (url.value) { await qb("torrents/addTrackers", { hash: x.hash, urls: url.value }); renderDetail(true); }
      })));
    } else if (S.tab === "peers") {
      const ps = await nova("peers?hash=" + x.hash);
      body = h("div", { class: "pad" }, h("table", {}, h("tr", {}, ["peer.ip", "peer.client", "peer.progress", "peer.down", "peer.up"].map((k) => h("th", {}, t(k)))),
        ps.map((p) => h("tr", {}, h("td", {}, `${p.ip}:${p.port}`), h("td", {}, p.client), h("td", {}, (p.progress * 100).toFixed(0) + "%"),
          h("td", { class: "num" }, speed(p.dl_speed)), h("td", { class: "num" }, speed(p.up_speed)),
          readOnly() ? null : h("td", {}, btn(t("peer.ban"), async () => { await nova("ban", "POST", { ip: p.ip }); toast(p.ip); }))))));
    } else if (S.tab === "files") {
      const fs = await qb("torrents/files?hash=" + x.hash);
      body = h("div", { class: "pad" }, h("table", {}, h("tr", {}, ["file.name", "file.size", "file.progress", "file.prio"].map((k) => h("th", {}, t(k)))),
        fs.map((f) => h("tr", {}, h("td", { title: f.name }, f.name), h("td", { class: "num" }, bytes(f.size)), h("td", { class: "num" }, (f.progress * 100).toFixed(1) + "%"),
          h("td", {}, h("select", { disabled: readOnly(), onchange: async (e) => { await qb("torrents/filePrio", { hash: x.hash, id: f.index, priority: e.target.value }); } },
            [0, 1, 6, 7].map((p) => h("option", { value: p, selected: f.priority === p }, t("prio." + p)))))))));
    } else {
      const c = h("canvas", {});
      body = h("div", { class: "pad" }, c);
      requestAnimationFrame(() => { const hist = S.torHist.get(x.hash) || []; drawGraph(c, [hist.map((v) => v[0]), hist.map((v) => v[1])], ["--accent", "--ok"]); });
    }
  } catch { body = h("div", { class: "pad bad" }, t("common.error")); }
  el.replaceChildren(tabs, body);
}

// ---------------------------------------------------------------- status, stats, logs
function renderStatus() {
  const el = document.getElementById("status");
  if (!el) return;
  const s = S.server;
  el.replaceChildren(
    h("span", {}, t("bar.down") + ": ", h("b", {}, speed(s.dl_info_speed) || "0")),
    h("span", {}, t("bar.up") + ": ", h("b", {}, speed(s.up_info_speed) || "0")),
    h("span", {}, t("bar.torrents") + ": ", h("b", {}, S.torrents.size)),
    h("span", {}, t("bar.peers") + ": ", h("b", {}, s.total_peer_connections ?? 0)),
    h("span", {}, t("bar.free") + ": ", h("b", {}, bytes(s.free_space_on_disk))),
    h("span", {}, t("bar.vpn") + ": ", h("b", { class: s.vpn_ok === false ? "bad" : "ok" }, s.vpn_ok === false ? t("bar.vpn_down") : t("bar.vpn_ok")),
      s.vpn_ok === false && s.vpn_reason ? ` (${s.vpn_reason})` : ""));
}
async function renderStats() {
  const el = document.getElementById("statspage");
  if (!el) return;
  let st = {};
  try { st = await nova("stats"); } catch { return; }
  const c = h("canvas", {});
  el.replaceChildren(h("h2", {}, t("stats.title")), h("section", {}, h("div", { class: "kv" },
    [[t("stats.torrents"), st.torrents], [t("stats.alltime_dl"), bytes(st.alltime_dl)], [t("stats.alltime_ul"), bytes(st.alltime_ul)],
      [t("stats.ratio"), st.global_ratio], [t("stats.free"), bytes(st.free_space)]].map(([k, v]) => [h("div", {}, k), h("div", {}, v)]))),
  h("section", {}, h("h3", {}, t("stats.graph")), c));
  requestAnimationFrame(() => drawGraph(c, [S.speedHist.map((v) => v[0]), S.speedHist.map((v) => v[1])], ["--accent", "--ok"]));
}
async function renderLogs() {
  const el = document.getElementById("logspage");
  if (!el) return;
  const logs = await nova("logs");
  const lv = { 1: "", 2: "", 4: "warn", 8: "bad" };
  el.replaceChildren(h("h2", {}, t("logs.title")), logs.length ? h("pre", {}, logs.slice(-300).reverse().map((l) =>
    h("div", { class: lv[l.type] || "" }, `${new Date(l.timestamp).toLocaleTimeString()}  ${l.message}`))) : h("p", {}, t("logs.empty")));
}

// ---------------------------------------------------------------- keyboard
document.addEventListener("keydown", (e) => {
  const tag = (e.target.tagName || "").toLowerCase();
  if (e.key === "Escape") { closeDialogs(); document.activeElement?.blur?.(); return; }
  if (["input", "textarea", "select"].includes(tag) || document.querySelector(".dialog-bg") || S.view !== "torrents") return;
  if (e.ctrlKey && e.key.toLowerCase() === "a") { e.preventDefault(); shownOrder.forEach((k) => S.selected.add(k)); renderTable(); return; }
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
    if (shownOrder[n]) { S.cur = shownOrder[n]; S.selected = new Set([S.cur]); renderTable(); renderDetail(true); }
  }
});

// ---------------------------------------------------------------- start
async function start() {
  try {
    const s = await nova("session");
    setCsrf(s.csrf); S.role = s.role;
  } catch { return showLogin(); }
  S.rid = 0;
  shell();
  await poll();
  clearInterval(S.timer);
  S.timer = setInterval(() => { if (!document.hidden) poll(); }, 1500);
}
(async () => { await loadI18n(); start(); })();
