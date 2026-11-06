// Inline SVG icon set (24x24, stroke = currentColor). Built node by node with createElementNS (never from an HTML string), CSP-safe.
const NS = "http://www.w3.org/2000/svg";
// A string is a path "d"; [cx, cy, r] is a circle.
const ICONS = {
  plus: ["M12 5v14M5 12h14"],
  play: ["M7 4.5v15l12-7.5z"],
  pause: ["M8 5v14M16 5v14"],
  trash: ["M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"],
  search: [[11, 11, 7], "M20 20l-4-4"],
  sliders: ["M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1", [15, 6, 2], [9, 12, 2], [17, 18, 2]],
  list: ["M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"],
  activity: ["M3 12h4l3-8 4 16 3-8h4"],
  file: ["M7 3h8l4 4v14H7zM15 3v4h4M10 12h6M10 16h6"],
  logout: ["M9 4H5v16h4M16 8l4 4-4 4M20 12H9"],
  sun: [[12, 12, 4], "M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"],
  moon: ["M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"],
  globe: [[12, 12, 9], "M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"],
  down: ["M12 4v12M7 11l5 5 5-5M5 20h14"],
  up: ["M12 20V8M7 13l5-5 5 5M5 4h14"],
  check: ["M5 12l5 5 10-10"],
  x: ["M6 6l12 12M18 6L6 18"],
  more: ["M5 12h.01M12 12h.01M19 12h.01"],
  shield: ["M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"],
  disk: ["M3 14h18v6H3zM3 14l3-9h12l3 9M7 17h.01"],
  users: [[9, 8, 3], "M3 20c0-3 3-5 6-5s6 2 6 5M16 5a3 3 0 0 1 0 6M17 15c2.5.3 4 2 4 5"],
  tag: ["M3 12V4h8l10 10-8 8z", "M7.5 8h.01"],
  folder: ["M3 6h6l2 2h10v11H3z"],
  link: ["M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"],
  copy: ["M9 9h11v11H9zM5 15V4h11"],
  refresh: ["M20 11a8 8 0 1 0 2 5M20 4v7h-7"],
  cloud: ["M7 18a4 4 0 0 1 0-8 5 5 0 0 1 9.6-1A4.5 4.5 0 0 1 17 18M12 12v7M9 15l3-3 3 3"],
  menu: ["M4 6h16M4 12h16M4 18h16"],
  chevronDown: ["M6 9l6 6 6-6"],
  chevronUp: ["M6 15l6-6 6 6"],
  chevronRight: ["M9 6l6 6-6 6"],
  help: [[12, 12, 9], "M9.5 9.2a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 1-1 1.7M12 17h.01"],
  layers: ["M12 3l9 5-9 5-9-5zM3 13l9 5 9-5"],
  checkCircle: [[12, 12, 9], "M8 12l3 3 5-6"],
  pauseCircle: [[12, 12, 9], "M10 9v6M14 9v6"],
  zap: ["M13 2L4 14h7l-1 8 9-12h-7z"],
  clock: [[12, 12, 9], "M12 7v5l3 2"],
  alert: ["M12 3l10 18H2z", "M12 10v5M12 18h.01"],
  xCircle: [[12, 12, 9], "M9 9l6 6M15 9l-6 6"],
  filter: ["M3 5h18l-7 8v6l-4 2v-8z"],
  rss: ["M5 5a14 14 0 0 1 14 14M5 11a8 8 0 0 1 8 8M6 19h.01"],
  inbox: ["M3 13l3-8h12l3 8v6H3zM3 13h5l1 3h6l1-3h5"],
};

export function icon(name, size = 16, cls = "") {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.75");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("class", "icon" + (cls ? " " + cls : ""));
  for (const part of ICONS[name] || []) {
    if (typeof part === "string") {
      const p = document.createElementNS(NS, "path");
      p.setAttribute("d", part);
      svg.append(p);
    } else {
      const c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", part[0]); c.setAttribute("cy", part[1]); c.setAttribute("r", part[2]);
      svg.append(c);
    }
  }
  return svg;
}
export const iconNames = () => Object.keys(ICONS);
