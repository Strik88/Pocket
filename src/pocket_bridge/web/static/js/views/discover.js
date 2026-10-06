// "Claude stelt je klanten voor": who-are-you, cost estimate, run, progress. Then the review (proposal.js).
// Three routes: Claude in the app (API key), Claude Desktop (subscription, via MCP), or rules only.
import { $, api, esc, icon, t, tp, toast, errText, run, fmtEur, fmtNum, copyText, announce } from "../core.js";
import { S, refresh, watch } from "../state.js";
import { renderReview } from "./proposal.js";

/**
 * opts.onboarding: shown inside the setup flow (no page header, onboarding footer handled by caller)
 * opts.onApplied(result): after the user confirmed the proposal
 * opts.onReview(bool): called when the review is shown/hidden (onboarding hides its footer then)
 */
export async function renderDiscover(root, opts = {}) {
  const st = S.state;
  const existing = (await api("/api/discover/proposal")).proposal;
  if (existing && !st.discover?.running) return showReview(root, existing, opts);

  const est = await api("/api/discover/estimate");
  const mode = st.settings.onboarding.claude_mode;
  const route = st.ai_ready ? "claude" : est.demo_example ? "example" : mode === "desktop" && st.claude_desktop_connected ? "desktop" : "rules";
  opts.onReview?.(false);

  root.innerHTML = `
    <div class="ai-head">
      <span class="ai-orb">${icon("wand-sparkles")}</span>
      <div><h1>${esc(t("disc_title"))}</h1><p class="lead">${esc(t("disc_lead"))}</p></div>
    </div>
    ${est.recordings === 0 ? `<div class="msg">${icon("info")}<p>${esc(t("disc_none"))}</p></div>` : `
    <div class="card" style="margin:20px 0;box-shadow:none">
      <h2 style="font-size:1.125rem">${esc(t("disc_who"))}</h2>
      <p class="help" style="margin-bottom:12px">${esc(t("disc_who_help"))}</p>
      <div class="grid two" style="gap:12px 24px">
        <div class="field" style="margin:0"><label for="dName">${esc(t("disc_name"))}</label><input id="dName" autocomplete="name" value="${esc(st.settings.user_name || est.owner || "")}"></div>
        <div class="field" style="margin:0"><label for="dDomain">${esc(t("disc_domain"))}</label><input id="dDomain" placeholder="${esc(t("disc_domain_ph"))}" value="${esc((st.settings.own_domains?.length ? st.settings.own_domains : est.own_domains || []).join(", "))}"></div>
      </div>
    </div>
    <div id="dRoute"></div>`}
    <div id="dRun" aria-live="polite"></div>`;
  if (est.recordings === 0) return;

  const routeBox = $("#dRoute", root);
  const who = () => ({
    user_name: $("#dName", root).value.trim(),
    own_domains: $("#dDomain", root).value.split(/[,\s]+/).map((d) => d.trim()).filter(Boolean),
  });

  if (route === "claude") {
    routeBox.innerHTML = `
      <div class="estimate" style="margin-bottom:16px">
        <span>${icon("messages-square", "sm")} ${esc(tp("disc_reads", est.recordings))}</span>
        <span>${icon("clock", "sm")} ${esc(t("disc_time", { min: Math.max(1, Math.round(est.batches * 1.5)) }))}</span>
        <span>${icon("info", "sm")} ${t("disc_cost", { eur: `<b>${esc(fmtEur(est.eur))}</b>`, low: esc(fmtEur(est.eur_low)), high: esc(fmtEur(est.eur_high)) })}</span>
      </div>
      <p class="help" style="margin-bottom:16px">${esc(t("disc_sent"))}</p>
      <div class="row"><button class="btn ai cta-ai" id="dGo">${icon("sparkles")} ${esc(t("disc_go"))}</button>
      <button class="btn ghost" id="dRules">${esc(t("disc_rules_instead"))}</button></div>`;
    $("#dGo", root).onclick = (e) => start(e.currentTarget, "claude");
    $("#dRules", root).onclick = (e) => start(e.currentTarget, "rules");
  } else if (route === "example") {
    routeBox.innerHTML = `
      <div class="msg">${icon("info")}<p>${esc(t("disc_example_note"))}</p></div>
      <div class="row"><button class="btn ai" id="dGo">${icon("sparkles")} ${esc(t("disc_example_go"))}</button></div>`;
    $("#dGo", root).onclick = (e) => start(e.currentTarget, "example");
  } else if (route === "desktop") {
    const prompt = t("disc_desktop_prompt");
    routeBox.innerHTML = `
      <ol class="steps-howto">
        <li>${esc(t("disc_desktop_1"))}</li>
        <li>${esc(t("disc_desktop_2"))}<div class="copy-block" style="margin-top:6px"><pre>${esc(prompt)}</pre><button class="btn small" id="dCopy">${icon("copy", "sm")} ${esc(t("copy"))}</button></div></li>
        <li>${esc(t("disc_desktop_3"))}</li>
      </ol>
      <div class="row" style="margin-top:12px"><span class="badge working plain">${icon("loader-circle", "sm spin")} ${esc(t("disc_desktop_wait"))}</span>
      <button class="btn ghost" id="dRules">${esc(t("disc_rules_instead"))}</button></div>`;
    $("#dCopy", root).onclick = () => copyText(prompt);
    $("#dRules", root).onclick = (e) => start(e.currentTarget, "rules");
    api("/api/settings", { method: "POST", body: who() }).catch(() => {});
    pollDesktop(root, opts);
  } else {
    routeBox.innerHTML = `
      <div class="msg">${icon("info")}<p>${esc(t("disc_rules_note"))}</p></div>
      <div class="row"><button class="btn primary" id="dGo">${icon("wand-sparkles")} ${esc(t("disc_rules_go"))}</button></div>`;
    $("#dGo", root).onclick = (e) => start(e.currentTarget, "rules");
  }

  async function start(btn, kind) {
    await run(btn, async () => {
      const r = await api("/api/discover", { method: "POST", body: { route: kind, ...who() } });
      await refresh();
      showRunning(root, r.route);
      watch();
      await waitForDiscover(root, opts);
    });
  }
  if (st.discover?.running) {
    showRunning(root, "claude");
    watch();
    waitForDiscover(root, opts);
  }
}

function showRunning(root, route) {
  const box = $("#dRun", root);
  $("#dRoute", root)?.setAttribute("hidden", "");
  const d = S.state.discover || {};
  const pct = d.total ? Math.round((d.done / d.total) * 100) : 0;
  box.innerHTML = `
    <div class="card tint-ai" style="margin-top:16px">
      <div class="progress ${d.total > 1 ? "" : "indeterminate"}" role="progressbar" aria-label="${esc(t("disc_running"))}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="width:${pct}%"></i></div>
      <p style="margin:12px 0 4px"><b>${esc(route === "rules" ? t("disc_running_rules") : t("disc_running"))}</b></p>
      <p class="help">${esc(d.total > 1 ? t("disc_part", { done: d.done, total: d.total }) : t("disc_running_help"))}</p>
      <div class="skel" style="width:70%"></div><div class="skel" style="width:55%"></div><div class="skel" style="width:62%"></div>
    </div>`;
}

function waitForDiscover(root, opts) {
  return new Promise((resolve) => {
    const onState = (e) => {
      if (!root.isConnected) return cleanup();
      if (e.detail.discover?.running) showRunning(root, "claude");
    };
    const onIdle = async () => {
      cleanup();
      if (!root.isConnected) return resolve();
      const d = S.state.discover || {};
      if (d.code) {
        $("#dRoute", root)?.removeAttribute("hidden");
        $("#dRun", root).innerHTML = `<div class="msg err">${icon("circle-alert")}<p>${esc(t("err_" + d.code) !== "err_" + d.code ? t("err_" + d.code) : t("err_discover_failed"))}</p></div>`;
        announce(t("err_discover_failed"));
        return resolve();
      }
      const p = (await api("/api/discover/proposal")).proposal;
      if (p) showReview(root, p, opts);
      resolve();
    };
    const cleanup = () => { window.removeEventListener("pb:state", onState); window.removeEventListener("pb:idle", onIdle); };
    window.addEventListener("pb:state", onState);
    window.addEventListener("pb:idle", onIdle);
  });
}

function pollDesktop(root, opts) {
  const tick = async () => {
    if (!root.isConnected || !$("#dRoute", root) || $("#dRoute", root).hidden) return;
    try {
      const p = (await api("/api/discover/proposal")).proposal;
      if (p) return showReview(root, p, opts);
    } catch { /* keep polling */ }
    setTimeout(tick, 3000);
  };
  setTimeout(tick, 3000);
}

function showReview(root, proposal, opts) {
  opts.onReview?.(true);
  renderReview(root, proposal, {
    onboarding: opts.onboarding,
    onApplied: (res) => { opts.onReview?.(false); opts.onApplied?.(res); },
    onDismissed: () => { opts.onReview?.(false); renderDiscover(root, opts); },
  });
}
