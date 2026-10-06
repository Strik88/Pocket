// Instellingen: tabs, every control saves on change.
import { $, $$, api, esc, icon, t, tp, toast, errText, run, LANG, fmtRelative, sortReason, openDialog, announce } from "../core.js";
import { S, refresh, watch } from "../state.js";
import { renderDesktopBox } from "./onboarding.js";

const TABS = ["connections", "automatic", "sorting", "search", "appearance", "about"];

export async function render(root, param, ctx) {
  const tab = TABS.includes(param) ? param : "connections";
  root.innerHTML = `
    <div class="page-head"><div><h1>${esc(t("nav_settings"))}</h1></div></div>
    <nav class="tabs" aria-label="${esc(t("nav_settings"))}">${TABS.map((tb) => `<a href="#/settings/${tb}" ${tb === tab ? 'aria-current="page"' : ""}>${esc(t("tab_" + tb))}</a>`).join("")}</nav>
    <div id="tabBody"></div>`;
  await TAB[tab]($("#tabBody", root), ctx);
}

async function save(patch, el) {
  try {
    await api("/api/settings", { method: "POST", body: patch });
    await refresh();
    const tick = el?.closest(".settings-row, .switch, .field")?.querySelector(".saved-tick");
    if (tick) { tick.classList.add("show"); setTimeout(() => tick.classList.remove("show"), 1800); }
    announce(t("saved"));
    return true;
  } catch (e) {
    toast(errText(e), { type: "error" });
    return false;
  }
}

const tick = () => `<span class="saved-tick" aria-hidden="true">${icon("check", "sm")} ${esc(t("saved"))}</span>`;
const sw = (id, on, label, help = "", disabled = false) => `
  <label class="switch"><input type="checkbox" role="switch" id="${id}" ${on ? "checked" : ""} ${disabled ? "disabled" : ""}>
  <span><span class="label">${esc(label)}${tick()}</span>${help ? `<span class="help">${esc(help)}</span>` : ""}</span></label>`;

// -- Koppelingen ---------------------------------------------------------------------------------

async function tabConnections(box) {
  const st = S.state;
  const s = st.settings;
  box.innerHTML = `
    <section class="card" aria-labelledby="cnH">
      <h2 id="cnH" style="font-size:1.25rem">${esc(t("tab_connections"))}</h2>
      <div class="settings-row"><div class="what"><b>Pocket</b><span class="help">${esc(s.demo_mode ? t("demo_pocket") : t("conn_pocket_help"))}</span></div>
        <span class="badge ${st.pocket_ready ? "ok" : "off"}">${esc(st.pocket_ready ? (s.demo_mode ? t("demo") : t("connected")) : t("not_connected"))}</span>
        ${s.demo_mode ? "" : `<span class="mono muted">${esc(s.pocket_api_key_masked || "")}</span><button class="btn small" data-edit="pocket">${esc(st.pocket_ready ? t("change") : t("connect"))}</button>`}</div>
      <div id="edit-pocket" hidden></div>
      <div class="settings-row"><div class="what"><b>${esc(t("claude_in_app"))}</b><span class="help">${esc(t("conn_claude_help"))}</span></div>
        <span class="badge ${st.ai_ready ? "ok" : "off"}">${esc(st.ai_ready ? t("connected") : t("not_connected"))}</span>
        <span class="mono muted">${esc(s.anthropic_api_key_masked || (s.anthropic_from_env ? "ANTHROPIC_API_KEY" : ""))}</span>
        <button class="btn small" data-edit="anthropic">${esc(st.ai_ready ? t("change") : t("connect"))}</button>
        ${s.anthropic_api_key_masked ? `<button class="btn small ghost" id="forgetAk">${esc(t("remove"))}</button>` : ""}</div>
      <div id="edit-anthropic" hidden></div>
      <div class="settings-row"><div class="what"><b>Claude Desktop</b><span class="help">${esc(t("conn_desktop_help"))}</span></div><div id="dkBox3" style="flex:1;min-width:260px"></div></div>
      <div class="settings-row" style="display:block"><div class="what" style="margin-bottom:8px"><b>${esc(t("set_calendar"))}</b><span class="help">${esc(t("conn_calendar_help"))}</span></div><div id="calBox"></div></div>
    </section>
    <section class="card section" aria-labelledby="prH">
      <h2 id="prH" style="font-size:1.25rem">${icon("shield-check")} ${esc(t("privacy_title"))}</h2>
      <ul style="padding-left:1.2rem;margin:0"><li>${esc(t("privacy_files"))}</li><li>${esc(t("privacy_pocket"))}</li><li>${esc(t("privacy_claude"))}</li><li>${esc(t("privacy_desktop"))}</li></ul>
    </section>`;
  renderDesktopBox($("#dkBox3", box));
  await renderCalendar($("#calBox", box));
  $$("[data-edit]", box).forEach((b) => (b.onclick = () => keyEditor(box, b.dataset.edit)));
  $("#forgetAk", box)?.addEventListener("click", (e) => run(e.currentTarget, async () => {
    await api("/api/forget-key", { method: "POST", body: { key: "anthropic" } });
    await refresh();
    toast(t("key_removed"));
    tabConnections(box);
  }));
}

function keyEditor(box, which) {
  const el = $(`#edit-${which}`, box);
  el.hidden = !el.hidden;
  if (el.hidden) return;
  el.innerHTML = `<div class="field" style="margin:8px 0 16px">
    <label for="k-${which}">${esc(which === "pocket" ? t("ob_pocket_label") : t("ob_claude_a_label"))}</label>
    <div class="row"><input id="k-${which}" class="input-mid" type="password" autocomplete="off" spellcheck="false" placeholder="${which === "pocket" ? "pk_…" : "sk-ant-…"}">
    <button class="btn primary" id="kt-${which}">${esc(t("ob_pocket_test"))}</button></div>
    <p class="help">${which === "pocket" ? t("ob_pocket_h2") : t("ob_claude_a_h1")}</p><div id="km-${which}"></div></div>`;
  $(`#k-${which}`, el).focus();
  $(`#kt-${which}`, el).onclick = (e) => run(e.currentTarget, async () => {
    const key = $(`#k-${which}`, el).value.trim();
    if (!key) return;
    const r = await api(which === "pocket" ? "/api/test-pocket" : "/api/test-anthropic", { method: "POST", body: { key } });
    if (r.ok) { await refresh(); toast(t("connected")); tabConnections(box); }
    else $(`#km-${which}`, el).innerHTML = `<div class="msg err">${icon("circle-alert")}<p>${esc(t("err_" + r.code))}</p></div>`;
  }, { busy: t("testing") });
}

export async function renderCalendar(box) {
  const urls = (await api("/api/calendar-urls")).urls;
  box.innerHTML = `
    <div class="field">
      <label for="calUrls">${esc(t("calendar_link"))}</label>
      <textarea id="calUrls" rows="2" spellcheck="false" placeholder="https://calendar.google.com/calendar/ical/…/basic.ics">${esc(urls.join("\n"))}</textarea>
      <p class="help">${t("calendar_where")}</p>
    </div>
    <div class="row"><button class="btn small" id="calTest" type="button">${esc(t("calendar_test"))}</button><span id="calMsg" aria-live="polite"></span></div>`;
  $("#calTest", box).onclick = (e) => run(e.currentTarget, async () => {
    const list = $("#calUrls", box).value.split("\n").map((s) => s.trim()).filter(Boolean);
    await api("/api/settings", { method: "POST", body: { calendar_urls: list } });
    const r = await api("/api/test-calendar", { method: "POST" });
    const ok = r.results.length && r.results.every((x) => x.ok);
    $("#calMsg", box).innerHTML = !r.results.length ? esc(t("saved"))
      : r.results.map((x) => `<span class="badge ${x.ok ? "ok" : "err"}">${esc(x.ok ? tp("calendar_ok", x.events ?? x.count ?? 0) : t("calendar_bad"))}</span>`).join(" ");
    await refresh();
    if (ok) toast(t("saved"));
  }, { busy: t("testing") });
}

// -- Automatisch -----------------------------------------------------------------------------

async function tabAutomatic(box) {
  const st = S.state, s = st.settings;
  box.innerHTML = `
    <section class="card">
      ${sw("aSync", s.auto_sync, t("set_auto_sync"), t("set_auto_sync_help", { n: s.sync_interval_minutes }), s.demo_mode)}
      <div class="field" style="margin:8px 0 16px 58px"><label for="aInt">${esc(t("set_interval"))}${tick()}</label><input id="aInt" type="number" min="1" max="1440" class="input-short" value="${s.sync_interval_minutes}"></div>
      ${sw("aStart", st.autostart, t("set_autostart"), t("set_autostart_help"), s.demo_mode)}
      ${sw("aWeekly", s.weekly_auto, t("set_weekly"), t("set_weekly_help"))}
      ${sw("aStatus", s.ai_client_status, t("set_status"), t("set_status_help"), !st.ai_ready)}
      <div class="field" style="margin-top:16px"><label for="aSince">${esc(t("set_since"))}${tick()}</label><input id="aSince" type="date" class="input-short" style="max-width:200px" value="${esc(s.sync_since || "")}"><p class="help">${esc(t("set_since_help"))}</p></div>
      <div class="settings-row"><div class="what"><b>${esc(t("refetch_all"))}</b><span class="help">${esc(t("refetch_all_help"))}</span></div><button class="btn small" id="aFull">${icon("refresh-cw", "sm")} ${esc(t("refetch_all_btn"))}</button></div>
    </section>`;
  $("#aSync", box).onchange = (e) => save({ auto_sync: e.target.checked }, e.target);
  $("#aInt", box).onchange = (e) => save({ sync_interval_minutes: Math.max(1, +e.target.value || 15) }, e.target);
  $("#aWeekly", box).onchange = (e) => save({ weekly_auto: e.target.checked }, e.target);
  $("#aStatus", box).onchange = (e) => save({ ai_client_status: e.target.checked }, e.target);
  $("#aSince", box).onchange = (e) => save({ sync_since: e.target.value }, e.target);
  $("#aStart", box).onchange = async (e) => {
    try { await api("/api/autostart", { method: "POST", body: { enabled: e.target.checked } }); toast(t(e.target.checked ? "autostart_on" : "autostart_off")); }
    catch (err) { e.target.checked = !e.target.checked; toast(errText(err), { type: "error" }); }
  };
  $("#aFull", box).onclick = (e) => run(e.currentTarget, async () => {
    await api("/api/sync?full=true", { method: "POST" });
    await refresh();
    watch();
    toast(t("refetch_started"));
  });
}

// -- Sorteren ---------------------------------------------------------------------------------

async function tabSorting(box) {
  const st = S.state, s = st.settings;
  box.innerHTML = `
    <section class="card">
      <p class="muted">${esc(t("sorting_intro"))}</p>
      ${sw("sAi", s.ai_classify && st.ai_ready, t("set_ai_classify"), st.ai_ready ? t("set_ai_classify_help") : t("needs_claude"), !st.ai_ready)}
      <div class="field" style="margin-top:12px"><label for="sHits">${esc(t("set_min_hits"))}${tick()}</label><input id="sHits" type="number" min="1" max="10" class="input-short" value="${s.keyword_min_hits}"><p class="help">${esc(t("set_min_hits_help"))}</p></div>
      <div class="settings-row"><div class="what"><b>${esc(t("resort_all"))}</b><span class="help">${esc(t("resort_all_help"))}</span></div><button class="btn small" id="sResort">${icon("refresh-cw", "sm")} ${esc(t("resort_preview"))}</button></div>
      <div class="settings-row"><div class="what"><b>${esc(t("disc_title"))}</b><span class="help">${esc(t("disc_settings_help"))}</span></div><a class="btn small ai" href="#/clients/discover">${icon("sparkles", "sm")} ${esc(t("disc_go_short"))}</a></div>
    </section>`;
  $("#sAi", box).onchange = (e) => save({ ai_classify: e.target.checked }, e.target);
  $("#sHits", box).onchange = (e) => save({ keyword_min_hits: Math.min(10, Math.max(1, +e.target.value || 2)) }, e.target);
  $("#sResort", box).onclick = (e) => run(e.currentTarget, async () => {
    const r = await api("/api/resort/preview", { method: "POST", body: { scope: "all_auto" } });
    if (!r.moves.length) { toast(t("moves_none")); return; }
    const { dialog, close } = openDialog(`<div class="d-body"><h2>${esc(tp("moves_title", r.moves.length))}</h2><p class="help">${esc(t("moves_help"))}</p>
      <ul class="list">${r.moves.map((m, i) => `<li><label class="item check"><input type="checkbox" checked data-i="${i}"><span class="grow"><span class="t">${esc(m.title)}</span>
      <span class="m"><span>${esc(m.from_client || t("no_client"))} → ${esc(m.to_client)}${m.to_project ? " / " + esc(m.to_project) : ""}</span><span>${esc(sortReason(m.reason))}</span></span></span></label></li>`).join("")}</ul></div>
      <div class="d-foot"><button class="btn" data-close>${esc(t("cancel"))}</button><button class="btn primary" id="mvAll">${esc(t("moves_apply"))}</button></div>`, { wide: true });
    $("#mvAll", dialog).onclick = (ev) => run(ev.currentTarget, async () => {
      const chosen = $$("input[data-i]", dialog).filter((c) => c.checked).map((c) => r.moves[+c.dataset.i]);
      const res = await api("/api/resort/apply", { method: "POST", body: { moves: chosen } });
      close();
      await refresh();
      toast(tp("moved_n", res.moved), { action: res.log_id ? { label: t("undo"), fn: () => api("/api/resort/undo", { method: "POST", body: { log_id: res.log_id } }).then(() => toast(t("undone_short"))) } : null });
    });
  });
}

// -- Zoeken ------------------------------------------------------------------------------------

async function tabSearch(box) {
  const s = S.state.settings;
  const sem = await api("/api/semantic").catch(() => null);
  box.innerHTML = `
    <section class="card">
      ${sw("zSem", s.semantic_search, t("set_semantic"), t("set_semantic_help"))}
      ${sem ? `<p class="help" style="margin-left:58px">${esc(t("sem_status", { indexed: sem.indexed ?? 0, recordings: sem.recordings ?? 0 }))} ${esc(sem.model_downloaded ? t("sem_dl_yes") : t("sem_dl_no"))}</p>` : ""}
      <div class="row" style="margin-left:58px"><button class="btn small" id="zBuild" ${s.semantic_search ? "" : 'aria-disabled="true"'}>${icon("refresh-cw", "sm")} ${esc(t("sem_build"))}</button></div>
      <pre class="log" id="zLog" hidden></pre>
    </section>`;
  $("#zSem", box).onchange = async (e) => { if (await save({ semantic_search: e.target.checked }, e.target)) tabSearch(box); };
  $("#zBuild", box).onclick = (e) => {
    if (e.currentTarget.getAttribute("aria-disabled") === "true") { toast(t("sem_turn_on"), { type: "error" }); return; }
    run(e.currentTarget, async () => {
      await api("/api/semantic/build", { method: "POST" });
      await refresh();
      watch();
      const log = $("#zLog", box);
      log.hidden = false;
      const show = (st) => {
        log.textContent = (st.progress || []).map((l) => l.replace(/model_loading/, t("sem_loading")).replace(/model_done (\d+)/, (_, n) => t("sem_done", { n })).replace(/model_failed (.*)/, (_, m) => t("sem_failed", { m }))).join("\n");
      };
      const on = (ev) => { if (!box.isConnected) return window.removeEventListener("pb:state", on); show(ev.detail); };
      window.addEventListener("pb:state", on);
      window.addEventListener("pb:idle", () => { window.removeEventListener("pb:state", on); show(S.state); }, { once: true });
    });
  };
}

// -- Weergave ---------------------------------------------------------------------------------------

async function tabAppearance(box) {
  let theme = "system";
  try { theme = localStorage.getItem("pb-theme") || "system"; } catch { /* ignore */ }
  box.innerHTML = `
    <section class="card">
      <div class="field"><span class="label" id="lgL">${esc(t("language"))}</span>
        <div class="seg" role="group" aria-labelledby="lgL"><button data-lang="nl" lang="nl" aria-pressed="${LANG === "nl"}">Nederlands</button><button data-lang="en" lang="en" aria-pressed="${LANG === "en"}">English</button></div>
        <p class="help">${esc(t("language_help"))}</p></div>
      <div class="field"><span class="label" id="thL">${esc(t("theme"))}</span>
        <div class="seg" role="group" aria-labelledby="thL">${["system", "light", "dark"].map((v) => `<button data-theme="${v}" aria-pressed="${theme === v}">${esc(t("theme_" + v))}</button>`).join("")}</div></div>
    </section>`;
  $$("[data-lang]", box).forEach((b) => (b.onclick = () => window.pb.switchLang(b.dataset.lang)));
  $$("[data-theme]", box).forEach((b) => (b.onclick = () => {
    try { localStorage.setItem("pb-theme", b.dataset.theme); } catch { /* ignore */ }
    window.pb.applyTheme();
    $$("[data-theme]", box).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  }));
}

// -- Over -----------------------------------------------------------------------------------------

async function tabAbout(box, ctx) {
  const st = S.state, s = st.settings;
  box.innerHTML = `
    <section class="card">
      <img class="about-logo" src="/static/img/striks-logo-color.png" alt="Striks AI Consulting" width="140">
      <h2 style="margin-top:16px">Pocket Bridge <span class="muted" style="font-weight:500">v${esc(st.version)}</span></h2>
      <p>${esc(t("about_text"))} <a href="https://striks.ai" target="_blank" rel="noopener">striks.ai</a></p>
      <p class="help">${esc(t("about_license"))}</p>
    </section>
    <section class="card section">
      <h2 style="font-size:1.25rem">${esc(t("about_demo"))}</h2>
      <p class="muted">${esc(s.demo_mode ? t("about_demo_on") : t("about_demo_off"))}</p>
      ${s.demo_mode ? `<button class="btn" id="dmStop">${esc(t("demo_exit"))}</button>` : `<button class="btn" id="dmStart">${icon("play")} ${esc(t("w_demo"))}</button>`}
      <div class="settings-row" style="margin-top:16px"><div class="what"><b>${esc(t("setup_again"))}</b><span class="help">${esc(t("setup_again_help"))}</span></div><button class="btn small" id="obRestart">${esc(t("setup_again_btn"))}</button></div>
    </section>
    <section class="card section">
      <details><summary><b>${esc(t("tech_details"))}</b></summary>
        <div style="margin-top:12px">
          <div class="settings-row"><div class="what"><b>${esc(t("data_folder"))}</b><span class="mono muted">${esc(s.data_dir)}</span></div><button class="btn small" id="openDir">${icon("folder-open", "sm")} ${esc(t("open_folder"))}</button></div>
          <div class="settings-row"><div class="what"><b>${esc(t("config_file"))}</b><span class="mono muted">${esc(st.config_file)}</span></div></div>
          <div class="settings-row"><div class="what"><b>${esc(t("claude_model"))}</b><span class="help">${esc(t("claude_model_help"))}</span></div>
            <label class="sr-only" for="mdl">${esc(t("claude_model"))}</label>
            <select id="mdl" style="max-width:280px"><option value="claude-opus-5-5" ${s.claude_model === "claude-opus-5-5" ? "selected" : ""}>${esc(t("model_best"))}</option><option value="claude-sonnet-5-5" ${s.claude_model === "claude-sonnet-5-5" ? "selected" : ""}>${esc(t("model_fast"))}</option></select></div>
          ${sw("rawJson", s.keep_raw_json, t("keep_raw"), t("keep_raw_help"))}
          <div class="settings-row"><div class="what"><b>${esc(t("rebuild"))}</b><span class="help">${esc(t("rebuild_help"))}</span></div><button class="btn small" id="rebuild">${esc(t("rebuild_btn"))}</button></div>
        </div>
      </details>
    </section>`;
  $("#dmStart", box)?.addEventListener("click", (e) => run(e.currentTarget, async () => {
    await api("/api/demo/start", { method: "POST" });
    await refresh();
    ctx.go("#/setup/claude");
  }));
  $("#dmStop", box)?.addEventListener("click", (e) => window.pb.leaveDemo(e.currentTarget));
  $("#obRestart", box).onclick = (e) => run(e.currentTarget, async () => {
    await api("/api/onboarding/restart", { method: "POST" });
    await refresh();
    ctx.go("#/setup/welcome");
  });
  $("#openDir", box).onclick = (e) => run(e.currentTarget, () => api("/api/open-folder", { method: "POST" }));
  $("#mdl", box).onchange = (e) => save({ claude_model: e.target.value }, e.target);
  $("#rawJson", box).onchange = (e) => save({ keep_raw_json: e.target.checked }, e.target);
  $("#rebuild", box).onclick = (e) => run(e.currentTarget, async () => {
    const r = await api("/api/rebuild", { method: "POST" });
    toast(t("rebuild_done", { n: r.dossiers ?? 0 }));
  });
}

const TAB = { connections: tabConnections, automatic: tabAutomatic, sorting: tabSorting, search: tabSearch, appearance: tabAppearance, about: tabAbout };
