// UI helpers: safe DOM builder (text nodes only), i18n, formatting, dialogs.
let dict = { en: {}, nl: {} };
let lang = "en";
try { lang = localStorage.getItem("lang") || (navigator.language || "en").slice(0, 2); } catch { /* storage blocked */ }
if (!["en", "nl"].includes(lang)) lang = "en";

export async function loadI18n() {
  dict = await (await fetch("i18n.json")).json();
}
export const getLang = () => lang;
export function setLang(l) {
  lang = l;
  try { localStorage.setItem("lang", l); } catch { /* ignore */ }
}
export function t(key) {
  return (dict[lang] && dict[lang][key]) || (dict.en && dict.en[key]) || key;
}

export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

export function bytes(n) {
  if (n == null || n < 0) return "";
  const u = ["B", "KiB", "MiB", "GiB", "TiB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i ? n.toFixed(n < 10 ? 2 : 1) : n.toFixed(0)) + " " + u[i];
}
export const speed = (n) => (n > 0 ? bytes(n) + "/s" : "");
export function eta(s) {
  if (s == null || s >= 8640000 || s < 0) return "∞";
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m " + (s % 60) + "s";
  if (s < 86400) return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m";
  return Math.floor(s / 86400) + "d " + Math.floor((s % 86400) / 3600) + "h";
}
export const date = (ts) => (ts > 0 ? new Date(ts * 1000).toLocaleString(lang === "nl" ? "nl-BE" : "en-GB") : "");

let toastTimer;
export function toast(msg, bad = false) {
  document.querySelectorAll(".toast").forEach((x) => x.remove());
  const el = h("div", { class: "toast" + (bad ? " bad" : "") }, msg);
  document.body.append(el);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), 3500);
}

export function dialog(title, body, actions) {
  const close = () => bg.remove();
  const bg = h("div", { class: "dialog-bg", onmousedown: (e) => { if (e.target === bg) close(); } },
    h("div", { class: "dialog", role: "dialog", "aria-modal": "true" }, h("h3", {}, title), body,
      h("div", { class: "actions" }, (actions || []).map((a) =>
        h("button", { class: a.cls || "", onclick: async () => { if ((await a.run?.()) !== false) close(); } }, a.label)))));
  document.body.append(bg);
  const first = bg.querySelector("input, textarea, select, button");
  if (first) first.focus();
  return close;
}
export const closeDialogs = () => document.querySelectorAll(".dialog-bg").forEach((x) => x.remove());

export function drawGraph(canvas, series, colors) {
  const dpr = window.devicePixelRatio || 1;
  const w = (canvas.width = canvas.clientWidth * dpr), hgt = (canvas.height = canvas.clientHeight * dpr);
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, w, hgt);
  const css = getComputedStyle(document.documentElement);
  const max = Math.max(1024, ...series.flat());
  ctx.strokeStyle = css.getPropertyValue("--line"); ctx.lineWidth = 1;
  for (let i = 1; i < 4; i++) { ctx.beginPath(); ctx.moveTo(0, (hgt * i) / 4); ctx.lineTo(w, (hgt * i) / 4); ctx.stroke(); }
  series.forEach((s, k) => {
    ctx.strokeStyle = css.getPropertyValue(colors[k]); ctx.lineWidth = 2 * dpr; ctx.beginPath();
    s.forEach((v, i) => {
      const x = (i / Math.max(s.length - 1, 1)) * w, y = hgt - (v / max) * (hgt - 6 * dpr) - 3 * dpr;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
  });
  ctx.fillStyle = css.getPropertyValue("--muted"); ctx.font = `${11 * dpr}px sans-serif`;
  ctx.fillText(speed(max) || "0", 4 * dpr, 12 * dpr);
}
