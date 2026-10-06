// First run in focus mode: welcome, then Koppelen (Pocket, Claude), Inrichten (ophalen, klanten, indelen)
// and Afmaken (Claude Desktop, extra's). Progress lives on the server (settings.onboarding).
import { $, $$, api, esc, icon, t, tp, LANG, setLang, toast, errText, run, announce, fmtEur, fmtNum, copyText } from "../core.js";
import { S, refresh, watch } from "../state.js";
import { renderDiscover } from "./discover.js";
import { renderUnsorted } from "./unsorted.js";

const STEPS = ["pocket", "claude", "fetch", "discover", "sort", "desktop", "extras"];
const PHASES = [["connect", ["pocket", "claude"]], ["organise", ["fetch", "discover", "sort"]], ["finish", ["desktop", "extras"]]];
const OPTIONAL = new Set(["claude", "discover", "sort", "desktop", "extras"]);

let ctx;

export async function render(root, step, context) {
  ctx = context;
  const st = S.state;
  if (step === "welcome") return renderWelcome(root);
  if (step === "done") return renderDone(root);
  if (!STEPS.includes(step)) return ctx.go("#/setup/welcome");
  if (step !== "pocket" && !st.pocket_ready) return ctx.go("#/setup/pocket");
  if (st.settings.onboarding.step !== step) {
    api("/api/onboarding", { method: "POST", body: { step } }).catch(() => {});
  }
  const i = STEPS.indexOf(step);
  document.title = `${t("ob_" + step + "_title")} · Pocket Bridge by Striks`;
  root.innerHTML = `
    <div class="onb">
      <header class="onb-top">
        <a class="brand-mini" href="#/setup/welcome"><img src="/static/img/striks-icon-color.png" alt=""><span><b>Pocket Bridge</b> <span>by Striks</span></span></a>
        <ol class="phases" aria-label="${esc(t("ob_phases"))}">
          ${PHASES.map(([p, steps], pi) => {
            const cur = steps.includes(step);
            const done = STEPS.indexOf(steps[steps.length - 1]) < i;
            return `<li class="${done ? "done" : ""}" ${cur ? 'aria-current="step"' : ""}><i>${done ? icon("check", "sm") : pi + 1}</i>${esc(t("phase_" + p))}</li>`;
          }).join("")}
        </ol>
        <span class="spacer"></span>
        ${st.settings.demo_mode ? `<button class="btn small ghost" id="obDemoExit">${esc(t("demo_exit"))}</button>` : ""}
        ${st.pocket_ready ? `<a class="btn small ghost" href="#/overview" id="obLater">${esc(t("ob_later"))}</a>` : ""}
      </header>
      <div class="onb-progress" aria-hidden="true"><i style="width:${Math.round(((i + 1) / STEPS.length) * 100)}%"></i></div>
      <main class="onb-main" id="main" tabindex="-1">
        <div class="onb-card ${["discover", "sort"].includes(step) ? "wide" : ""}">
          <p class="onb-step">${esc(t("ob_step_of", { n: i + 1, total: STEPS.length }))}</p>
          <div id="stepBody"></div>
          <div class="onb-foot">
            ${i > 0 ? `<button class="btn ghost" id="obBack">${icon("arrow-left")} ${esc(t("back"))}</button>` : ""}
            <span class="spacer"></span>
            ${OPTIONAL.has(step) ? `<button class="btn ghost" id="obSkip">${esc(t("skip"))}</button>` : ""}
            <button class="btn primary" id="obNext">${esc(i === STEPS.length - 1 ? t("finish") : t("next"))} ${icon("arrow-right")}</button>
          </div>
        </div>
      </main>
    </div>`;
  $("#obBack")?.addEventListener("click", () => ctx.go(`#/setup/${STEPS[i - 1]}`));
  $("#obSkip")?.addEventListener("click", async () => {
    await api("/api/onboarding", { method: "POST", body: { skip: step } }).catch(() => {});
    next(step);
  });
  $("#obNext").addEventListener("click", (e) => {
    if (e.currentTarget.getAttribute("aria-disabled") === "true") {
      toast(t("ob_step_first_" + step) || t("ob_step_first"), { type: "error" });
      return;
    }
    next(step);
  });
  $("#obDemoExit")?.addEventListener("click", (e) => window.pb.leaveDemo(e.currentTarget));
  const body = $("#stepBody");
  await STEP_RENDER[step](body);
  const h1 = $("h1", body);
  if (h1) { h1.setAttribute("tabindex", "-1"); h1.focus({ preventScroll: true }); }
  window.scrollTo(0, 0);
}

function next(step) {
  const i = STEPS.indexOf(step);
  if (step === "pocket" && !S.state.pocket_ready) {
    toast(t("ob_pocket_needed"), { type: "error" });
    $("#pkKey")?.focus();
    return;
  }
  let target = STEPS[i + 1] || "done";
  // Claude Desktop already connected in the Claude step: nothing to do in the Desktop step
  if (target === "desktop" && S.state.claude_desktop_connected && S.state.settings.onboarding.claude_mode === "desktop") target = "extras";
  ctx.go(`#/setup/${target}`);
}

const setNextEnabled = (on) => {
  const b = $("#obNext");
  if (!b) return;
  if (on) b.removeAttribute("aria-disabled");
  else b.setAttribute("aria-disabled", "true");
};

// -- Welcome ----------------------------------------------------------------------------------

function renderWelcome(root) {
  document.title = "Pocket Bridge by Striks";
  root.innerHTML = `
    <div class="welcome">
      <header class="welcome-band">
        <div class="inner">
          <img class="logo" src="/static/img/striks-logo-color.png" alt="Striks AI Consulting" width="132">
          <p class="by">Pocket Bridge by Striks</p>
          <h1 id="wTitle" tabindex="-1">${esc(t("w_title"))}</h1>
          <p>${esc(t("w_lead"))}</p>
          <div class="lang-switch" role="group" aria-label="${esc(t("language"))}">
            <button data-lang="nl" lang="nl" aria-pressed="${LANG === "nl"}">Nederlands</button><button data-lang="en" lang="en" aria-pressed="${LANG === "en"}">English</button>
          </div>
        </div>
      </header>
      <main class="welcome-body" id="main" tabindex="-1">
        <h2>${esc(t("w_how"))}</h2>
        <div class="how">
          <div class="card"><span class="n">1</span><h3>${esc(t("w_how1_t"))}</h3><p class="muted">${esc(t("w_how1"))}</p></div>
          <div class="card ai-step"><span class="n">2</span><h3>${esc(t("w_how2_t"))}</h3><p class="muted">${esc(t("w_how2"))}</p></div>
          <div class="card"><span class="n">3</span><h3>${esc(t("w_how3_t"))}</h3><p class="muted">${esc(t("w_how3"))}</p></div>
        </div>
        <div class="row">
          <button class="btn cta" id="wStart">${esc(t("w_start"))} ${icon("arrow-right")}</button>
          <button class="btn" id="wDemo">${icon("play")} ${esc(t("w_demo"))}</button>
        </div>
        <p class="muted small" style="margin-top:12px">${esc(t("w_time"))}</p>
        <div class="privacy" style="margin-top:32px">
          <b>${icon("shield-check")} ${esc(t("privacy_title"))}</b>
          <ul><li>${esc(t("privacy_files"))}</li><li>${esc(t("privacy_pocket"))}</li><li>${esc(t("privacy_claude"))}</li></ul>
        </div>
      </main>
    </div>`;
  $$(".lang-switch button", root).forEach((b) => (b.onclick = () => window.pb.switchLang(b.dataset.lang)));
  $("#wStart").onclick = () => {
    const st = S.state;
    if (st.settings.demo_mode) return ctx.go("#/setup/claude");
    ctx.go(st.pocket_ready ? `#/setup/${st.settings.onboarding.step && !["welcome", "done"].includes(st.settings.onboarding.step) ? st.settings.onboarding.step : "claude"}` : "#/setup/pocket");
  };
  $("#wDemo").onclick = (e) => run(e.currentTarget, async () => {
    await api("/api/demo/start", { method: "POST" });
    await refresh();
    toast(t("demo_started"));
    ctx.go("#/setup/claude");
  });
  $("#wTitle").focus({ preventScroll: true });
}

// -- 1. Pocket ------------------------------------------------------------------------------------

async function stepPocket(body) {
  const st = S.state;
  body.innerHTML = `
    <h1>${esc(t("ob_pocket_title"))}</h1>
    <p class="lead">${esc(t("ob_pocket_lead"))}</p>
    <ol class="steps-howto">
      <li>${esc(t("ob_pocket_h1"))}</li>
      <li>${t("ob_pocket_h2")}</li>
      <li>${t("ob_pocket_h3")}</li>
    </ol>
    <div id="pkStatus"></div>
    <div class="field" id="pkField">
      <label for="pkKey">${esc(t("ob_pocket_label"))}</label>
      <div class="row">
        <input id="pkKey" class="input-mid" type="password" autocomplete="off" spellcheck="false" placeholder="pk_…" aria-describedby="pkHelp">
        <button class="btn primary" id="pkTest">${esc(t("ob_pocket_test"))}</button>
      </div>
      <p class="help" id="pkHelp">${esc(t("ob_pocket_help"))}</p>
    </div>`;
  const status = $("#pkStatus");
  const showOk = (total) => {
    status.innerHTML = `<div class="success-row">${icon("circle-check")}<span><b>${esc(t("connected"))}</b>${total != null ? ". " + esc(tp("ob_pocket_seen", total)) : ""}
      <span class="muted mono">${esc(S.state.settings.pocket_api_key_masked || "")}</span></span>
      <button class="link-btn" id="pkOther">${esc(t("ob_pocket_other"))}</button></div>`;
    $("#pkField").hidden = true;
    $("#pkOther").onclick = () => { $("#pkField").hidden = false; $("#pkKey").focus(); };
    setNextEnabled(true);
  };
  if (st.pocket_ready && !st.settings.demo_mode) showOk(null);
  else setNextEnabled(false);
  const test = async () => {
    const key = $("#pkKey").value.trim();
    if (!key) { $("#pkKey").focus(); return; }
    const r = await api("/api/test-pocket", { method: "POST", body: { key } });
    if (r.ok) {
      await refresh();
      showOk(r.total);
      announce(t("connected"));
      $("#obNext").focus();
    } else {
      status.innerHTML = `<div class="msg err">${icon("circle-alert")}<p>${esc(t("err_" + (r.code || "pocket_error")))}</p></div>`;
      $("#pkKey").focus();
    }
  };
  $("#pkTest").onclick = (e) => run(e.currentTarget, test, { busy: t("testing") });
  $("#pkKey").addEventListener("paste", () => setTimeout(() => $("#pkTest").click(), 50));
  $("#pkKey").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#pkTest").click(); });
}

// -- 2. Claude ------------------------------------------------------------------------------------

async function stepClaude(body) {
  const st = S.state;
  const mode = st.settings.onboarding.claude_mode || (st.ai_ready ? "api" : "");
  body.innerHTML = `
    <h1>${esc(t("ob_claude_title"))}</h1>
    <p class="lead">${esc(t("ob_claude_lead"))}</p>
    <fieldset class="choice" style="border:0;padding:0;margin:20px 0">
      <legend class="sr-only">${esc(t("ob_claude_title"))}</legend>
      <div class="choice-card">
        <input type="radio" name="cm" value="api" id="cm-api" aria-describedby="cm-api-d" ${mode === "api" ? "checked" : ""}>
        <label class="t" for="cm-api">${esc(t("ob_claude_a"))} <span class="badge ai">${esc(t("recommended"))}</span></label>
        <p class="d" id="cm-api-d">${esc(t("ob_claude_a_d"))}</p>
        <div class="more" data-for="api" hidden>
          <ol class="steps-howto">
            <li>${t("ob_claude_a_h1")}</li><li>${esc(t("ob_claude_a_h2"))}</li><li>${esc(t("ob_claude_a_h3"))}</li>
          </ol>
          <div id="akStatus"></div>
          ${st.settings.anthropic_from_env && !st.ai_ready ? `<div class="msg" id="akEnv">${icon("key-round")}<div><p>${esc(t("ob_claude_env"))}</p>
            <button class="btn small" type="button" id="akUseEnv">${esc(t("ob_claude_env_use"))}</button></div></div>` : ""}
          <div class="field" id="akField">
            <label for="akKey">${esc(t("ob_claude_a_label"))}</label>
            <div class="row">
              <input id="akKey" class="input-mid" type="password" autocomplete="off" spellcheck="false" placeholder="sk-ant-…">
              <button class="btn primary" id="akTest" type="button">${esc(t("ob_pocket_test"))}</button>
            </div>
          </div>
          <label class="check" style="margin-top:8px"><input type="checkbox" id="akStatusOpt" ${st.settings.ai_client_status ? "checked" : ""}>
            <span><b>${esc(t("set_status"))}</b><span class="help" style="display:block">${esc(t("set_status_help"))}</span></span></label>
        </div>
      </div>
      <div class="choice-card">
        <input type="radio" name="cm" value="desktop" id="cm-desktop" aria-describedby="cm-desktop-d" ${mode === "desktop" ? "checked" : ""}>
        <label class="t" for="cm-desktop">${esc(t("ob_claude_b"))}</label>
        <p class="d" id="cm-desktop-d">${esc(t("ob_claude_b_d"))}</p>
        <div class="more" data-for="desktop" hidden><div id="dkBox"></div></div>
      </div>
      <div class="choice-card">
        <input type="radio" name="cm" value="none" id="cm-none" aria-describedby="cm-none-d" ${mode === "none" ? "checked" : ""}>
        <label class="t" for="cm-none">${esc(t("ob_claude_c"))}</label>
        <p class="d" id="cm-none-d">${esc(t("ob_claude_c_d"))}</p>
      </div>
    </fieldset>
    <div class="privacy">
      <b>${icon("shield-check")} ${esc(t("ob_claude_privacy"))}</b>
      <ul><li>${esc(t("ob_claude_privacy1"))}</li><li>${esc(t("ob_claude_privacy2"))}</li><li>${esc(t("ob_claude_privacy4"))}</li><li>${esc(t("ob_claude_privacy3"))}</li></ul>
    </div>`;

  const akOk = () => {
    $("#akStatus").innerHTML = `<div class="success-row">${icon("circle-check")}<span><b>${esc(t("ob_claude_a_ok"))}</b>
      <span class="muted mono">${esc(S.state.settings.anthropic_api_key_masked || (S.state.settings.anthropic_from_env ? "ANTHROPIC_API_KEY" : ""))}</span></span>
      <button class="link-btn" type="button" id="akOther">${esc(t("ob_pocket_other"))}</button></div>`;
    $("#akField").hidden = true;
    $("#akOther").onclick = (e) => { e.preventDefault(); $("#akField").hidden = false; $("#akKey").focus(); };
  };
  if (st.ai_ready) akOk();
  $("#akTest").onclick = (e) => {
    e.preventDefault();
    run(e.currentTarget, async () => {
      const key = $("#akKey").value.trim();
      if (!key) return $("#akKey").focus();
      const r = await api("/api/test-anthropic", { method: "POST", body: { key } });
      if (r.ok) { await refresh(); akOk(); announce(t("ob_claude_a_ok")); }
      else $("#akStatus").innerHTML = `<div class="msg err">${icon("circle-alert")}<p>${esc(t("err_" + (r.code || "claude_error")))}</p></div>`;
    }, { busy: t("testing") });
  };
  $("#akUseEnv")?.addEventListener("click", (e) => {
    e.preventDefault();
    run(e.currentTarget, async () => {
      await api("/api/onboarding", { method: "POST", body: { claude_mode: "api" } });
      const r = await api("/api/test-anthropic", { method: "POST", body: { key: "" } });
      await refresh();
      if (r.ok) { $("#akEnv")?.remove(); akOk(); announce(t("ob_claude_a_ok")); }
      else $("#akStatus").innerHTML = `<div class="msg err">${icon("circle-alert")}<p>${esc(t("err_" + (r.code || "claude_error")))}</p></div>`;
    }, { busy: t("testing") });
  });
  $("#akStatusOpt").addEventListener("change", (e) => {
    api("/api/settings", { method: "POST", body: { ai_client_status: e.target.checked } }).then(refresh).catch((err) => toast(errText(err), { type: "error" }));
  });
  $("#akKey").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#akTest").click(); } });
  renderDesktopBox($("#dkBox"));

  const sync = () => {
    const v = $("input[name=cm]:checked", body)?.value || "";
    $$(".more", body).forEach((m) => (m.hidden = m.dataset.for !== v));
    setNextEnabled(!!v);
  };
  $$("input[name=cm]", body).forEach((r) => r.addEventListener("change", sync));
  // The whole card selects its option, except clicks on fields and buttons inside it
  $$(".choice-card", body).forEach((card) => card.addEventListener("click", (e) => {
    if (e.target.closest("input, button, a, label, select, textarea")) return;
    const radio = $("input[name=cm]", card);
    if (!radio.checked) { radio.checked = true; sync(); }
  }));
  sync();
  // Save the choice when moving on
  const nextBtn = $("#obNext");
  nextBtn.addEventListener("click", async (e) => {
    const v = $("input[name=cm]:checked", body)?.value;
    if (!v) { e.stopImmediatePropagation(); toast(t("ob_claude_choose"), { type: "error" }); return; }
    if (v === "api" && !S.state.ai_ready) {
      e.stopImmediatePropagation();
      toast(t("ob_claude_need_key"), { type: "error" });
      $("#akKey").focus();
      return;
    }
    await api("/api/onboarding", { method: "POST", body: { claude_mode: v } }).catch(() => {});
    await refresh();
  }, { capture: true });
}

/** Connect-to-Claude-Desktop box (used in the Claude step and the Desktop step). */
export function renderDesktopBox(box, { big = false } = {}) {
  const st = S.state;
  if (!st.claude_desktop_installed) {
    box.innerHTML = `<div class="msg warn">${icon("info")}<div><p>${esc(t("dk_not_installed"))}</p>
      <p><a href="https://claude.ai/download" target="_blank" rel="noopener">claude.ai/download</a> · <button class="link-btn" type="button" id="dkRecheck">${esc(t("check_again"))}</button></p></div></div>`;
    $("#dkRecheck", box).onclick = async (e) => { e.preventDefault(); await refresh(); renderDesktopBox(box, { big }); };
    return;
  }
  if (st.claude_desktop_connected) {
    const seen = st.claude_desktop_seen;
    box.innerHTML = `<div class="success-row">${icon("circle-check")}<span><b>${esc(t("dk_connected"))}</b> ${esc(seen ? t("dk_seen") : t("dk_restart"))}</span>
      ${seen ? "" : `<button class="link-btn" type="button" id="dkRecheck">${esc(t("check_again"))}</button>`}</div>`;
    $("#dkRecheck", box)?.addEventListener("click", async (e) => { e.preventDefault(); await refresh(); renderDesktopBox(box, { big }); });
    return;
  }
  box.innerHTML = `<p class="help">${esc(t("dk_explain"))}</p>
    <button class="btn ${big ? "primary" : ""}" type="button" id="dkConnect">${icon("link")} ${esc(t("dk_connect"))}</button>`;
  $("#dkConnect", box).onclick = (e) => {
    e.preventDefault();
    run(e.currentTarget, async () => {
      const r = await api("/api/connect-claude-desktop", { method: "POST" });
      if (!r.ok) { toast(t("err_desktop_config"), { type: "error" }); return; }
      await refresh();
      renderDesktopBox(box, { big });
      announce(t("dk_connected"));
    });
  };
}

// -- 3. Fetch ---------------------------------------------------------------------------------------

async function stepFetch(body) {
  const st = S.state;
  const demo = st.settings.demo_mode;
  const folders = demo ? null : await api("/api/folders");
  const haveAny = (await api("/api/stats")).recordings > 0;
  const since = st.settings.sync_since;
  const range = !since ? "all" : sinceRange(since);
  body.innerHTML = `
    <h1>${esc(t("ob_fetch_title"))}</h1>
    <p class="lead">${esc(demo ? t("ob_fetch_lead_demo") : t("ob_fetch_lead"))}</p>
    ${demo ? "" : `
    <div class="field">
      <span class="label" id="fLabel">${esc(t("ob_fetch_folder"))}</span>
      <div class="folder-crumb" aria-labelledby="fLabel">${icon("folder")} <span id="fPath">${esc(folders.current)}</span></div>
      <div class="row" style="margin-top:8px">
        <button class="btn small" id="fPick">${icon("folder-open")} ${esc(t("ob_fetch_other"))}</button>
        ${folders.quick.map((q) => `<button class="btn small ghost" data-path="${esc(q.path)}">${esc(t("folder_" + q.kind))}</button>`).join("")}
      </div>
      <details style="margin-top:8px"><summary class="small">${esc(t("ob_fetch_type"))}</summary>
        <div class="row" style="margin-top:8px"><input id="fType" class="input-mid" value="${esc(folders.current)}" aria-label="${esc(t("ob_fetch_folder"))}"><button class="btn small" id="fTypeSave">${esc(t("use"))}</button></div>
      </details>
      <p class="help">${esc(t("ob_fetch_folder_help"))}</p>
    </div>
    <div class="field">
      <span class="label" id="rLabel">${esc(t("ob_fetch_range"))}</span>
      <div class="seg" role="group" aria-labelledby="rLabel">
        ${["3m", "1y", "all"].map((r) => `<button data-r="${r}" aria-pressed="${range === r}">${esc(t("range_" + r))}</button>`).join("")}
      </div>
      <p class="help">${esc(t("ob_fetch_range_help"))}</p>
    </div>`}
    <div id="fetchBox"></div>`;

  if (!demo) {
    const setFolder = async (path) => {
      if (!path) return;
      await api("/api/settings", { method: "POST", body: { data_dir: path } });
      await refresh();
      $("#fPath").textContent = S.state.settings.data_dir;
      $("#fType").value = S.state.settings.data_dir;
      toast(t("ob_fetch_folder_set"));
    };
    $("#fPick").onclick = (e) => run(e.currentTarget, async () => {
      const r = await api("/api/folders/pick", { method: "POST" });
      if (!r.supported) { $("details", body).open = true; $("#fType").focus(); return; }
      if (!r.cancelled) await setFolder(r.path);
    }, { busy: t("ob_fetch_picking") });
    $$("[data-path]", body).forEach((b) => (b.onclick = () => setFolder(b.dataset.path)));
    $("#fTypeSave").onclick = () => setFolder($("#fType").value.trim());
    $$(".seg button", body).forEach((b) => (b.onclick = async () => {
      $$(".seg button", body).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      await api("/api/settings", { method: "POST", body: { sync_since: rangeToSince(b.dataset.r) } });
    }));
    if (!since && !haveAny) {
      // default: last 3 months on a first run
      await api("/api/settings", { method: "POST", body: { sync_since: rangeToSince("3m") } });
      $$(".seg button", body).forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.r === "3m")));
    }
  }

  const box = $("#fetchBox");
  const drawIdle = () => {
    box.innerHTML = `<button class="btn cta" id="fGo">${icon("download")} ${esc(haveAny ? t("ob_fetch_again") : t("ob_fetch_go"))}</button>`;
    $("#fGo").onclick = start;
  };
  const drawProgress = (st) => {
    const c = st.sync_count || {};
    const pct = c.total ? Math.round((c.done / c.total) * 100) : 0;
    box.innerHTML = `<div aria-live="polite">
      <div class="progress ${c.total ? "" : "indeterminate"}" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}" aria-label="${esc(t("ob_fetch_go"))}"><i style="width:${pct}%"></i></div>
      <p style="margin-top:10px"><b>${esc(c.total ? t("ob_fetch_progress", { done: fmtNum(c.done), total: fmtNum(c.total) }) : t("ob_fetch_connecting"))}</b></p>
      ${c.title ? `<p class="muted small">${esc(c.title)}</p>` : ""}
      <p class="help">${esc(t("ob_fetch_wait"))}</p></div>`;
  };
  const drawDone = async (st) => {
    const r = st.last_result || {};
    const n = (await api("/api/stats")).recordings;
    box.innerHTML = `<div class="success-row">${icon("circle-check")}<span><b>${esc(tp("ob_fetch_done", n))}</b></span></div>
      ${r.pending ? `<p class="help">${esc(tp("ob_fetch_pending", r.pending))}</p>` : ""}
      ${(r.errors || []).length ? `<div class="msg warn">${icon("info")}<p>${esc(tp("ob_fetch_errors", r.errors.length))}</p></div>` : ""}
      <button class="link-btn" id="fAgain">${esc(t("ob_fetch_again"))}</button>`;
    $("#fAgain").onclick = start;
    setNextEnabled(true);
    $("#obNext").focus();
  };
  async function start() {
    setNextEnabled(false);
    try {
      await api("/api/sync", { method: "POST" });
    } catch (e) { toast(errText(e), { type: "error" }); return; }
    await refresh();
    drawProgress(S.state);
    watch();
  }
  const onState = (e) => {
    if (!box.isConnected) return window.removeEventListener("pb:state", onState);
    if (e.detail.sync_running) drawProgress(e.detail);
  };
  const onIdle = async () => {
    if (!box.isConnected) return window.removeEventListener("pb:idle", onIdle);
    if (S.state.last_result) drawDone(S.state);
  };
  window.addEventListener("pb:state", onState);
  window.addEventListener("pb:idle", onIdle);
  if (st.sync_running) { drawProgress(st); setNextEnabled(false); watch(); }
  else if (haveAny && st.last_result) { drawDone(st); }
  else { drawIdle(); setNextEnabled(haveAny); }
}

function rangeToSince(r) {
  if (r === "all") return "";
  const d = new Date();
  if (r === "3m") d.setMonth(d.getMonth() - 3);
  else d.setFullYear(d.getFullYear() - 1);
  return d.toISOString().slice(0, 10);
}
function sinceRange(since) {
  const days = (Date.now() - new Date(since)) / 86400000;
  return days < 200 ? "3m" : "1y";
}

// -- 4. Discover ------------------------------------------------------------------------------------

async function stepDiscover(body) {
  await renderDiscover(body, {
    onboarding: true,
    onApplied: () => { setNextEnabled(true); },
    onReview: (reviewing) => { $(".onb-foot").hidden = reviewing; },
  });
}

// -- 5. Sort -------------------------------------------------------------------------------------------

async function stepSort(body) {
  await renderUnsorted(body, { onboarding: true });
}

// -- 6. Claude Desktop ---------------------------------------------------------------------------------

async function stepDesktop(body) {
  const st = S.state;
  body.innerHTML = `
    <h1>${esc(t("ob_desktop_title"))}</h1>
    <p class="lead">${esc(t("ob_desktop_lead"))}</p>
    <div class="card tint-ai" style="margin:16px 0"><p class="eyebrow">${esc(t("ob_desktop_example"))}</p><p style="margin:0"><q>${esc(t("ob_desktop_q"))}</q></p></div>
    <div id="dkBox2"></div>
    <details style="margin-top:24px"><summary>${esc(t("dev_options"))}</summary>
      <p class="help" style="margin-top:8px">${esc(t("dev_claude_code"))}</p>
      <div class="copy-block"><pre>${esc(st.claude_code_command)}</pre><button class="btn small" data-copy="cc">${icon("copy", "sm")} ${esc(t("copy"))}</button></div>
      <p class="help" style="margin-top:12px">${esc(t("dev_manual"))}</p>
      <div class="copy-block"><pre>${esc(st.manual_snippet)}</pre><button class="btn small" data-copy="ms">${icon("copy", "sm")} ${esc(t("copy"))}</button></div>
    </details>`;
  renderDesktopBox($("#dkBox2"), { big: true });
  $$("[data-copy]", body).forEach((b) => (b.onclick = () => copyText(b.dataset.copy === "cc" ? st.claude_code_command : st.manual_snippet)));
}

// -- 7. Extras --------------------------------------------------------------------------------------------

async function stepExtras(body) {
  const s = S.state.settings;
  const demo = s.demo_mode;
  body.innerHTML = `
    <h1>${esc(t("ob_extras_title"))}</h1>
    <p class="lead">${esc(t("ob_extras_lead"))}</p>
    ${demo ? `<div class="msg">${icon("info")}<p>${esc(t("ob_extras_demo"))}</p></div>` : ""}
    <label class="switch"><input type="checkbox" role="switch" id="exAuto" ${s.auto_sync ? "checked" : ""} ${demo ? "disabled" : ""}>
      <span><span class="label">${esc(t("set_auto_sync"))}</span><span class="help">${esc(t("set_auto_sync_help", { n: s.sync_interval_minutes }))}</span></span></label>
    <label class="switch"><input type="checkbox" role="switch" id="exStart" ${S.state.autostart ? "checked" : ""} ${demo ? "disabled" : ""}>
      <span><span class="label">${esc(t("set_autostart"))}</span><span class="help">${esc(t("set_autostart_help"))}</span></span></label>
    ${S.state.ai_ready ? `<label class="switch"><input type="checkbox" role="switch" id="exStatus" ${s.ai_client_status ? "checked" : ""}>
      <span><span class="label">${esc(t("set_status"))}</span><span class="help">${esc(t("set_status_help"))}</span></span></label>` : ""}
    <label class="switch"><input type="checkbox" role="switch" id="exSem" ${s.semantic_search ? "checked" : ""}>
      <span><span class="label">${esc(t("set_semantic"))}</span><span class="help">${esc(t("set_semantic_help"))}</span></span></label>
    <details style="margin-top:12px" ${s.calendar_count ? "open" : ""}><summary><b>${esc(t("set_calendar"))}</b> <span class="muted small">${esc(t("optional"))}</span></summary>
      <div style="margin-top:12px" id="calBox"></div>
    </details>`;
  const save = async (patch) => {
    try { await api("/api/settings", { method: "POST", body: patch }); await refresh(); toast(t("saved")); }
    catch (e) { toast(errText(e), { type: "error" }); }
  };
  $("#exAuto").onchange = (e) => save({ auto_sync: e.target.checked });
  $("#exSem").onchange = (e) => save({ semantic_search: e.target.checked });
  $("#exStatus")?.addEventListener("change", (e) => save({ ai_client_status: e.target.checked }));
  $("#exStart").onchange = async (e) => {
    try { await api("/api/autostart", { method: "POST", body: { enabled: e.target.checked } }); toast(t(e.target.checked ? "autostart_on" : "autostart_off")); }
    catch (err) { e.target.checked = !e.target.checked; toast(errText(err), { type: "error" }); }
  };
  const { renderCalendar } = await import("./settings.js");
  await renderCalendar($("#calBox"));
}

// -- Done ---------------------------------------------------------------------------------------------------

async function renderDone(root) {
  const stats = await api("/api/stats");
  const st = S.state;
  document.title = `${t("ob_done_title")} · Pocket Bridge by Striks`;
  root.innerHTML = `
    <div class="onb"><div class="onb-progress" aria-hidden="true"><i style="width:100%"></i></div>
    <main class="onb-main" id="main" tabindex="-1"><div class="onb-card">
      <img src="/static/img/striks-icon-color.png" alt="" width="56" style="margin-bottom:16px">
      <h1 tabindex="-1">${esc(t("ob_done_title"))}</h1>
      <p class="lead">${esc(t("ob_done_lead"))}</p>
      <div class="kpis" style="margin:24px 0">
        <div class="kpi"><span class="n">${fmtNum(stats.recordings)}</span><span class="l">${esc(t("kpi_conversations"))}</span></div>
        <div class="kpi"><span class="n">${fmtNum(stats.clients)}</span><span class="l">${esc(t("kpi_clients"))}</span></div>
        <div class="kpi"><span class="n">${fmtNum(stats.recordings - stats.unsorted)}</span><span class="l">${esc(t("kpi_sorted"))}</span></div>
        <div class="kpi ${stats.unsorted ? "attn" : ""}"><span class="n">${fmtNum(stats.unsorted)}</span><span class="l">${esc(t("kpi_unsorted"))}</span></div>
      </div>
      <h2>${esc(t("ob_done_next"))}</h2>
      <ul>
        <li>${esc(t("ob_done_n1"))}</li>
        <li>${esc(st.ai_ready ? t("ob_done_n2") : t("ob_done_n2b"))}</li>
        <li>${esc(st.claude_desktop_connected ? t("ob_done_n3") : t("ob_done_n3b"))}</li>
      </ul>
      <div class="onb-foot"><span class="spacer"></span><button class="btn cta" id="doneGo">${esc(t("ob_done_go"))} ${icon("arrow-right")}</button></div>
    </div></main></div>`;
  $("h1", root).focus({ preventScroll: true });
  $("#doneGo").onclick = (e) => run(e.currentTarget, async () => {
    await api("/api/onboarding", { method: "POST", body: { completed: true } });
    await refresh();
    ctx.go("#/overview");
  });
}

const STEP_RENDER = { pocket: stepPocket, claude: stepClaude, fetch: stepFetch, discover: stepDiscover, sort: stepSort, desktop: stepDesktop, extras: stepExtras };
