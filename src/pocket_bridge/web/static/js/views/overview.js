// Overzicht: what is new, what needs attention, and a way into Claude.
import { $, $$, api, esc, icon, t, tp, toast, errText, run, fmtDate, fmtRelative, fmtNum, firstName, md, openDialog, copyText } from "../core.js";
import { S, refresh, watch } from "../state.js";

export async function render(root, _param, ctx) {
  const st = S.state;
  const [stats, recent, cl] = await Promise.all([api("/api/stats"), api("/api/recordings?limit=6"), api("/api/clients")]);
  const hour = new Date().getHours();
  const greet = t(hour < 12 ? "greet_morning" : hour < 18 ? "greet_afternoon" : "greet_evening");
  const name = firstName(st.settings.user_name);
  const lead = stats.recordings
    ? [stats.last7 ? t("ov_lead_week", { n: tp("conv", stats.last7), k: tp("client", stats.last7_clients) }) : t("ov_lead_quiet"),
       stats.open_actions ? tp("ov_lead_actions", stats.open_actions) : ""].filter(Boolean).join(" ")
    : t("ov_lead_empty");
  const clientNames = cl.clients.filter((c) => !c.folder_only).map((c) => c.name);
  const chips = st.demo_questions?.length ? st.demo_questions : askChips(clientNames, cl.clients);

  root.innerHTML = `
    <div class="page-head">
      <div><h1>${esc(name ? `${greet}, ${name}` : greet)}</h1><p class="lead">${esc(lead)}</p></div>
      <div class="stack" style="align-items:flex-end;gap:6px">
        <button class="btn primary" id="syncBtn">${icon("refresh-cw")} ${esc(t("sync_now"))}</button>
        <span class="small muted" id="syncWhen">${st.last_sync ? esc(t("updated_at", { when: fmtRelative(st.last_sync) })) : ""}</span>
      </div>
    </div>
    <div id="syncArea"></div>
    <div id="attention" class="stack" style="margin-bottom:24px"></div>
    <div class="kpis">
      <a class="kpi" href="#/conversations"><span class="n">${fmtNum(stats.recordings)}</span><span class="l">${esc(t("kpi_conversations"))}</span>
        <span class="d">${esc(t("kpi_last30", { n: fmtNum(stats.last30) }))}</span></a>
      <a class="kpi" href="#/clients"><span class="n">${fmtNum(stats.clients)}</span><span class="l">${esc(t("kpi_clients"))}</span></a>
      <a class="kpi" href="#/actions"><span class="n">${fmtNum(stats.open_actions)}</span><span class="l">${esc(t("kpi_actions"))}</span></a>
      <div class="kpi"><span class="n">${fmtNum(Math.round(stats.minutes / 60))}</span><span class="l">${esc(t("kpi_hours"))}</span></div>
    </div>
    <div class="grid two section">
      <section class="card" aria-labelledby="recentH">
        <div class="card-head"><h2 id="recentH" style="font-size:1.25rem">${esc(t("ov_recent"))}</h2><a href="#/conversations">${esc(t("all_conversations"))}</a></div>
        ${recent.recordings.length ? `<ul class="list">${recent.recordings.map(recItem).join("")}</ul>` : emptyRecent()}
      </section>
      <div class="stack" style="gap:24px">
        <section class="card ai-made" aria-labelledby="askH">
          <div class="ai-label">${icon("sparkles", "sm")} Claude</div>
          <h2 id="askH" style="font-size:1.25rem">${esc(t("ov_ask_title"))}</h2>
          <p class="muted">${esc(st.ai_ready || st.settings.demo_mode ? t("ov_ask_help") : t("ov_ask_nokey"))}</p>
          ${(st.ai_ready || st.settings.demo_mode) && chips.length ? `<div class="suggest-chips">${chips.map((q) => `<button data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>`
            : `<a class="btn" href="#/settings/connections">${esc(t("connect_claude"))}</a>`}
        </section>
        <section class="card" aria-labelledby="weekH">
          <h2 id="weekH" style="font-size:1.25rem">${esc(t("weekly_title"))}</h2>
          <p class="muted">${esc(t("weekly_help"))}</p>
          <button class="btn" id="weekBtn">${icon("file-text")} ${esc(t("weekly_open"))}</button>
        </section>
      </div>
    </div>`;

  drawAttention($("#attention", root), st);
  $$("[data-q]", root).forEach((b) => (b.onclick = () => ctx.go(`#/ask/${encodeURIComponent(b.dataset.q)}`)));
  $("#weekBtn", root).onclick = () => openWeekly();
  $("#syncBtn", root).onclick = (e) => startSync(e.currentTarget, root);
  if (st.sync_running) { drawSync(root, st); watch(); }
  const onState = (e) => { if (!root.isConnected) return window.removeEventListener("pb:state", onState); if (e.detail.sync_running) drawSync(root, e.detail); };
  const onIdle = () => {
    window.removeEventListener("pb:idle", onIdle);
    if (root.isConnected && $("#syncArea", root).dataset.active) { delete $("#syncArea", root).dataset.active; ctx.go("#/overview"); }
  };
  window.addEventListener("pb:state", onState);
  window.addEventListener("pb:idle", onIdle);
  showLastResult(root, st);
}

function emptyRecent() {
  return `<div class="empty"><div class="bubble-icon">${icon("messages-square")}</div><p>${esc(t("empty_conversations"))}</p></div>`;
}

export function recItem(r) {
  return `<li><a class="item" href="#/conversations/${encodeURIComponent(r.pocket_id)}">
    <span class="grow"><span class="t">${esc(r.title)}</span>
    <span class="m"><span><time datetime="${esc(r.date)}">${esc(fmtDate(r.date))}</time></span>
    <span>${r.client ? esc(r.client) + (r.project ? " / " + esc(r.project) : "") : `<i>${esc(t("no_client"))}</i>`}</span>
    ${r.open_actions ? `<span>${esc(tp("open_actions", r.open_actions))}</span>` : ""}</span></span></a></li>`;
}

function askChips(names, clients) {
  const top = [...clients].filter((c) => !c.folder_only).sort((a, b) => (b.last_date || "").localeCompare(a.last_date || ""))[0]?.name;
  if (!top) return names.length ? [] : [t("chip_week")];
  return [t("chip_status", { c: top }), t("chip_actions", { c: top }), t("chip_week")];
}

function drawAttention(box, st) {
  const parts = [];
  if (st.proposal) {
    parts.push(`<div class="card tint-ai row between"><div class="row">${icon("sparkles")}<span>${esc(t("att_proposal", { n: st.proposal.clients }))}</span></div>
      <a class="btn ai small" href="#/proposal">${esc(t("review"))}</a></div>`);
  }
  for (const s of st.suggestions || []) {
    parts.push(`<div class="card row between"><div class="row">${icon("building-2")}<span>${esc(t("att_suggestion", { name: s.name, n: tp("conv", s.recording_ids.length) }))}</span></div>
      <div class="row"><button class="btn small" data-sugg="${esc(s.name)}" data-acc="1">${esc(t("add_client"))}</button>
      <button class="btn small ghost" data-sugg="${esc(s.name)}" data-acc="0">${esc(t("not_a_client"))}</button></div></div>`);
  }
  if (st.unsorted) {
    parts.push(`<div class="card tint-gold row between"><div class="row">${icon("inbox")}<span>${esc(tp("att_unsorted", st.unsorted))}</span></div>
      <a class="btn small" href="#/clients/unsorted">${esc(t("sort_now"))}</a></div>`);
  }
  if (!st.pocket_ready) parts.push(`<div class="msg err">${icon("circle-alert")}<p>${esc(t("att_pocket"))} <a href="#/settings/connections">${esc(t("fix"))}</a></p></div>`);
  box.innerHTML = parts.join("");
  $$("[data-sugg]", box).forEach((b) => (b.onclick = () => run(b, async () => {
    await api("/api/suggestions", { method: "POST", body: { name: b.dataset.sugg, accept: b.dataset.acc === "1" } });
    await refresh();
    toast(b.dataset.acc === "1" ? t("client_added", { name: b.dataset.sugg }) : t("noted"));
    window.pb.render();
  })));
}

let justSynced = false;

async function startSync(btn, root) {
  justSynced = true;
  await run(btn, async () => {
    const r = await api("/api/sync", { method: "POST" });
    if (!r.started) toast(t("sync_busy"));
    await refresh();
    drawSync(root, S.state);
    watch();
  });
}

function drawSync(root, st) {
  const area = $("#syncArea", root);
  if (!area) return;
  area.dataset.active = "1";
  const c = st.sync_count || {};
  const pct = c.total ? Math.round((c.done / c.total) * 100) : 0;
  area.innerHTML = `<div class="card" style="margin-bottom:24px" aria-live="polite">
    <div class="progress ${c.total ? "" : "indeterminate"}" role="progressbar" aria-label="${esc(t("sync_now"))}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="width:${pct}%"></i></div>
    <p style="margin:10px 0 0"><b>${esc(c.total ? t("ob_fetch_progress", { done: fmtNum(c.done), total: fmtNum(c.total) }) : t("ob_fetch_connecting"))}</b> ${c.title ? `<span class="muted">${esc(c.title)}</span>` : ""}</p></div>`;
}

function showLastResult(root, st) {
  const r = st.last_result;
  if (!justSynced || !r || st.sync_running || !r.finished) return;  // only after a fetch started here
  justSynced = false;
  const fresh = Date.now() - new Date(r.finished) < 10 * 60 * 1000;
  if (!fresh) return;
  const parts = [tp("sync_new", r.new || 0)];
  if (r.updated) parts.push(tp("sync_updated", r.updated));
  if (r.pending) parts.push(tp("sync_pending", r.pending));
  $("#syncArea", root).innerHTML = `<div class="msg ${r.errors?.length ? "warn" : "ok"}">${icon(r.errors?.length ? "info" : "circle-check")}
    <div><p>${esc(parts.join(". ") + ".")}</p>${r.errors?.length ? `<details><summary class="small">${esc(tp("ob_fetch_errors", r.errors.length))}</summary><pre class="log">${esc(r.errors.join("\n"))}</pre></details>` : ""}</div></div>`;
}

// -- Weekly overview (dialog) ---------------------------------------------------------------

function isoWeek(d) {
  const x = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = x.getUTCDay() || 7;
  x.setUTCDate(x.getUTCDate() + 4 - day);
  const y = x.getUTCFullYear();
  const w = Math.ceil(((x - Date.UTC(y, 0, 1)) / 86400000 + 1) / 7);
  return `${y}-W${String(w).padStart(2, "0")}`;
}

export async function openWeekly() {
  const st = S.state;
  const weeks = [];
  for (let i = 0; i < 8; i++) { const d = new Date(); d.setDate(d.getDate() - 7 * i); weeks.push(isoWeek(d)); }
  const { dialog, close } = openDialog(`
    <div class="d-body">
      <div class="row between"><h2 style="margin:0">${esc(t("weekly_title"))}</h2><button class="btn ghost small" data-close aria-label="${esc(t("close"))}">${icon("x")}</button></div>
      <div class="row" style="margin:16px 0">
        <label class="sr-only" for="wkSel">${esc(t("week"))}</label>
        <select id="wkSel" style="max-width:260px">${weeks.map((w, i) => `<option value="${w}">${esc(i === 0 ? t("this_week", { n: w.slice(-2) }) : i === 1 ? t("last_week", { n: w.slice(-2) }) : t("week_n", { n: w.slice(-2), y: w.slice(0, 4) }))}</option>`).join("")}</select>
        ${st.ai_ready ? `<label class="check" style="padding:0"><input type="checkbox" id="wkAi" checked> ${esc(t("weekly_ai"))}</label>` : ""}
        <button class="btn primary" id="wkMake">${esc(t("weekly_make"))}</button>
      </div>
      <div id="wkOut" class="md weekly-md prose"></div>
    </div>`, { wide: true, label: t("weekly_title") });
  const out = $("#wkOut", dialog);
  const load = async () => {
    const r = await api(`/api/weekly?week=${$("#wkSel", dialog).value}`);
    out.innerHTML = r.markdown ? md(r.markdown) : `<p class="muted">${esc(t("weekly_not_yet"))}</p>`;
  };
  $("#wkSel", dialog).onchange = load;
  $("#wkMake", dialog).onclick = (e) => run(e.currentTarget, async () => {
    const r = await api("/api/weekly", { method: "POST", body: { week: $("#wkSel", dialog).value, ai: !!$("#wkAi", dialog)?.checked } });
    out.innerHTML = md(r.markdown);
  }, { busy: $("#wkAi", dialog)?.checked ? t("claude_busy") : t("busy") });
  load().catch((e) => (out.textContent = errText(e)));
  return close;
}
