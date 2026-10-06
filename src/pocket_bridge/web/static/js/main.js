// Entry point: picks onboarding (focus mode) or the app shell, and routes between views.
import { $, $$, api, esc, icon, setLang, t, LANG, toast, errText, announce } from "./core.js";
import { S, refresh, watch } from "./state.js";
import * as overview from "./views/overview.js";
import * as ask from "./views/ask.js";
import * as clients from "./views/clients.js";
import * as conversations from "./views/conversations.js";
import * as actions from "./views/actions.js";
import * as settings from "./views/settings.js";
import * as onboarding from "./views/onboarding.js";
import * as proposal from "./views/proposal.js";

const VIEWS = { overview, ask, clients, conversations, actions, settings, proposal };
const NAV = [
  ["overview", "layout-dashboard"],
  ["ask", "sparkles"],
  ["clients", "building-2"],
  ["conversations", "messages-square"],
  ["actions", "list-checks"],
];

function applyTheme() {
  let theme = "system";
  try { theme = localStorage.getItem("pb-theme") || "system"; } catch { /* ignore */ }
  if (theme === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.dataset.theme = theme;
}

export function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  return { view: parts[0] || "", param: parts.slice(1).join("/") };
}

export function go(hash) {
  if (location.hash === hash) render();
  else location.hash = hash;
}

function shellHtml() {
  const st = S.state;
  const counts = { actions: 0, clients: (st.unsorted || 0) + (st.suggestions?.length || 0) + (st.proposal ? 1 : 0) };
  const navItem = ([v, ic]) => {
    const badge = v === "clients" && counts.clients ? `<span class="count" aria-label="${esc(t("needs_attention"))}">${counts.clients}</span>` : "";
    return `<li><a href="#/${v}" data-view="${v}">${icon(ic)}<span>${esc(t("nav_" + v))}</span>${badge}</a></li>`;
  };
  const brokenConn = !st.pocket_ready;
  return `
    <div class="topbar">
      <img src="/static/img/striks-icon-color.png" alt="">
      <b>Pocket Bridge</b>
      <button id="menuBtn" aria-expanded="false" aria-controls="sidebar" aria-label="${esc(t("menu"))}">${icon("menu")}</button>
    </div>
    <div class="shell">
      <aside class="sidebar" id="sidebar">
        <a class="brand" href="#/overview">
          <img src="/static/img/striks-icon-color.png" alt="">
          <span><b>Pocket Bridge</b><span>by Striks</span></span>
        </a>
        <nav aria-label="${esc(t("main_menu"))}">
          <ul class="nav">${NAV.map(navItem).join("")}</ul>
          <div class="nav-sep" role="separator"></div>
          <ul class="nav"><li><a href="#/settings" data-view="settings">${icon("settings")}<span>${esc(t("nav_settings"))}</span>${brokenConn ? `<span class="dot-alert" aria-label="${esc(t("needs_attention"))}"></span>` : ""}</a></li></ul>
        </nav>
        <div class="side-foot">
          <div class="status-dots">
            <span class="sd"><i class="${st.pocket_ready ? "on" : ""}"></i>Pocket</span>
            <span class="sd"><i class="${st.ai_ready ? "on" : ""}"></i>Claude</span>
            <span class="sd"><i class="${st.claude_desktop_connected ? "on" : ""}"></i>Desktop</span>
          </div>
          <div class="lang-switch" role="group" aria-label="${esc(t("language"))}">
            <button data-lang="nl" lang="nl" aria-pressed="${LANG === "nl"}">NL</button><button data-lang="en" lang="en" aria-pressed="${LANG === "en"}">EN</button>
          </div>
          <span>v${esc(st.version)}</span>
        </div>
      </aside>
      <main id="main" tabindex="-1"><div class="main-inner" id="view"></div></main>
    </div>`;
}

function banners() {
  const st = S.state;
  let html = "";
  if (st.settings.demo_mode) {
    html += `<div class="banner" role="note">${icon("play")}<span>${esc(t("demo_banner"))}</span><span class="spacer"></span>
      <button class="btn small" id="demoExit">${esc(t("demo_exit"))}</button></div>`;
  } else if (!st.settings.onboarding.completed) {
    html += `<div class="banner" role="note">${icon("info")}<span>${esc(t("setup_unfinished"))}</span><span class="spacer"></span>
      <a class="btn small primary" href="#/setup/${esc(st.settings.onboarding.step === "done" ? "welcome" : st.settings.onboarding.step)}">${esc(t("setup_continue"))}</a></div>`;
  }
  return html;
}

export async function leaveDemo(btn) {
  try {
    if (btn) btn.setAttribute("aria-disabled", "true");
    await api("/api/demo/stop", { method: "POST" });
    await refresh();
    toast(t("demo_left"));
    go(S.state.settings.onboarding.completed ? "#/overview" : `#/setup/${S.state.settings.onboarding.step || "welcome"}`);
  } catch (e) {
    toast(errText(e), { type: "error" });
  }
}

let shellMounted = false;
let lastView = "";

async function render() {
  const { view, param } = parseRoute();
  const st = S.state;
  const ob = st.settings.onboarding;
  // First visit, or onboarding still running: show the setup flow
  if (!view) {
    if (!ob.completed) return go(`#/setup/${ob.step && ob.step !== "done" ? ob.step : "welcome"}`);
    return go("#/overview");
  }
  if (view === "setup" || view === "welcome") {
    shellMounted = false;
    document.body.classList.add("focus-mode");
    await onboarding.render($("#app"), param || "welcome", { go, refresh });
    return;
  }
  document.body.classList.remove("focus-mode");
  const mod = VIEWS[view];
  if (!mod) return go("#/overview");
  if (!shellMounted) {
    $("#app").innerHTML = shellHtml();
    bindShell();
    shellMounted = true;
  } else {
    updateShell();
  }
  const navView = view === "proposal" ? "clients" : view;
  $$(".nav a").forEach((a) => (a.dataset.view === navView ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  closeMenu();
  const container = $("#view");
  container.innerHTML = banners() + `<div id="viewBody"></div>`;
  $("#demoExit")?.addEventListener("click", (e) => leaveDemo(e.currentTarget));
  const titleKey = "nav_" + navView;
  document.title = `${t(titleKey)} · Pocket Bridge by Striks`;
  try {
    await mod.render($("#viewBody"), param, { go, refresh });
  } catch (e) {
    console.error(e);
    $("#viewBody").innerHTML = `<div class="msg err">${icon("circle-alert")}<p>${esc(errText(e))}</p></div>`;
  }
  if (lastView !== view + "/" + param) {
    const h1 = $("#viewBody h1, #viewBody h2");
    if (h1 && lastView) { h1.setAttribute("tabindex", "-1"); h1.focus({ preventScroll: true }); }
    window.scrollTo(0, 0);
    if (lastView) announce(document.title);
  }
  lastView = view + "/" + param;
}

function updateShell() {
  const st = S.state;
  const dots = $$(".side-foot .sd i");
  [st.pocket_ready, st.ai_ready, st.claude_desktop_connected].forEach((on, i) => dots[i]?.classList.toggle("on", !!on));
  const n = (st.unsorted || 0) + (st.suggestions?.length || 0) + (st.proposal ? 1 : 0);
  const a = $('.nav a[data-view="clients"]');
  if (a) {
    let c = $(".count", a);
    if (n && !c) { c = document.createElement("span"); c.className = "count"; c.setAttribute("aria-label", t("needs_attention")); a.append(c); }
    if (c) { if (n) c.textContent = n; else c.remove(); }
  }
}

function closeMenu() {
  $("#sidebar")?.classList.remove("open");
  $("#menuBtn")?.setAttribute("aria-expanded", "false");
  $(".scrim")?.remove();
}

function bindShell() {
  $("#menuBtn").onclick = () => {
    const sb = $("#sidebar");
    if (sb.classList.contains("open")) return closeMenu();
    sb.classList.add("open");
    $("#menuBtn").setAttribute("aria-expanded", "true");
    const scrim = document.createElement("div");
    scrim.className = "scrim";
    scrim.onclick = closeMenu;
    document.body.append(scrim);
    $(".nav a", sb)?.focus();
  };
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
  $$(".lang-switch button").forEach((b) => (b.onclick = () => switchLang(b.dataset.lang)));
}

export async function switchLang(lang) {
  setLang(lang);
  try { await api("/api/settings", { method: "POST", body: { language: lang } }); } catch { /* language still switches */ }
  await refresh();
  shellMounted = false;
  lastView = "";
  render();
}

window.addEventListener("hashchange", render);
window.addEventListener("pb:state", () => { if (shellMounted) updateShell(); });

async function boot() {
  applyTheme();
  try {
    await refresh();
  } catch (e) {
    document.body.innerHTML = `<main style="padding:40px"><h1>Pocket Bridge</h1><p>${esc(errText(e))}</p></main>`;
    return;
  }
  const serverLang = S.state.settings.language;
  if (serverLang && serverLang !== LANG) setLang(serverLang);
  else setLang(LANG);
  if (S.state.sync_running || S.state.discover?.running) watch();
  render();
}

window.pb = { go, refresh, render, applyTheme, switchLang, leaveDemo };
boot();
