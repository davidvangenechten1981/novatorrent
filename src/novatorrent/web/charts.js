// Small SVG chart helpers (no dependencies). Series colours come from CSS variables so light/dark swap in one place.
// All text is inserted with textContent. Marks: 2px lines, ~10% area wash, hairline grid, crosshair + one tooltip.
import { h } from "./ui.js";

const NS = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, v);
  for (const k of kids) if (k != null) el.append(k);
  return el;
}

// Round a max up to a "nice" tick step so the axis reads 0 / 1 / 2 / 3 style numbers.
export function niceMax(max, ticks = 4) {
  if (!(max > 0)) return { max: 1, step: 0.25 };
  const raw = max / ticks, pow = 10 ** Math.floor(Math.log10(raw)), f = raw / pow;
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * pow;
  return { max: step * Math.ceil(max / step), step };
}

/** Tiny line + area sparkline (no axes). values: numbers; colorVar: e.g. "--s1". */
export function spark(values, colorVar, w = 96, hgt = 28) {
  const svg = s("svg", { viewBox: `0 0 ${w} ${hgt}`, width: w, height: hgt, class: "spark", "aria-hidden": "true", preserveAspectRatio: "none" });
  const vals = values.length > 1 ? values : [0, 0];
  const { max } = niceMax(Math.max(...vals, 1));
  const x = (i) => (i / (vals.length - 1)) * (w - 2) + 1, y = (v) => hgt - 2 - (v / max) * (hgt - 4);
  const pts = vals.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  svg.append(s("path", { d: `M${pts.join(" L")} L${x(vals.length - 1)},${hgt} L1,${hgt} Z`, fill: `var(${colorVar})`, opacity: ".12" }),
    s("path", { d: `M${pts.join(" L")}`, fill: "none", stroke: `var(${colorVar})`, "stroke-width": 1.75, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  return svg;
}

/**
 * Time-series line chart with legend, hover crosshair + tooltip and a table view.
 * opts: { series: [{ name, colorVar, values }], secondsPerPoint, fmt: (v)=>string, height }
 */
export function lineChart(opts) {
  const { series, secondsPerPoint = 1.5, fmt = String, height = 240, labels, minMax = 1 } = opts;
  const n = Math.max(...series.map((x) => x.values.length), 0);
  const W = 800, H = height, m = { l: 56, r: 12, t: 12, b: 26 };
  const wrap = h("div", { class: "chart" });
  if (n < 2) { wrap.append(h("p", { class: "muted" }, labels.waiting)); return wrap; }
  const maxRaw = Math.max(...series.flatMap((x) => x.values), minMax);
  const { max, step } = niceMax(maxRaw);
  const x = (i) => m.l + (i / (n - 1)) * (W - m.l - m.r), y = (v) => H - m.b - (v / max) * (H - m.t - m.b);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img", "aria-label": labels.aria, preserveAspectRatio: "none" });
  const grid = s("g", { class: "chart-grid" });
  for (let v = 0; v <= max + 1e-9; v += step) {
    grid.append(s("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }));
    const t = s("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }); t.textContent = fmt(v); grid.append(t);
  }
  const span = (n - 1) * secondsPerPoint;
  for (const frac of [0, 0.25, 0.5, 0.75, 1]) {
    const secs = Math.round(span * (1 - frac)), t = s("text", { x: m.l + frac * (W - m.l - m.r), y: H - 6, "text-anchor": frac === 0 ? "start" : frac === 1 ? "end" : "middle" });
    t.textContent = frac === 1 ? labels.now : secs >= 60 ? `-${Math.round(secs / 60)}m` : `-${secs}s`; grid.append(t);
  }
  svg.append(grid);
  for (const sr of series) {
    const pts = sr.values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
    svg.append(s("path", { d: `M${pts.join(" L")} L${x(sr.values.length - 1)},${y(0)} L${x(0)},${y(0)} Z`, fill: `var(${sr.colorVar})`, opacity: ".1" }),
      s("path", { d: `M${pts.join(" L")}`, fill: "none", stroke: `var(${sr.colorVar})`, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round", "vector-effect": "non-scaling-stroke" }));
  }
  const cross = s("line", { class: "chart-cross", y1: m.t, y2: H - m.b, x1: 0, x2: 0, visibility: "hidden" });
  const dots = series.map((sr) => s("circle", { r: 4, fill: `var(${sr.colorVar})`, class: "chart-dot", visibility: "hidden" }));
  svg.append(cross, ...dots);
  const hit = s("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent", tabindex: 0 });
  svg.append(hit);
  const tip = h("div", { class: "chart-tip", hidden: true });
  const frame = h("div", { class: "chart-frame" }, svg, tip);

  function show(i, clientX) {
    i = Math.min(Math.max(i, 0), n - 1);
    const ago = Math.round((n - 1 - i) * secondsPerPoint);
    tip.replaceChildren(h("div", { class: "tip-time" }, ago === 0 ? labels.now : `-${ago >= 60 ? Math.floor(ago / 60) + "m " + (ago % 60) + "s" : ago + "s"}`),
      ...series.map((sr) => h("div", { class: "tip-row" }, h("i", { class: "key", style: `background:var(${sr.colorVar})` }),
        h("b", {}, fmt(sr.values[i] ?? 0)), h("span", {}, sr.name))));
    tip.hidden = false;
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    dots.forEach((d, k) => { d.setAttribute("cx", x(i)); d.setAttribute("cy", y(series[k].values[i] ?? 0)); d.setAttribute("visibility", "visible"); });
    const r = frame.getBoundingClientRect(), px = ((x(i) / W) * r.width);
    tip.style.left = Math.min(Math.max(px + 12, 4), r.width - tip.offsetWidth - 4) + "px";
    tip.style.top = "8px";
    void clientX;
  }
  function hide() { tip.hidden = true; cross.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("visibility", "hidden")); }
  let cur = n - 1;
  hit.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect(), vx = ((e.clientX - r.left) / r.width) * W;
    cur = Math.round(((vx - m.l) / (W - m.l - m.r)) * (n - 1)); show(cur, e.clientX);
  });
  hit.addEventListener("pointerleave", hide);
  hit.addEventListener("focus", () => show(cur));
  hit.addEventListener("blur", hide);
  hit.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") { cur = Math.max(cur - 1, 0); show(cur); e.preventDefault(); }
    if (e.key === "ArrowRight") { cur = Math.min(cur + 1, n - 1); show(cur); e.preventDefault(); }
  });

  const legend = h("div", { class: "legend" }, series.map((sr) => h("span", {}, h("i", { class: "key", style: `background:var(${sr.colorVar})` }), sr.name)));
  // Table view: same data, no hover needed.
  const rows = [];
  for (let i = n - 1; i >= 0 && rows.length < 60; i -= Math.max(1, Math.round(6 / secondsPerPoint))) rows.push(i);
  const table = h("table", { class: "chart-table", hidden: true },
    h("thead", {}, h("tr", {}, h("th", {}, labels.time), series.map((sr) => h("th", { class: "num" }, sr.name)))),
    h("tbody", {}, rows.map((i) => { const ago = Math.round((n - 1 - i) * secondsPerPoint);
      return h("tr", {}, h("td", {}, ago === 0 ? labels.now : `-${ago}s`), series.map((sr) => h("td", { class: "num" }, fmt(sr.values[i] ?? 0)))); })));
  const toggle = h("button", { class: "ghost sm", onclick: () => { table.hidden = !table.hidden; frame.hidden = !table.hidden; toggle.textContent = table.hidden ? labels.table : labels.chart; } }, labels.table);
  wrap.append(h("div", { class: "chart-head" }, legend, toggle), frame, table);
  return wrap;
}

// Label colour inside a coloured fill: near-black or white, whichever has more contrast (WCAG luminance).
function inkFor(color) {
  let c = color;
  const m = /^var\((--[\w-]+)\)$/.exec(color);
  if (m) c = getComputedStyle(document.documentElement).getPropertyValue(m[1]).trim();
  const hex = /^#([0-9a-f]{6})$/i.exec(c);
  if (!hex) return "#ffffff";
  const lin = [0, 2, 4].map((i) => { const v = parseInt(hex[1].slice(i, i + 2), 16) / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
  const L = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
  return (L + 0.05) / 0.05 > 1.05 / (L + 0.05) ? "#0b0b0b" : "#ffffff";
}

/** One whole split into parts. parts: [{ name, value, color }]; thin figure + legend that names the parts. */
export function segBar(parts, { fmt = String, tall = true } = {}) {
  const total = parts.reduce((a, p) => a + p.value, 0);
  const shown = parts.filter((p) => p.value > 0);
  const bar = h("div", { class: "segbar" + (tall ? " tall" : ""), role: "img", "aria-label": shown.map((p) => `${p.name}: ${fmt(p.value)}`).join(", ") });
  for (const p of shown) {
    const pct = (p.value / total) * 100;
    bar.append(h("div", { class: "seg", style: `flex:${p.value} 1 0;background:${p.color}`, title: `${p.name}: ${fmt(p.value)} (${pct.toFixed(0)}%)` },
      tall && pct >= 12 ? h("span", { style: `color:${inkFor(p.color)}` }, `${pct.toFixed(0)}%`) : null));
  }
  if (!shown.length) bar.append(h("div", { class: "seg empty" }));
  const legend = h("div", { class: "legend" }, parts.map((p) => h("span", {}, h("i", { class: "key sq", style: `background:${p.color}` }), p.name, h("b", {}, fmt(p.value)))));
  return h("div", {}, bar, legend);
}
