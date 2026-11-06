// Search view: query the configured Torznab indexers and add a result. Keys/URLs never reach the browser.
import { nova } from "./api.js";
import { bytes, date, h, t, toast } from "./ui.js";

export async function renderSearch(el, { categories }) {
  let indexers = [];
  try { indexers = await nova("search/indexers"); } catch { /* forbidden or offline */ }
  if (!indexers.length) {
    el.replaceChildren(h("h2", {}, t("srch.title")), h("p", { class: "muted" }, t("srch.no_indexers")));
    return;
  }
  const q = h("input", { type: "search", placeholder: t("srch.placeholder"), style: "min-width:260px", autocomplete: "off" });
  const cat = h("select", { title: t("srch.category") }, h("option", { value: "" }, t("opt.none")),
    Object.keys(categories || {}).map((c) => h("option", { value: c }, c)));
  const picks = indexers.map((x) => ({ x, box: h("input", { type: "checkbox", checked: true }) }));
  const out = h("div", {});
  let busy = false;

  async function run(ev) {
    ev.preventDefault();
    if (busy || !q.value.trim()) return;
    busy = true;
    out.replaceChildren(h("p", { class: "muted" }, t("srch.searching")));
    try {
      const ids = picks.filter((p) => p.box.checked).map((p) => p.x.id).join(",");
      const r = await nova(`search?q=${encodeURIComponent(q.value.trim())}&indexers=${encodeURIComponent(ids)}`);
      draw(r);
    } catch { out.replaceChildren(h("p", { class: "bad" }, t("common.error"))); }
    busy = false;
  }

  function draw(r) {
    const errs = Object.entries(r.errors || {});
    out.replaceChildren(
      ...(errs.length ? [h("div", { class: "bad" }, t("srch.errors") + ": " + errs.map(([n, m]) => `${n} (${m})`).join(", "))] : []),
      r.results.length ? h("table", {}, h("thead", {}, h("tr", {}, ["common.name", "srch.indexer", "srch.size", "srch.seeders", "srch.leechers", "srch.age", ""]
        .map((k) => h("th", {}, k ? t(k) : "")))),
      h("tbody", {}, r.results.map((x) => {
        const b = h("button", { class: "primary", onclick: async () => {
          b.disabled = true;
          try {
            await nova("search/add", "POST", { rid: x.rid, category: cat.value });
            b.textContent = t("srch.added");
          } catch (e) { b.disabled = false; toast(e.status === 404 ? t("srch.expired") : t("common.error"), true); }
        } }, t("srch.add"));
        return h("tr", {}, h("td", { title: x.title, style: "max-width:520px;overflow:hidden;text-overflow:ellipsis" }, x.title), h("td", {}, x.indexer),
          h("td", { class: "num" }, bytes(x.size)), h("td", { class: "num" }, x.seeders), h("td", { class: "num" }, x.leechers),
          h("td", {}, date(x.published)), h("td", {}, b));
      }))) : h("p", { class: "muted" }, t("srch.none")));
  }

  el.replaceChildren(h("h2", {}, t("srch.title")),
    h("form", { class: "actions", style: "justify-content:flex-start;flex-wrap:wrap", onsubmit: run }, q, cat,
      picks.map((p) => h("label", { class: "field row" }, p.box, p.x.name)), h("button", { class: "primary" }, t("srch.go"))),
    out);
  q.focus();
}
