// Thin fetch wrappers for the qBittorrent-style and Nova APIs.
let csrf = "";
export const setCsrf = (v) => { csrf = v; window.__csrf = v; };

export async function qb(path, data, opts = {}) {
  const init = { credentials: "same-origin" };
  let url = "/api/v2/" + path;
  if (data instanceof FormData) {
    init.method = "POST"; init.body = data;
  } else if (data) {
    init.method = "POST";
    init.body = new URLSearchParams(data);
  }
  const r = await fetch(url, init);
  if (r.status === 403 && !opts.quiet) throw Object.assign(new Error("forbidden"), { status: 403 });
  if (!r.ok && !opts.quiet) throw Object.assign(new Error(await r.text()), { status: r.status });
  const text = await r.text();
  try { return JSON.parse(text); } catch { return text; }
}

export async function nova(path, method = "GET", body) {
  const init = { method, credentials: "same-origin", headers: {} };
  if (method !== "GET") init.headers["X-CSRF-Token"] = csrf;
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const r = await fetch("/api/nova/" + path, init);
  if (!r.ok) throw Object.assign(new Error(await r.text()), { status: r.status });
  return r.json();
}
