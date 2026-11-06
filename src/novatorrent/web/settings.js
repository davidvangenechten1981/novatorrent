import { nova, qb } from "./api.js";
import { icon } from "./icons.js";
import { dialog, h, t, toast } from "./ui.js";

const sel = (...opts) => ({ type: "select", opts });
const DAYS = sel([0, "opt.alldays"], [1, "opt.weekdays"], [2, "opt.weekend"], [3, "opt.mon"], [4, "opt.tue"], [5, "opt.wed"],
  [6, "opt.thu"], [7, "opt.fri"], [8, "opt.sat"], [9, "opt.sun"]);
const B = { type: "bool" }, I = { type: "int" }, F = { type: "float" }, T = { type: "text" };

// section -> [key, kind]. Labels come from i18n key set.<key>; sections from sec.<name>.
const SCHEMA = {
  downloads: [["save_path", T], ["temp_path_enabled", B], ["temp_path", T], ["start_paused_enabled", B], ["preallocate_all", B],
    ["nova_min_free_space_mb", I], ["nova_warn_free_space_mb", I], ["nova_sequential_default", B]],
  limits: [["dl_limit", I], ["up_limit", I], ["alt_dl_limit", I], ["alt_up_limit", I], ["alt_speed_active", B], ["scheduler_enabled", B],
    ["schedule_from_hour", I], ["schedule_from_min", I], ["schedule_to_hour", I], ["schedule_to_min", I], ["scheduler_days", DAYS],
    ["max_connec", I], ["max_connec_per_torrent", I], ["max_uploads", I], ["max_uploads_per_torrent", I],
    ["nova_max_peerlist_size", I]],
  queue: [["queueing_enabled", B], ["max_active_downloads", I], ["max_active_uploads", I], ["max_active_torrents", I],
    ["max_active_checking_torrents", I], ["dont_count_slow_torrents", B], ["slow_torrent_dl_rate_threshold", I], ["slow_torrent_ul_rate_threshold", I]],
  seeding: [["max_ratio_enabled", B], ["max_ratio", F], ["max_seeding_time_enabled", B], ["max_seeding_time", I],
    ["max_inactive_seeding_time_enabled", B], ["max_inactive_seeding_time", I],
    ["max_ratio_act", sel([0, "opt.pause"], [1, "opt.remove"], [2, "opt.removefiles"])]],
  network: [["listen_port", I], ["random_port", B], ["upnp", B], ["dht", B], ["pex", B], ["lsd", B], ["utp_enabled", B], ["tcp_enabled", B],
    ["encryption", sel([0, "opt.pref"], [1, "opt.req"], [2, "opt.off"])], ["anonymous_mode", B],
    ["current_network_interface", T], ["current_interface_address", T], ["nova_require_vpn_binding", B], ["nova_port_file", T],
    ["nova_ipv4", B], ["nova_ipv6", B]],
  proxy: [["proxy_type", sel(["None", "opt.none"], ["SOCKS5", "SOCKS5"], ["HTTP", "HTTP"])], ["proxy_ip", T], ["proxy_port", I],
    ["proxy_username", T], ["proxy_password", { type: "password" }], ["proxy_peer_connections", B]],
  ipfilter: [["ip_filter_enabled", B], ["ip_filter_path", T], ["nova_ip_filter_url", T], ["nova_ip_filter_interval_h", I]],
  trackers: [["nova_extra_trackers_enabled", B], ["nova_extra_trackers", { type: "area" }]],
  hooks: [["nova_hook_enabled", B], ["nova_hook_command", T]],
};
const NOTIFY_FIELDS = ["discord_url", "ntfy_url", "telegram_token", "telegram_chat", "webhook_url"];
const EVENTS = ["added", "completed", "error", "disk_low", "disk_full", "vpn_down", "vpn_up", "seed_limit"];

function control(kind, value, onchange) {
  if (kind.type === "bool") return h("input", { type: "checkbox", checked: !!value, onchange: (e) => onchange(e.target.checked) });
  if (kind.type === "select") {
    return h("select", { onchange: (e) => onchange(isNaN(+e.target.value) || e.target.value === "" ? e.target.value : +e.target.value) },
      kind.opts.map(([v, l]) => h("option", { value: v, selected: String(v) === String(value) }, l.includes(".") ? t(l) : l)));
  }
  if (kind.type === "area") return h("textarea", { oninput: (e) => onchange(e.target.value) }, value ?? "");
  const num = kind.type === "int" || kind.type === "float";
  return h("input", { type: kind.type === "password" ? "password" : num ? "number" : "text", step: kind.type === "float" ? "0.1" : null,
    value: kind.type === "password" ? "" : value ?? "", autocomplete: "off",
    oninput: (e) => onchange(num ? (kind.type === "int" ? parseInt(e.target.value, 10) : parseFloat(e.target.value)) : e.target.value) });
}

function field(label, ctl, bool) {
  return h("div", { class: "field" + (bool ? " row" : "") }, bool ? [ctl, h("label", {}, label)] : [h("label", {}, label), ctl]);
}

export async function renderSettings(el, { role }) {
  const prefs = await qb("app/preferences");
  const changed = {};
  const notify = { ...(prefs.nova_notify || {}), events: { ...(prefs.nova_notify?.events || {}) } };
  let notifyChanged = false;
  const ro = role !== "admin";
  const msg = h("span", { class: "msg" }, t("set.clean"));
  const savebar = h("div", { class: "savebar" });
  const dirty = () => { savebar.classList.add("dirty"); msg.textContent = t("set.dirty"); };
  const on = (key) => (v) => { changed[key] = v; dirty(); };

  const sections = Object.entries(SCHEMA).map(([name, fields]) => h("section", { "data-nav": t("sec." + name) }, h("h3", {}, t("sec." + name)),
    h("div", { class: "grid2" }, fields.map(([key, kind]) => field(t("set." + key), control(kind, prefs[key], on(key)), kind.type === "bool")))));

  const notifySection = h("section", { "data-nav": t("sec.notify") }, h("h3", {}, t("sec.notify")), h("div", { class: "grid2" },
    EVENTS.map((ev) => field(t("ev." + ev), control(B, notify.events[ev], (v) => { notify.events[ev] = v; notifyChanged = true; dirty(); }), true)),
    NOTIFY_FIELDS.map((k) => field(t("set.notify." + k), control(T, notify[k], (v) => { notify[k] = v; notifyChanged = true; dirty(); })))),
  h("div", { class: "actions" }, h("button", { onclick: async () => {
    try { const r = await nova("notify/test", "POST", {}); toast(r.sent_via.join(", ") || t("opt.none")); } catch { toast(t("common.error"), true); }
  } }, t("common.test"))));

  const saveBtn = h("button", { class: "primary", disabled: ro, onclick: async () => {
    if (notifyChanged) changed.nova_notify = notify;
    if (changed.proxy_password === "") delete changed.proxy_password;
    try {
      await qb("app/setPreferences", { json: JSON.stringify(changed) });
      window.dispatchEvent(new CustomEvent("nova:prefs", { detail: { ...changed } }));
      for (const k of Object.keys(changed)) delete changed[k];
      notifyChanged = false; savebar.classList.remove("dirty"); msg.textContent = t("set.clean");
      toast(t("common.saved"));
    } catch { toast(t("common.error"), true); }
  } }, icon("check", 15), t("common.save"));
  const discard = h("button", { class: "ghost", onclick: () => renderSettings(el, { role }) }, t("set.discard"));
  savebar.append(msg, discard, saveBtn);

  const extra = h("div", {});
  const q = h("input", { type: "search", placeholder: t("set.search"), "aria-label": t("set.search"), oninput: () => applyFilter(q.value) });
  const nav = h("nav", { class: "snav", "aria-label": t("nav.settings") }, q);
  const content = h("div", { class: "center-col" }, savebar, sections.slice(0, 5), notifySection, sections.slice(5), extra);
  el.replaceChildren(h("h2", {}, t("set.title")), h("div", { class: "settings" }, nav, content));
  if (!ro) extra.append(await rssSection(), await searchSection(), await usersSection(), backupSection());

  const secs = () => [...el.querySelectorAll("section")];
  function link(sec) {
    const title = sec.dataset.nav || sec.querySelector("h3")?.textContent || "";
    return h("a", { role: "link", tabindex: 0, onclick: () => sec.scrollIntoView({ behavior: "smooth", block: "start" }),
      onkeydown: (e) => { if (e.key === "Enter") sec.scrollIntoView({ block: "start" }); } }, title);
  }
  const links = secs().map((sec) => ({ sec, a: link(sec) }));
  nav.append(...links.map((l) => l.a));
  function applyFilter(query) {
    const needle = query.trim().toLowerCase();
    for (const { sec, a } of links) {
      const title = (sec.dataset.nav || sec.querySelector("h3")?.textContent || "").toLowerCase();
      const fields = [...sec.querySelectorAll(".field")];
      let any = !needle || title.includes(needle);
      for (const f of fields) {
        const hit = !needle || title.includes(needle) || f.textContent.toLowerCase().includes(needle);
        f.hidden = !hit; if (hit) any = true;
      }
      sec.hidden = !any; a.hidden = !any;
    }
  }
  const io = new IntersectionObserver((entries) => {
    for (const en of entries) if (en.isIntersecting) { links.forEach((l) => l.a.classList.toggle("on", l.sec === en.target)); }
  }, { root: el, rootMargin: "0px 0px -70% 0px" });
  links.forEach((l) => io.observe(l.sec));
}

async function rssSection() {
  const box = h("section", {});
  async function draw() {
    const d = await nova("rss");
    const fn = h("input", { placeholder: t("rss.feed_name") }), fu = h("input", { placeholder: t("rss.feed_url"), style: "min-width:240px" });
    const fi = h("input", { type: "number", value: 30, style: "width:80px", title: t("rss.interval") });
    const r = { name: h("input", { placeholder: t("rss.rule_name") }), must: h("input", { placeholder: t("rss.must") }),
      not: h("input", { placeholder: t("rss.mustnot") }), cat: h("input", { placeholder: t("rss.category") }),
      path: h("input", { placeholder: t("rss.savepath") }), rx: h("input", { type: "checkbox" }), smart: h("input", { type: "checkbox", checked: true }),
      paused: h("input", { type: "checkbox" }) };
    box.replaceChildren(h("h3", {}, t("nav.rss")), h("h4", {}, t("rss.feeds")),
      h("table", {}, Object.entries(d.feeds).map(([id, f]) => h("tr", {}, h("td", {}, f.name), h("td", {}, f.url),
        h("td", { class: f.last_error ? "bad" : "" }, f.last_error ? `${t("rss.error")}: ${f.last_error}` : ""),
        h("td", {}, h("button", { onclick: async () => { const x = await nova(`rss/run/${id}`, "POST", {}); toast(`${t("rss.added")}: ${x.added}`); draw(); } }, t("rss.run")),
          h("button", { class: "danger", onclick: async () => { await nova(`rss/feeds/${id}`, "DELETE"); draw(); } }, t("common.delete")))))),
      h("div", { class: "actions", style: "justify-content:flex-start" }, fn, fu, fi, h("button", { onclick: async () => {
        try { await nova("rss/feeds", "POST", { name: fn.value, url: fu.value, interval_min: +fi.value }); draw(); } catch { toast(t("common.error"), true); }
      } }, t("common.add"))),
      h("h4", {}, t("rss.rules")),
      h("table", {}, Object.entries(d.rules).map(([id, x]) => h("tr", {}, h("td", {}, x.name), h("td", {}, x.must_contain), h("td", {}, x.must_not_contain), h("td", {}, x.category),
        h("td", {}, h("button", { class: "danger", onclick: async () => { await nova(`rss/rules/${id}`, "DELETE"); draw(); } }, t("common.delete")))))),
      h("div", { class: "grid2" }, r.name, r.must, r.not, r.cat, r.path,
        h("label", { class: "field row" }, r.rx, t("rss.regex")), h("label", { class: "field row" }, r.smart, t("rss.smart")),
        h("label", { class: "field row" }, r.paused, t("rss.paused"))),
      h("div", { class: "actions" }, h("button", { class: "primary", onclick: async () => {
        try {
          await nova("rss/rules", "POST", { name: r.name.value, must_contain: r.must.value, must_not_contain: r.not.value, use_regex: r.rx.checked,
            category: r.cat.value, save_path: r.path.value, smart_filter: r.smart.checked, paused: r.paused.checked });
          draw();
        } catch { toast(t("common.error"), true); }
      } }, t("common.add"))));
  }
  await draw();
  return box;
}

async function searchSection() {
  const box = h("section", {});
  async function draw() {
    const list = await nova("search/indexers");
    const n = h("input", { placeholder: t("srch.name") }), u = h("input", { placeholder: t("srch.url"), style: "min-width:260px" });
    const k = h("input", { type: "password", placeholder: t("srch.key"), autocomplete: "new-password" });
    box.replaceChildren(h("h3", {}, t("srch.indexers")), h("p", { class: "muted" }, t("srch.help")),
      h("table", {}, list.map((x) => h("tr", {}, h("td", {}, x.name), h("td", {}, x.url), h("td", {}, x.has_key ? t("srch.has_key") : ""),
        h("td", {}, h("button", { onclick: async () => {
          try { const r = await nova(`search/indexers/${x.id}/test`, "POST", {}); toast(r.ok ? t("srch.ok") : `${t("common.error")}: ${r.error}`, !r.ok); } catch { toast(t("common.error"), true); }
        } }, t("srch.test")),
        h("button", { class: "danger", onclick: async () => { try { await nova(`search/indexers/${x.id}`, "DELETE"); draw(); } catch { toast(t("common.error"), true); } } }, t("common.delete"))))) ),
      h("div", { class: "actions", style: "justify-content:flex-start" }, n, u, k, h("button", { onclick: async () => {
        try { await nova("search/indexers", "POST", { name: n.value, url: u.value, apikey: k.value }); draw(); } catch { toast(t("common.error"), true); }
      } }, t("common.add"))));
  }
  await draw();
  return box;
}

async function usersSection() {
  const box = h("section", {});
  async function draw() {
    const [users, keys] = await Promise.all([nova("users"), nova("apikeys")]);
    const u = h("input", { placeholder: t("users.user") }), p = h("input", { type: "password", placeholder: t("common.password"), autocomplete: "new-password" });
    const role = h("select", {}, h("option", { value: "readonly" }, t("users.readonly")), h("option", { value: "admin" }, t("users.admin")));
    const kn = h("input", { placeholder: t("common.name") });
    const kr = h("select", {}, h("option", { value: "readonly" }, t("users.readonly")), h("option", { value: "admin" }, t("users.admin")));
    box.replaceChildren(h("h3", {}, t("sec.users")),
      h("table", {}, users.map((x) => h("tr", {}, h("td", {}, x.user), h("td", {}, x.role),
        h("td", {}, h("button", { class: "danger", onclick: async () => { try { await nova(`users/${encodeURIComponent(x.user)}`, "DELETE"); draw(); } catch { toast(t("common.error"), true); } } }, t("common.delete")))))),
      h("div", { class: "actions", style: "justify-content:flex-start" }, u, p, role, h("button", { onclick: async () => {
        try { await nova("users", "POST", { user: u.value, password: p.value, role: role.value }); toast(t("common.saved")); draw(); } catch { toast(t("common.error"), true); }
      } }, t("users.add"))),
      h("h4", {}, t("keys.title")),
      h("table", {}, keys.map((k) => h("tr", {}, h("td", {}, k.name), h("td", {}, k.role), h("td", {}, k.id),
        h("td", {}, h("button", { class: "danger", onclick: async () => { await nova(`apikeys/${k.id}`, "DELETE"); draw(); } }, t("common.delete")))))),
      h("div", { class: "actions", style: "justify-content:flex-start" }, kn, kr, h("button", { onclick: async () => {
        try {
          const r = await nova("apikeys", "POST", { name: kn.value || "key", role: kr.value });
          dialog(t("keys.title"), h("div", {}, h("p", {}, t("keys.shown_once")), h("code", { style: "word-break:break-all" }, r.token)), [{ label: t("common.close") }]);
          draw();
        } catch { toast(t("common.error"), true); }
      } }, t("keys.create"))));
  }
  await draw();
  return box;
}

function backupSection() {
  const pw = h("input", { type: "password", placeholder: t("backup.password"), autocomplete: "new-password" });
  const file = h("input", { type: "file" });
  return h("section", {}, h("h3", {}, t("sec.backup")), h("p", { class: "muted" }, t("backup.manual")),
    h("div", { class: "actions", style: "justify-content:flex-start" }, pw,
      h("button", { onclick: async () => {
        try {
          const r = await fetch("/api/nova/backup/export", { method: "POST", credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRF-Token": window.__csrf || "" }, body: JSON.stringify({ password: pw.value }) });
          if (!r.ok) throw new Error();
          const url = URL.createObjectURL(await r.blob());
          h("a", { href: url, download: "novatorrent-backup.ntbak" }).click();
          URL.revokeObjectURL(url);
        } catch { toast(t("common.error"), true); }
      } }, t("backup.export"))),
    h("div", { class: "actions", style: "justify-content:flex-start" }, file, h("button", { class: "danger", onclick: async () => {
      if (!file.files[0]) return;
      const fd = new FormData(); fd.set("password", pw.value); fd.set("file", file.files[0]);
      try {
        const r = await fetch("/api/nova/backup/import", { method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": window.__csrf || "" }, body: fd });
        if (!r.ok) throw new Error();
        toast(t("backup.done"));
      } catch { toast(t("common.error"), true); }
    } }, t("backup.import"))));
}
