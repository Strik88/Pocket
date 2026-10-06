// Shared helpers: DOM, translations, formatting, API calls, feedback (toast, live region, dialogs).
import { STR } from "./i18n.js";

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// -- Language -----------------------------------------------------------------------------

export let LANG = (() => {
  try { return localStorage.getItem("pb-lang") || "nl"; } catch { return "nl"; }
})();

export function setLang(lang) {
  LANG = lang === "en" ? "en" : "nl";
  try { localStorage.setItem("pb-lang", LANG); } catch { /* private window */ }
  document.documentElement.lang = LANG;
}

/** t(key, {n: 3}) with {placeholders}; falls back to Dutch, then the key. */
export function t(key, vars = {}) {
  let s = STR[LANG]?.[key] ?? STR.nl[key] ?? key;
  for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, v);
  return s;
}

/** Plural: tp("conv", 1) uses conv_one, otherwise conv_other. Both get {n}. */
export function tp(key, n, vars = {}) {
  const rule = new Intl.PluralRules(LANG === "nl" ? "nl-NL" : "en-GB").select(n);
  return t(`${key}_${rule === "one" ? "one" : "other"}`, { n: fmtNum(n), ...vars });
}

export const fmtNum = (n) => new Intl.NumberFormat(LANG === "nl" ? "nl-NL" : "en-GB").format(n);
export const fmtEur = (n) =>
  new Intl.NumberFormat(LANG === "nl" ? "nl-NL" : "en-GB", { style: "currency", currency: "EUR", minimumFractionDigits: 2 }).format(n);

const locale = () => (LANG === "nl" ? "nl-NL" : "en-GB");

function toDate(iso) {
  if (!iso) return null;
  const d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
  return isNaN(d) ? null : d;
}

/** "wo 3 sep, 09:30" (with time) or "3 sep 2026" (day only). */
export function fmtDate(iso, { time = true, weekday = true, year = false } = {}) {
  const d = toDate(iso);
  if (!d) return "";
  const hasTime = time && iso.length > 10;
  const opts = { day: "numeric", month: "short" };
  if (weekday) opts.weekday = "short";
  if (year || d.getFullYear() !== new Date().getFullYear()) opts.year = "numeric";
  if (hasTime) Object.assign(opts, { hour: "2-digit", minute: "2-digit" });
  return new Intl.DateTimeFormat(locale(), opts).format(d).replace(" om ", ", ");
}

/** "vandaag om 11:07", "gisteren om 16:20", else a short date. */
export function fmtRelative(iso) {
  const d = toDate(iso);
  if (!d) return "";
  const today = new Date();
  const days = Math.round((new Date(today.toDateString()) - new Date(d.toDateString())) / 86400000);
  const hm = new Intl.DateTimeFormat(locale(), { hour: "2-digit", minute: "2-digit" }).format(d);
  if (days === 0) return t("today_at", { t: hm });
  if (days === 1) return t("yesterday_at", { t: hm });
  return fmtDate(iso, { weekday: false });
}

export const firstName = (name) => (name || "").trim().split(/\s+/)[0] || "";

// -- Icons ----------------------------------------------------------------------------------

export const icon = (name, cls = "") =>
  `<svg class="icon ${cls}" aria-hidden="true" focusable="false"><use href="/static/img/icons.svg#i-${name}"></use></svg>`;

// -- API --------------------------------------------------------------------------------------

export class ApiError extends Error {
  constructor(code, message, status) {
    super(message || code);
    this.code = code;
    this.status = status;
  }
}

export async function api(path, { method = "GET", body, signal } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      signal,
      headers: body !== undefined ? { "Content-Type": "application/json", "X-Pocket-Bridge": "1" } : { "X-Pocket-Bridge": "1" },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError("offline", e.message);
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (res.status === 401) { location.reload(); throw new ApiError("auth"); }
  if (!res.ok) {
    const d = data?.detail;
    if (d && typeof d === "object" && !Array.isArray(d)) throw new ApiError(d.code || "error", d.message, res.status);
    if (Array.isArray(d)) throw new ApiError("invalid_input", "", res.status);
    throw new ApiError(typeof d === "string" ? d : "error", "", res.status);
  }
  return data;
}

/** A readable, translated message for any error. */
export function errText(err) {
  const code = err?.code || "error";
  const key = `err_${code}`;
  const known = STR.nl[key] !== undefined;
  return known ? t(key) : t("err_error");
}

// -- Feedback -------------------------------------------------------------------------------

let toastTimer;
export function toast(msg, { type = "ok", action = null, timeout } = {}) {
  const el = $("#toast");
  clearTimeout(toastTimer);
  el.className = type === "error" ? "err" : "";
  el.setAttribute("role", type === "error" ? "alert" : "status");
  el.innerHTML = `<span>${esc(msg)}</span>`;
  if (action) {
    const b = document.createElement("button");
    b.textContent = action.label;
    b.onclick = () => { hideToast(); action.fn(); };
    el.append(b);
  }
  const x = document.createElement("button");
  x.className = "x";
  x.setAttribute("aria-label", t("close"));
  x.innerHTML = icon("x", "sm");
  x.onclick = hideToast;
  el.append(x);
  requestAnimationFrame(() => el.classList.add("show"));
  const ms = timeout ?? (type === "error" ? 0 : action ? 8000 : 4000);
  if (ms) toastTimer = setTimeout(hideToast, ms);
}
export function hideToast() { $("#toast").classList.remove("show"); }

export function announce(msg) {
  const el = $("#live");
  el.textContent = "";
  setTimeout(() => (el.textContent = msg), 50);
}

/** Run an async action from a button: busy state, error toast with retry. Returns the result or undefined. */
export async function run(btn, fn, { busy = t("busy"), retry = true } = {}) {
  if (btn?.getAttribute("aria-busy") === "true") return;
  const label = btn?.innerHTML;
  if (btn) {
    btn.setAttribute("aria-busy", "true");
    btn.setAttribute("aria-disabled", "true");
    btn.innerHTML = `${icon("loader-circle", "spin")} ${esc(busy)}`;
  }
  try {
    return await fn();
  } catch (e) {
    if (e.name === "AbortError") return;
    console.error(e);
    toast(errText(e), { type: "error", action: retry && btn ? { label: t("try_again"), fn: () => btn.click() } : null });
  } finally {
    if (btn && btn.isConnected) {
      btn.innerHTML = label;
      btn.removeAttribute("aria-busy");
      btn.removeAttribute("aria-disabled");
    }
  }
}

/** Branded confirm dialog. Resolves true/false. */
export function confirmDialog({ title, body = "", ok = t("ok"), cancel = t("cancel"), danger = false }) {
  return new Promise((resolve) => {
    const d = document.createElement("dialog");
    d.setAttribute("aria-labelledby", "dlg-t");
    d.innerHTML = `<div class="d-body"><h2 id="dlg-t">${esc(title)}</h2><p class="muted">${esc(body)}</p></div>
      <div class="d-foot"><button class="btn" value="no">${esc(cancel)}</button>
      <button class="btn ${danger ? "danger solid" : "primary"}" value="yes">${esc(ok)}</button></div>`;
    document.body.append(d);
    const done = (v) => { d.close(); d.remove(); resolve(v); };
    $$("button", d).forEach((b) => (b.onclick = () => done(b.value === "yes")));
    d.addEventListener("cancel", (e) => { e.preventDefault(); done(false); });
    d.showModal();
    $("button[value=no]", d).focus();
  });
}

/** Modal or drawer with arbitrary content. Returns {dialog, close}. */
export function openDialog(html, { drawer = false, wide = false, label = "" } = {}) {
  const d = document.createElement("dialog");
  if (drawer) d.classList.add("drawer");
  if (wide) d.classList.add("wide");
  if (label) d.setAttribute("aria-label", label);
  d.innerHTML = html;
  document.body.append(d);
  const close = () => { if (d.open) d.close(); d.remove(); };
  d.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
  d.addEventListener("click", (e) => { if (e.target === d) close(); });
  $$("[data-close]", d).forEach((b) => (b.onclick = close));
  d.showModal();
  return { dialog: d, close };
}

/** Small branded dialog asking for one name. Resolves the trimmed text, or "" when cancelled. */
export function promptName({ title, label }) {
  return new Promise((resolve) => {
    const { dialog, close } = openDialog(`<form class="d-body" id="pnf"><h2>${esc(title)}</h2>
      <div class="field"><label for="pnIn">${esc(label)}</label><input id="pnIn" required autocomplete="off"></div>
      <div class="row end"><button type="button" class="btn" data-close>${esc(t("cancel"))}</button><button class="btn primary">${esc(t("save"))}</button></div></form>`, { label: title });
    let value = "";
    $("#pnIn", dialog).focus();
    $("#pnf", dialog).onsubmit = (e) => { e.preventDefault(); value = $("#pnIn", dialog).value.trim(); close(); };
    dialog.addEventListener("close", () => resolve(value));
  });
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.append(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  toast(t("copied"));
}

// -- Markdown (our own files and Claude's answers; everything is escaped first) ------------------

export function md(src, { headingStart = 3 } = {}) {
  const lines = esc(src).split("\n");
  let html = "", list = null, inFm = false, table = [];
  const inline = (s) =>
    s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/(^|[\s(])_(.+?)_(?=[\s).,;:!?]|$)/g, "$1<i>$2</i>")
      .replace(/`(.+?)`/g, "<code>$1</code>")
      .replace(/\[\[(.+?)\]\]/g, "$1");
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  const flushTable = () => {
    if (!table.length) return;
    const rows = table.filter((r) => !/^\|?\s*:?-{2,}/.test(r));
    html += "<table>" + rows.map((r, i) => {
      const cells = r.replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
      const tag = i === 0 ? "th" : "td";
      return `<tr>${cells.map((c) => `<${tag}>${c}</${tag}>`).join("")}</tr>`;
    }).join("") + "</table>";
    table = [];
  };
  lines.forEach((line, i) => {
    if (i === 0 && line === "---") { inFm = true; return; }
    if (inFm) { if (line === "---") inFm = false; return; }
    if (/^\s*\|.*\|\s*$/.test(line)) { closeList(); table.push(line.trim()); return; }
    flushTable();
    const ul = line.match(/^\s*[-*] (\[[ xX]\] )?(.*)$/);
    const ol = line.match(/^\s*\d+[.)] (.*)$/);
    if (ul) {
      if (list !== "ul") { closeList(); html += "<ul>"; list = "ul"; }
      const box = ul[1] ? (/x/i.test(ul[1]) ? "☑ " : "☐ ") : "";
      html += `<li>${box}${inline(ul[2])}</li>`;
      return;
    }
    if (ol) {
      if (list !== "ol") { closeList(); html += "<ol>"; list = "ol"; }
      html += `<li>${inline(ol[1])}</li>`;
      return;
    }
    closeList();
    const h = line.match(/^(#{1,6}) (.*)$/);
    if (h) {
      const n = Math.min(6, h[1].length + headingStart - 1);
      html += `<h${n}>${inline(h[2])}</h${n}>`;
    } else if (/^&gt; ?/.test(line)) html += `<blockquote>${inline(line.replace(/^&gt; ?/, ""))}</blockquote>`;
    else if (line.trim()) html += `<p>${inline(line)}</p>`;
  });
  flushTable();
  closeList();
  return html;
}

export function avatarClass(name) {
  let h = 0;
  for (const c of name || "") h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return `c${h % 4}`;
}
export const initials = (name) =>
  (name || "?").split(/\s+/).filter((w) => /^[A-Za-zÀ-ÿ0-9]/.test(w)).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";

/** Turn a client_source such as "rule: agenda / calendar: acme.nl" into a readable sentence. */
export function sortReason(src) {
  if (!src) return "";
  const s = String(src).trim();
  if (s === "manual") return t("reason_manual");
  if (s === "unsorted" || s === "kept") return s === "kept" ? "" : t("reason_unsorted");
  if (s.startsWith("claude:")) return t("reason_claude", { why: s.slice(7).trim() });
  if (s.startsWith("rule:")) {
    const r = s.slice(5).trim();
    const m = r.match(/^(Pocket-tag|agenda \/ calendar|titel \/ title|trefwoord \/ keyword):\s*(.*)$/);
    if (m) {
      const kind = { "Pocket-tag": "tag", "agenda / calendar": "calendar", "titel / title": "title", "trefwoord / keyword": "keyword" }[m[1]];
      return t(`reason_rule_${kind}`, { x: m[2] });
    }
    return t("reason_rule", { why: r });
  }
  if (s.startsWith("resort")) return t("reason_rule", { why: "" }).replace(/[:\s]+$/, "");
  return t("reason_other", { why: s });
}
