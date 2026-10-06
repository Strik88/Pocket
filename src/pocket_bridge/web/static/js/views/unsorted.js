// Conversations without a client: pick one per row, or let Claude (or the rules) propose moves.
import { $, $$, api, esc, icon, t, tp, toast, errText, run, fmtDate, announce, sortReason, promptName } from "../core.js";
import { S, refresh } from "../state.js";

export async function renderUnsorted(root, { onboarding = false } = {}) {
  const [recs, cl] = await Promise.all([api("/api/recordings?unsorted=true&skip_ignored=true&limit=500"), api("/api/clients")]);
  const clients = cl.clients.filter((c) => !c.folder_only).map((c) => c.name);
  const rows = recs.recordings;
  let head = `<h1>${esc(onboarding ? t("ob_sort_title") : t("unsorted_title"))}</h1>`;
  const sorted = cl.clients.filter((c) => c.recordings);
  if (onboarding && sorted.length) {
    head += `<p class="lead">${esc(t("ob_sort_result"))}</p><div class="chips" style="margin:8px 0 20px">${sorted.map((c) =>
      `<span class="chip">${icon("building-2", "sm")} ${esc(c.name)} · ${esc(tp("conv", c.recordings))}</span>`).join("")}</div>`;
  }
  const ignoredNote = recs.ignored ? `<p class="help">${esc(tp("unsorted_ignored", recs.ignored))}</p>` : "";
  if (!rows.length) {
    root.innerHTML = `${head}<div class="msg ok">${icon("circle-check")}<p>${esc(t("unsorted_none"))}</p></div>${ignoredNote}`;
    return;
  }
  root.innerHTML = `
    ${head}
    <p class="lead">${esc(tp("unsorted_lead", rows.length))}</p>
    <div class="row" style="margin:16px 0">
      ${S.state.ai_ready && clients.length ? `<button class="btn ai" id="uClaude" aria-describedby="uClaudeNote">${icon("sparkles")} ${esc(t("unsorted_claude"))}</button>` : ""}
      ${clients.length ? `<button class="btn" id="uRules">${icon("refresh-cw")} ${esc(t("unsorted_rules"))}</button>` : ""}
    </div>
    ${S.state.ai_ready && clients.length ? `<p class="help" id="uClaudeNote" style="margin-top:-8px">${esc(t("unsorted_claude_note"))}</p>` : ""}
    <div id="uMoves"></div>
    <div class="card" style="padding:8px 16px">
      <ul class="list" id="uList">
        ${rows.map((r) => `
          <li class="item" data-id="${esc(r.pocket_id)}" style="align-items:center;flex-wrap:wrap">
            <div class="grow"><a class="t" href="#/conversations/${encodeURIComponent(r.pocket_id)}">${esc(r.title)}</a>
              <span class="m"><span>${esc(fmtDate(r.date))}</span>${r.duration_minutes ? `<span>${esc(t("minutes", { n: r.duration_minutes }))}</span>` : ""}</span></div>
            <label class="sr-only" for="us-${esc(r.pocket_id)}">${esc(t("move_to"))}</label>
            <select id="us-${esc(r.pocket_id)}" class="input-mid" style="max-width:260px">
              <option value="">${esc(t("pick_client"))}</option>
              ${clients.map((c) => `<option>${esc(c)}</option>`).join("")}
              <option value="__new">${esc(t("new_client_opt"))}</option>
            </select>
          </li>`).join("")}
      </ul>
    </div>${ignoredNote}`;

  $$("#uList select", root).forEach((sel) => (sel.onchange = async () => {
    const li = sel.closest("li");
    let client = sel.value;
    if (client === "__new") {
      client = await promptName({ title: t("new_client"), label: t("name") });
      if (!client) { sel.value = ""; return; }
    }
    if (!client) return;
    try {
      await api(`/api/recordings/${encodeURIComponent(li.dataset.id)}/assign`, { method: "POST", body: { client } });
      li.remove();
      await refresh();
      toast(t("moved_to", { client }), { action: { label: t("undo"), fn: async () => {
        await api(`/api/recordings/${encodeURIComponent(li.dataset.id)}/assign`, { method: "POST", body: { client: "" } });
        await refresh();
        renderUnsorted(root, { onboarding });
      } } });
      if (!$("#uList li", root)) renderUnsorted(root, { onboarding });
    } catch (e) {
      sel.value = "";
      toast(errText(e), { type: "error" });
    }
  }));

  const preview = (btn, useAi) => run(btn, async () => {
    const r = await api("/api/resort/preview", { method: "POST", body: { scope: "unsorted", use_ai: useAi } });
    showMoves(root, r.moves, onboarding);
  }, { busy: useAi ? t("claude_busy") : t("busy") });
  $("#uClaude", root)?.addEventListener("click", (e) => preview(e.currentTarget, true));
  $("#uRules", root)?.addEventListener("click", (e) => preview(e.currentTarget, false));
}

function showMoves(root, moves, onboarding) {
  const box = $("#uMoves", root);
  if (!moves.length) {
    box.innerHTML = `<div class="msg">${icon("info")}<p>${esc(t("moves_none"))}</p></div>`;
    announce(t("moves_none"));
    return;
  }
  box.innerHTML = `
    <div class="card ai-made" style="margin-bottom:16px">
      <h2 style="font-size:1.125rem">${esc(tp("moves_title", moves.length))}</h2>
      <p class="help">${esc(t("moves_help"))}</p>
      <ul class="list">${moves.map((m, i) => `
        <li><label class="item check" style="padding:10px 4px">
          <input type="checkbox" checked data-i="${i}">
          <span class="grow"><span class="t">${esc(m.title)}</span>
          <span class="m"><span>${icon("arrow-right", "sm")} ${esc(m.to_client)}${m.to_project ? " / " + esc(m.to_project) : ""}</span><span>${esc(sortReason(m.reason))}</span></span></span>
        </label></li>`).join("")}</ul>
      <div class="row end" style="margin-top:12px"><button class="btn primary" id="mApply">${esc(t("moves_apply"))}</button></div>
    </div>`;
  $("#mApply", box).onclick = (e) => run(e.currentTarget, async () => {
    const chosen = $$("input[data-i]", box).filter((c) => c.checked).map((c) => moves[+c.dataset.i]);
    const r = await api("/api/resort/apply", { method: "POST", body: { moves: chosen } });
    await refresh();
    toast(tp("moved_n", r.moved), { action: r.log_id ? { label: t("undo"), fn: async () => {
      await api("/api/resort/undo", { method: "POST", body: { log_id: r.log_id } });
      await refresh();
      renderUnsorted(root, { onboarding });
    } } : null });
    renderUnsorted(root, { onboarding });
  });
}
