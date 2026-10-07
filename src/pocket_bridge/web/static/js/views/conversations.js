// Gesprekken: searchable list + detail (meta, why it was sorted here, summary, actions, transcript).
import { $, $$, api, esc, icon, t, tp, toast, errText, run, fmtDate, md, sortReason, copyText, announce, promptName } from "../core.js";
import { S, refresh } from "../state.js";

let filters = { q: "", client: "", unsorted: false };
let searchTimer;

export async function render(root, param, ctx) {
  const cl = await api("/api/clients");
  const clients = cl.clients.map((c) => c.name);
  root.innerHTML = `
    <div class="page-head"><div><h1>${esc(t("nav_conversations"))}</h1><p class="lead">${esc(t("conv_lead"))}</p></div></div>
    <div class="split ${param ? "has-detail" : ""}">
      <div class="list-col">
        <div class="card list-card">
          <div class="filters" role="search">
            <label class="sr-only" for="cq">${esc(t("search"))}</label>
            <input id="cq" type="search" placeholder="${esc(t("search_ph"))}" value="${esc(filters.q)}">
            <label class="sr-only" for="cc">${esc(t("filter_client"))}</label>
            <select id="cc"><option value="">${esc(t("all_clients"))}</option><option value="__unsorted" ${filters.unsorted ? "selected" : ""}>${esc(t("no_client"))}</option>
              ${clients.map((c) => `<option ${filters.client === c ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
          </div>
          <p class="small muted" id="cCount" aria-live="polite" style="margin:4px 8px"></p>
          <ul class="list" id="cList" aria-label="${esc(t("nav_conversations"))}"></ul>
        </div>
      </div>
      <div class="detail" id="cDetail"></div>
    </div>`;
  const loadList = async () => {
    const qs = new URLSearchParams({ limit: "300" });
    if (filters.q) qs.set("q", filters.q);
    if (filters.unsorted) qs.set("unsorted", "true");
    else if (filters.client) qs.set("client", filters.client);
    const r = await api(`/api/recordings?${qs}`);
    const list = $("#cList", root);
    if (!list) return;
    const rows = r.recordings;
    $("#cCount", root).textContent = filters.q ? tp("found_n", rows.length) : "";
    if (!rows.length) {
      list.innerHTML = `<li class="empty" style="padding:24px 8px"><p>${esc(filters.q ? t("search_none", { q: filters.q }) + (S.state.settings.semantic_search ? "" : " " + t("search_none_sem")) : filters.client || filters.unsorted ? t("empty_filter") : t("empty_conversations"))}</p>
        ${!filters.q && !filters.client && !filters.unsorted ? `<a class="btn small" href="#/overview">${esc(t("sync_now"))}</a>` : ""}</li>`;
      return;
    }
    list.innerHTML = rows.map((r) => `<li><a class="item" href="#/conversations/${encodeURIComponent(r.pocket_id)}" ${r.pocket_id === param ? 'aria-current="true"' : ""}>
      <span class="grow"><span class="t">${esc(r.title)}</span>
      <span class="m"><span><time datetime="${esc(r.date || "")}">${esc(fmtDate(r.date))}</time></span><span>${r.client ? esc(r.client) : `<i>${esc(t("no_client"))}</i>`}</span>
      ${r.open_actions ? `<span>${esc(tp("open_actions", r.open_actions))}</span>` : ""}</span>
      ${r.snippet ? `<span class="snip">${snippet(r.snippet)}</span>` : ""}</span></a></li>`).join("");
  };
  $("#cq", root).addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { filters.q = e.target.value.trim(); loadList().catch((err) => toast(errText(err), { type: "error" })); }, 250);
  });
  $("#cc", root).onchange = (e) => {
    filters.unsorted = e.target.value === "__unsorted";
    filters.client = filters.unsorted ? "" : e.target.value;
    loadList();
  };
  await loadList();
  if (param) await renderDetail($("#cDetail", root), param, clients);
  else $("#cDetail", root).innerHTML = `<div class="card empty"><div class="bubble-icon">${icon("messages-square")}</div><p>${esc(t("conv_pick"))}</p></div>`;
}

function snippet(s) {
  // search snippets mark hits with **…**
  return esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
}

function parseTranscript(text) {
  const turns = [];
  let cur = null;
  for (const line of (text || "").split("\n")) {
    const m = line.match(/^\*\*([^*]+)\*\*(?: \((\d\d:\d\d:\d\d)\))?\s*$/);
    if (m) { cur = { who: m[1], at: m[2] || "", lines: [] }; turns.push(cur); continue; }
    if (!line.trim()) continue;
    if (!cur) { cur = { who: "", at: "", lines: [] }; turns.push(cur); }
    cur.lines.push(line);
  }
  return turns;
}

async function renderDetail(box, id, clients) {
  box.innerHTML = `<div class="card"><div class="skel" style="width:60%;height:24px"></div><div class="skel" style="width:40%"></div><div class="skel"></div><div class="skel"></div></div>`;
  let r;
  try { r = await api(`/api/recordings/${encodeURIComponent(id)}`); }
  catch (e) { box.innerHTML = `<div class="msg err">${icon("circle-alert")}<p>${esc(errText(e))}</p></div>`; return; }
  const st = S.state;
  const canAi = st.ai_ready || (st.settings.demo_mode);
  const turns = parseTranscript(r.transcript);
  const reason = sortReason(r.client_source);
  document.title = `${r.title} · Pocket Bridge by Striks`;
  box.innerHTML = `
    <article class="card">
      <a class="back-link small" href="#/conversations">${icon("arrow-left", "sm")} ${esc(t("all_conversations"))}</a>
      <header class="detail-head">
        <h2 tabindex="-1">${esc(r.title)}</h2>
        <div class="meta-line">
          <span>${icon("calendar", "sm")} <time datetime="${esc(r.date)}">${esc(fmtDate(r.date))}</time></span>
          ${r.duration_minutes ? `<span>${icon("clock", "sm")} ${esc(t("minutes", { n: r.duration_minutes }))}</span>` : ""}
          <span class="client-chip">${icon("building-2", "sm")} ${r.client ? `<a href="#/clients/${encodeURIComponent(r.client)}">${esc(r.client)}</a>${r.project ? " / " + esc(r.project) : ""}` : esc(t("no_client"))}</span>
          <button class="link-btn" id="mvToggle" aria-expanded="false" aria-controls="mvRow">${esc(t("change"))}</button>
        </div>
        ${reason ? `<p class="reason">${icon(r.client_source?.startsWith("claude") ? "sparkles" : "info", "sm")} ${esc(reason)}</p>` : ""}
        <div id="mvRow" class="row" hidden style="margin-top:12px">
          <label class="sr-only" for="mvClient">${esc(t("move_to"))}</label>
          <select id="mvClient" style="max-width:240px"><option value="">${esc(t("no_client"))}</option>${clients.map((c) => `<option ${c === r.client ? "selected" : ""}>${esc(c)}</option>`).join("")}<option value="__new">${esc(t("new_client_opt"))}</option></select>
          <label class="sr-only" for="mvProj">${esc(t("project"))}</label>
          <input id="mvProj" style="max-width:200px" placeholder="${esc(t("project_optional"))}" value="${esc(r.project || "")}">
          <button class="btn primary small" id="mvGo">${esc(t("move"))}</button>
        </div>
        ${r.meeting || r.attendees.length ? `<p class="small muted" style="margin-top:8px">${icon("users", "sm")} ${esc(r.meeting || "")}${r.attendees.length ? " · " + esc(r.attendees.map((a) => a.replace(/<[^>]*>/, "").trim() || a).join(", ")) : ""}</p>` : ""}
      </header>
      <div class="toolbar">
        <button class="btn ai small" id="fuBtn" ${canAi ? "" : 'aria-disabled="true"'}>${icon("mail", "sm")} ${esc(t("followup_btn"))}</button>
        ${r.speakers.length ? `<button class="btn small" id="spBtn">${icon("users", "sm")} ${esc(t("speakers_btn"))}</button>` : ""}
        <button class="btn small ghost" id="revealBtn">${icon("folder-open", "sm")} ${esc(t("show_in_folder"))}</button>
      </div>
      ${canAi ? "" : `<p class="help">${esc(t("followup_needs"))}</p>`}
      <div id="panel"></div>
      ${r.summary ? `<section class="summary-box" aria-labelledby="sumH"><h3 id="sumH">${esc(t("summary"))}</h3><div class="md prose">${md(r.summary, { headingStart: 4 })}</div></section>` : ""}
      ${r.actions.length ? `<section aria-labelledby="actH" style="margin:16px 0"><h3 id="actH" style="font-size:1rem">${esc(t("nav_actions"))}</h3>
        <ul class="actions-list">${r.actions.map((a, i) => `<li class="${a.done ? "done" : ""}"><input type="checkbox" id="ra${i}" ${a.done ? "checked" : ""} data-text="${esc(a.text)}"><label class="txt" for="ra${i}">${esc(a.text)}</label></li>`).join("")}</ul></section>` : ""}
      <section aria-labelledby="trH"><h3 id="trH" style="font-size:1rem;margin-top:24px">${esc(t("transcript"))}</h3>
        <div class="transcript">${turns.map((tn) => `<div class="turn"><div class="who">${esc(tn.who)}${tn.at ? `<time>${esc(tn.at)}</time>` : ""}</div><div class="said">${tn.lines.map((l) => `<p>${esc(l)}</p>`).join("")}</div></div>`).join("") || `<p class="muted">${esc(t("no_transcript"))}</p>`}</div>
      </section>
    </article>`;
  if (matchMedia("(max-width: 800px)").matches) $("h2", box).focus();

  $("#mvToggle", box).onclick = (e) => {
    const row = $("#mvRow", box);
    row.hidden = !row.hidden;
    e.currentTarget.setAttribute("aria-expanded", String(!row.hidden));
    if (!row.hidden) $("#mvClient", box).focus();
  };
  $("#mvGo", box).onclick = (e) => run(e.currentTarget, async () => {
    let client = $("#mvClient", box).value;
    if (client === "__new") {
      client = await promptName({ title: t("new_client"), label: t("name") });
      if (!client) return;
    }
    const prev = { client: r.client || "", project: r.project || "" };
    const res = await api(`/api/recordings/${encodeURIComponent(id)}/assign`, { method: "POST", body: { client, project: client ? $("#mvProj", box).value.trim() : "" } });
    await refresh();
    toast(client ? t("moved_to", { client }) : t("moved_unsorted"), { action: { label: t("undo"), fn: async () => {
      await api(`/api/recordings/${encodeURIComponent(id)}/assign`, { method: "POST", body: prev });
      await refresh();
      window.pb.render();
    } } });
    if (res.suggestions?.length) suggestKeywords(box, client, res.suggestions);
    else window.pb.render();
  });
  $$(".actions-list input", box).forEach((cb) => (cb.onchange = async () => {
    cb.closest("li").classList.toggle("done", cb.checked);
    try { await api("/api/actions", { method: "POST", body: { pocket_id: id, text: cb.dataset.text, done: cb.checked } }); }
    catch (e) { cb.checked = !cb.checked; cb.closest("li").classList.toggle("done", cb.checked); toast(errText(e), { type: "error" }); }
  }));
  $("#revealBtn", box).onclick = (e) => run(e.currentTarget, () => api(`/api/open-folder?path=${encodeURIComponent(r.path)}`, { method: "POST" }));
  $("#fuBtn", box).onclick = (e) => {
    if (e.currentTarget.getAttribute("aria-disabled") === "true") { toast(t("followup_needs"), { type: "error" }); return; }
    followUp(e.currentTarget, $("#panel", box), id);
  };
  $("#spBtn", box)?.addEventListener("click", () => speakers($("#panel", box), r));
}


function suggestKeywords(box, client, words) {
  const p = $("#panel", box);
  p.innerHTML = `<div class="panel"><p>${esc(t("suggest_title", { client }))}</p>
    <div class="chips" style="margin:8px 0">${words.map((w, i) => `<label class="chip"><input type="checkbox" checked data-w="${esc(w)}" style="width:14px;height:14px"> ${esc(w)}</label>`).join("")}</div>
    <div class="row"><button class="btn primary small" id="kwAdd">${esc(t("add_kw"))}</button><button class="btn ghost small" id="kwNo">${esc(t("no_thanks"))}</button></div></div>`;
  $("#kwAdd", p).onclick = (e) => run(e.currentTarget, async () => {
    const chosen = $$("[data-w]", p).filter((c) => c.checked).map((c) => c.dataset.w);
    await api(`/api/clients/${encodeURIComponent(client)}/keywords`, { method: "POST", body: { keywords: chosen } });
    toast(t("saved"));
    window.pb.render();
  });
  $("#kwNo", p).onclick = () => window.pb.render();
  $("#kwAdd", p).focus();
}

async function followUp(btn, panel, id) {
  panel.innerHTML = `<div class="panel"><div class="ai-label">${icon("sparkles", "sm")} ${esc(t("followup_btn"))}</div><p class="muted">${esc(t("followup_wait"))}</p><div class="skel"></div><div class="skel" style="width:70%"></div></div>`;
  const m = await run(btn, () => api(`/api/recordings/${encodeURIComponent(id)}/followup`, { method: "POST" }), { busy: t("claude_busy") });
  if (!m) { panel.innerHTML = ""; return; }
  panel.innerHTML = `<div class="panel">
    <button class="btn ghost small close" aria-label="${esc(t("close"))}">${icon("x")}</button>
    <div class="ai-label">${icon("sparkles", "sm")} ${esc(t("followup_btn"))}</div>
    <div class="field"><label for="fuTo">${esc(t("to"))}</label><input id="fuTo" value="${esc((m.to || []).join(", "))}"></div>
    <div class="field"><label for="fuSubj">${esc(t("subject"))}</label><input id="fuSubj" value="${esc(m.subject)}"></div>
    <div class="field"><label for="fuBody">${esc(t("message"))}</label><textarea id="fuBody" rows="12">${esc(m.body)}</textarea></div>
    <div class="row"><button class="btn primary" id="fuMail">${icon("mail", "sm")} ${esc(t("mail_open"))}</button>
    <button class="btn" id="fuCopy">${icon("copy", "sm")} ${esc(t("copy"))}</button></div>
    ${m.example ? `<p class="help">${esc(t("example_result"))}</p>` : ""}</div>`;
  $(".close", panel).onclick = () => (panel.innerHTML = "");
  $("#fuMail", panel).onclick = () => {
    const q = new URLSearchParams({ subject: $("#fuSubj", panel).value, body: $("#fuBody", panel).value }).toString().replace(/\+/g, "%20");
    location.href = `mailto:${encodeURIComponent($("#fuTo", panel).value).replace(/%2C/g, ",").replace(/%40/g, "@")}?${q}`;
  };
  $("#fuCopy", panel).onclick = () => copyText(`${$("#fuSubj", panel).value}\n\n${$("#fuBody", panel).value}`);
  announce(t("followup_ready"));
  $("#fuSubj", panel).focus();
}

function speakers(panel, r) {
  const st = S.state;
  const firstLine = (label) => {
    const m = (r.transcript || "").split(`**${label}**`)[1];
    const line = (m || "").split("\n").map((l) => l.trim()).find((l) => l && !l.startsWith("(") && !l.startsWith("**"));
    return line ? line.slice(0, 90) : "";
  };
  const count = (label) => (r.transcript || "").split(`**${label}**`).length - 1;
  const labels = r.speakers;
  panel.innerHTML = `<div class="panel">
    <button class="btn ghost small close" aria-label="${esc(t("close"))}">${icon("x")}</button>
    <h3 style="font-size:1rem">${esc(t("speakers_title"))}</h3><p class="help">${esc(t("speakers_help"))}</p>
    ${labels.length ? `<datalist id="spList">${(r.attendees || []).map((a) => `<option value="${esc(a.replace(/<[^>]*>/, "").trim())}">`).join("")}</datalist>
    <div class="stack" style="margin-top:12px">${labels.map((l, i) => `<div class="field" style="margin:0">
      <label for="sp${i}">${esc(l)} <span class="muted small">· ${esc(t("times_n", { n: count(l) }))}</span></label>
      ${firstLine(l) ? `<span class="help">“${esc(firstLine(l))}”</span>` : ""}
      <input id="sp${i}" data-label="${esc(l)}" list="spList" placeholder="${esc(t("speaker_ph"))}"></div>`).join("")}</div>
    <div class="row" style="margin-top:12px">
      ${st.ai_ready ? `<button class="btn ai small" id="spGuess">${icon("sparkles", "sm")} ${esc(t("guess_btn"))}</button>` : ""}
      <span class="spacer"></span><button class="btn primary" id="spSave">${esc(t("save_speakers"))}</button></div>
    <p class="help" id="spMsg" aria-live="polite"></p>` : `<p class="muted">${esc(t("no_speakers"))}</p>`}</div>`;
  $(".close", panel).onclick = () => (panel.innerHTML = "");
  $("#spGuess", panel)?.addEventListener("click", (e) => run(e.currentTarget, async () => {
    const g = await api(`/api/recordings/${encodeURIComponent(r.pocket_id)}/speakers/guess`, { method: "POST" });
    let n = 0;
    $$("[data-label]", panel).forEach((inp) => { const v = g.mapping?.[inp.dataset.label]; if (v) { inp.value = v; n++; } });
    $("#spMsg", panel).textContent = n ? t("guess_ok", { k: n, n: labels.length }) : t("guess_none");
  }, { busy: t("claude_busy") }));
  $("#spSave", panel)?.addEventListener("click", (e) => run(e.currentTarget, async () => {
    const mapping = {};
    $$("[data-label]", panel).forEach((inp) => { if (inp.value.trim()) mapping[inp.dataset.label] = inp.value.trim(); });
    await api(`/api/recordings/${encodeURIComponent(r.pocket_id)}/speakers`, { method: "POST", body: { mapping } });
    toast(t("speakers_saved"));
    window.pb.render();
  }));
  $("input", panel)?.focus();
}
