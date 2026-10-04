// Pocket Bridge web app (no build step, no dependencies).
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
let STATE = null;
let LANG = "nl";
let CLIENTS = [];
let chatHistory = [];
let pollTimer = null;

function tr(key, vars = {}) {
  let s = (I18N[LANG] && I18N[LANG][key]) || I18N.en[key] || key;
  for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, v);
  return s;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.add("hidden"), 3500);
}

function setMsg(id, text, ok = true) {
  const el = $(id);
  el.textContent = text;
  el.className = "msg " + (ok ? "ok" : "bad");
}

function busy(btn, on) {
  if (!btn) return;
  if (on) { btn._label = btn.textContent; btn.textContent = tr("generating"); btn.disabled = true; }
  else { btn.textContent = btn._label || btn.textContent; btn.disabled = false; }
}

// Minimal, safe Markdown renderer for transcripts and answers.
function md(src) {
  const lines = esc(src).split("\n");
  let html = "", inList = false, inFm = false;
  const inline = (s) =>
    s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|\s)_(.+?)_(?=\s|$)/g, "$1<i>$2</i>").replace(/`(.+?)`/g, "<code>$1</code>").replace(/\[\[(.+?)\]\]/g, "<span class='link'>$1</span>");
  lines.forEach((line, i) => {
    if (i === 0 && line === "---") { inFm = true; return; }
    if (inFm) { if (line === "---") inFm = false; return; }
    const li = line.match(/^\s*[-*] (\[[ xX]\] )?(.*)$/);
    if (li) {
      if (!inList) { html += "<ul>"; inList = true; }
      const box = li[1] ? `<input type="checkbox" disabled ${/x/i.test(li[1]) ? "checked" : ""}> ` : "";
      html += `<li>${box}${inline(li[2])}</li>`;
      return;
    }
    if (inList) { html += "</ul>"; inList = false; }
    const h = line.match(/^(#{1,6}) (.*)$/);
    if (h) { const n = Math.min(6, h[1].length + 1); html += `<h${n}>${inline(h[2])}</h${n}>`; }
    else if (line.trim()) html += `<p>${inline(line)}</p>`;
  });
  if (inList) html += "</ul>";
  return html;
}

// ---------------------------------------------------------------- i18n / nav

function applyLang() {
  document.documentElement.lang = LANG;
  $$("[data-i18n]").forEach((el) => (el.textContent = tr(el.dataset.i18n)));
  $$("[data-i18n-html]").forEach((el) => (el.innerHTML = tr(el.dataset.i18nHtml)));
  $$("[data-i18n-placeholder]").forEach((el) => (el.placeholder = tr(el.dataset.i18nPlaceholder)));
  $$(".lang button").forEach((b) => b.classList.toggle("active", b.dataset.lang === LANG));
}

const VIEWS = {
  dashboard: () => renderDashboard(),
  clients: () => loadClients(),
  recordings: () => loadRecordings(),
  actions: () => loadActions(),
  reports: () => renderReports(),
  ask: () => renderAsk(),
  settings: () => renderSettings(),
  setup: () => renderSetup(),
};

function show(view) {
  if (!$("#view-" + view)) view = "dashboard";
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + view));
  $$(".sidebar a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  VIEWS[view]();
}

window.addEventListener("hashchange", () => show(location.hash.slice(1)));

// ---------------------------------------------------------------- state

async function refreshState() {
  STATE = await api("/api/state");
  LANG = STATE.settings.language || "nl";
  $("#version").textContent = "v" + STATE.version;
  return STATE;
}

function clientOptions(sel, { all = false, unsorted = false, current = "" } = {}) {
  sel.innerHTML =
    (all ? `<option value="">${tr("all_clients")}</option>` : "") +
    (unsorted ? `<option value="__unsorted">${tr("unsorted")}</option>` : "") +
    STATE.settings.clients.map((c) => `<option ${c.name === current ? "selected" : ""}>${esc(c.name)}</option>`).join("");
  if (current) sel.value = current;
}

// ---------------------------------------------------------------- setup

function renderSetup() {
  const s = STATE.settings;
  $("#pocketKey").placeholder = s.pocket_api_key_masked || "pk_…";
  $("#anthropicKey").placeholder = s.anthropic_api_key_masked || (s.anthropic_from_env ? "(ANTHROPIC_API_KEY)" : "sk-ant-…");
  $("#dataDir").value = s.data_dir;
  $("#claudeCodeCmd").textContent = STATE.claude_code_command;
  $("#manualSnippet").textContent = STATE.manual_snippet;
  $("#officialUrl").textContent = STATE.pocket_official_mcp;
  const done = { 1: STATE.pocket_ready, 2: !!s.data_dir, 3: s.clients.length > 0, 4: STATE.ai_ready, 5: STATE.claude_desktop_connected };
  $$(".step").forEach((el) => el.classList.toggle("done", !!done[el.dataset.step]));
  $("#setupClients").innerHTML = s.clients
    .map((c) => `<div class="pill-row"><b>${esc(c.name)}</b> <span class="muted">${esc(c.keywords.join(", "))}</span></div>`)
    .join("");
}

$("#testPocket").onclick = async () => {
  const key = $("#pocketKey").value.trim();
  setMsg("#pocketMsg", tr("running"));
  const r = await api("/api/test-pocket", { method: "POST", body: { key } }).catch((e) => ({ ok: false, error: e.message }));
  if (r.ok) {
    if (key) await api("/api/settings", { method: "POST", body: { pocket_api_key: key } });
    setMsg("#pocketMsg", tr("key_ok", { n: r.total }));
    $("#pocketKey").value = "";
    await refreshState();
    renderSetup();
  } else setMsg("#pocketMsg", tr("key_bad") + r.error, false);
};

$("#saveDir").onclick = async () => {
  await api("/api/settings", { method: "POST", body: { data_dir: $("#dataDir").value.trim() } });
  await refreshState();
  setMsg("#dirMsg", tr("saved") + ": " + STATE.settings.data_dir);
  renderSetup();
};

$("#setupAddClient").onclick = () => {
  location.hash = "#clients";
  setTimeout(() => openClientForm(), 50);
};

$("#testAnthropic").onclick = async () => {
  const key = $("#anthropicKey").value.trim();
  setMsg("#anthropicMsg", tr("running"));
  const r = await api("/api/test-anthropic", { method: "POST", body: { key } }).catch((e) => ({ ok: false, error: e.message }));
  if (r.ok) {
    if (key) await api("/api/settings", { method: "POST", body: { anthropic_api_key: key } });
    setMsg("#anthropicMsg", tr("claude_ok"));
    $("#anthropicKey").value = "";
    await refreshState();
    renderSetup();
  } else setMsg("#anthropicMsg", tr("key_bad") + r.error, false);
};

$("#connectDesktop").onclick = async () => {
  const r = await api("/api/connect-claude-desktop", { method: "POST" });
  if (r.ok) setMsg("#connectMsg", tr("connected_ok", { path: r.path }));
  else setMsg("#connectMsg", r.error, false);
  await refreshState();
  renderSetup();
};

$$("pre.copy").forEach((pre) =>
  pre.addEventListener("click", () => {
    navigator.clipboard?.writeText(pre.textContent);
    toast("✓ " + tr("copy"));
  })
);

// ---------------------------------------------------------------- dashboard

function stat(el, label, ok, okText, offText) {
  el.innerHTML = `<div class="stat-label">${esc(label)}</div><div class="stat-value ${ok ? "ok" : "off"}">${ok ? "● " + esc(okText) : "○ " + esc(offText)}</div>`;
}

function renderDashboard() {
  stat($("#statPocket"), tr("pocket"), STATE.pocket_ready, tr("connected"), tr("not_connected"));
  stat($("#statClaude"), tr("claude_api"), STATE.ai_ready, tr("connected"), tr("optional_off"));
  stat($("#statDesktop"), tr("claude_desktop"), STATE.claude_desktop_connected, tr("connected"), tr("not_connected"));
  const s = STATE.settings;
  const auto = s.auto_sync ? tr("every_min", { n: s.sync_interval_minutes }) : tr("auto_off");
  const last = STATE.last_sync ? new Date(STATE.last_sync).toLocaleString() : tr("never");
  const msg = STATE.last_result ? " — " + STATE.last_result.message : "";
  $("#syncInfo").textContent = STATE.pocket_ready ? `${tr("last_sync")}: ${last}${msg} · ${auto}` : tr("setup_needed");
  $("#syncLog").textContent = STATE.progress.join("\n");
  $("#syncLog").classList.toggle("hidden", !STATE.progress.length);
  $("#syncNow").disabled = $("#fullSync").disabled = STATE.sync_running || !STATE.pocket_ready;
  if (STATE.sync_running || STATE.job_running) pollSync();
  api("/api/recordings?limit=8").then((r) => {
    $("#recentList").innerHTML = recItems(r.recordings);
    $$("#recentList .item").forEach((el) => (el.onclick = () => gotoRecording(el.dataset.id)));
  });
}

async function startSync(full = false) {
  await api("/api/sync?full=" + full, { method: "POST" });
  toast(tr("sync_started"));
  if (!location.hash.includes("dashboard")) location.hash = "#dashboard";
  else pollSync();
}

function pollSync() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    await refreshState();
    if ($("#view-dashboard").classList.contains("active")) renderDashboard();
    if ($("#view-settings").classList.contains("active")) renderSemantic();
    if (STATE.sync_running || STATE.job_running) pollSync();
  }, 1500);
}

$("#syncNow").onclick = () => startSync(false);
$("#fullSync").onclick = () => startSync(true);
$("#setupSync").onclick = () => startSync(false);
$("#openFolder").onclick = () => api("/api/open-folder", { method: "POST" });

// ---------------------------------------------------------------- clients

async function loadClients() {
  const r = await api("/api/clients");
  CLIENTS = r.clients;
  $("#clientList").innerHTML =
    CLIENTS.map(
      (c, i) => `<div class="card client">
        <h3>${esc(c.name)}</h3>
        <div class="muted small">${c.recordings} ${tr("conversations")} · ${c.open_actions} ${tr("open_actions")}${c.last_date ? " · " + tr("last") + " " + c.last_date.slice(0, 10) : ""}</div>
        <div class="tags">${c.folder_only ? `<span class="tag">${tr("folder_only")}</span>` : ""}${c.keywords.map((k) => `<span class="tag">${esc(k)}</span>`).join("")}${c.pocket_tags.map((k) => `<span class="tag pt">#${esc(k)}</span>`).join("")}${(c.email_domains || []).map((k) => `<span class="tag">@${esc(k)}</span>`).join("")}</div>
        ${(c.projects || []).length ? `<div class="small muted">${tr("project")}: ${c.projects.map((p) => esc(p.name)).join(", ")}</div>` : ""}
        <div class="row between card-foot"><a href="#recordings" data-filter="${esc(c.name)}">${tr("nav_recordings")} →</a>
          <div class="row">${c.folder_only ? "" : `<button data-edit="${i}">${tr("edit")}</button>`}<button data-brief="${esc(c.name)}">${tr("briefing_btn")}</button><button data-del="${esc(c.name)}" class="ghost">${tr("delete")}</button></div></div>
      </div>`
    ).join("") + (r.unsorted ? `<div class="card client"><h3>${tr("unsorted")}</h3><div class="muted small">${r.unsorted} ${tr("conversations")}</div><div class="row between card-foot"><a href="#recordings" data-filter="__unsorted">${tr("nav_recordings")} →</a></div></div>` : "");
  $$("[data-edit]").forEach((b) => (b.onclick = () => openClientForm(CLIENTS[+b.dataset.edit])));
  $$("[data-del]").forEach((b) => (b.onclick = async () => {
    if (!confirm(tr("confirm_delete", { name: b.dataset.del }))) return;
    await api("/api/clients/" + encodeURIComponent(b.dataset.del), { method: "DELETE" });
    await refreshState();
    loadClients();
  }));
  $$("[data-brief]").forEach((b) => (b.onclick = () => { window._briefClient = b.dataset.brief; location.hash = "#reports"; }));
  $$("[data-filter]").forEach((a) => (a.onclick = () => (window._recFilter = a.dataset.filter)));
}

function projectsToText(projects) {
  return (projects || []).map((p) => (p.keywords.length ? `${p.name}: ${p.keywords.join(", ")}` : p.name)).join("\n");
}

function textToProjects(text) {
  return text.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const idx = l.indexOf(":");
    const name = idx >= 0 ? l.slice(0, idx) : l;
    const kw = idx >= 0 ? l.slice(idx + 1) : "";
    return { name: name.trim(), keywords: kw.split(",").map((k) => k.trim()).filter(Boolean) };
  });
}

function openClientForm(c = null) {
  const f = $("#clientForm");
  f.classList.remove("hidden");
  f.innerHTML = `
    <label>${tr("name")}</label><input id="cfName" value="${esc(c?.name || "")}">
    <label>${tr("keywords")}</label><input id="cfKeywords" value="${esc((c?.keywords || []).join(", "))}" placeholder="Acme, Jan de Vries, Project Phoenix">
    <label>${tr("email_domains")}</label><input id="cfDomains" value="${esc((c?.email_domains || []).join(", "))}" placeholder="acme.nl">
    <label>${tr("pocket_tags")}</label><input id="cfTags" value="${esc((c?.pocket_tags || []).join(", "))}">
    <label>${tr("projects_label")}</label><textarea id="cfProjects" rows="3" placeholder="Phoenix: migratie, phoenix&#10;Training">${esc(projectsToText(c?.projects))}</textarea>
    <label>${tr("notes")}</label><textarea id="cfNotes" rows="2">${esc(c?.notes || "")}</textarea>
    <div class="row end">${c && STATE.ai_ready ? `<button id="cfStatus">${tr("refresh_status")}</button>` : ""}<button id="cfCancel">${tr("cancel")}</button><button class="primary" id="cfSave">${tr("save")}</button></div>
    <div class="msg" id="cfMsg"></div>`;
  $("#cfName").focus();
  f.scrollIntoView({ behavior: "smooth", block: "start" });
  $("#cfCancel").onclick = () => f.classList.add("hidden");
  if ($("#cfStatus")) $("#cfStatus").onclick = async (e) => {
    busy(e.target, true);
    try {
      const r = await api(`/api/clients/${encodeURIComponent(c.name)}/status`, { method: "POST" });
      setMsg("#cfMsg", r.status.slice(0, 300));
    } catch (err) { setMsg("#cfMsg", err.message, false); }
    busy(e.target, false);
  };
  $("#cfSave").onclick = async () => {
    const split = (v) => v.split(",").map((x) => x.trim()).filter(Boolean);
    const body = {
      name: $("#cfName").value.trim(),
      keywords: split($("#cfKeywords").value),
      email_domains: split($("#cfDomains").value),
      pocket_tags: split($("#cfTags").value),
      projects: textToProjects($("#cfProjects").value),
      notes: $("#cfNotes").value.trim(),
    };
    if (!body.name) return;
    await api("/api/clients?original_name=" + encodeURIComponent(c?.name || ""), { method: "POST", body });
    f.classList.add("hidden");
    await refreshState();
    loadClients();
    toast(tr("saved"));
  };
}

$("#addClient").onclick = () => openClientForm();

// ---------------------------------------------------------------- recordings

function recItems(list) {
  if (!list.length) return `<p class="muted">${tr("no_recordings")}</p>`;
  return list
    .map(
      (r) => `<div class="item" data-id="${esc(r.pocket_id)}">
        <div class="item-title">${esc(r.title)}</div>
        <div class="muted small">${esc((r.date || "").slice(0, 16).replace("T", " "))} · ${esc(r.client || tr("unsorted"))}${r.project ? " / " + esc(r.project) : ""}${r.open_actions ? " · " + r.open_actions + " ☐" : ""}</div>
        ${r.snippet ? `<div class="snippet">${md(r.snippet)}</div>` : ""}
      </div>`
    )
    .join("");
}

function gotoRecording(id) {
  window._openRec = id;
  if ($("#view-recordings").classList.contains("active")) { window._openRec = undefined; openRecording(id); }
  else location.hash = "#recordings";
}

let searchTimer;
async function loadRecordings() {
  const sel = $("#recClient");
  const keep = window._recFilter ?? sel.value;
  window._recFilter = undefined;
  clientOptions(sel, { all: true, unsorted: true });
  sel.value = keep || "";
  const q = $("#search").value.trim();
  const params = new URLSearchParams({ q, limit: 300 });
  if (sel.value === "__unsorted") params.set("unsorted", "true");
  else if (sel.value) params.set("client", sel.value);
  const r = await api("/api/recordings?" + params);
  $("#recList").innerHTML = recItems(r.recordings);
  $$("#recList .item").forEach((el) => (el.onclick = () => openRecording(el.dataset.id)));
  if (window._openRec) { const id = window._openRec; window._openRec = undefined; openRecording(id); }
}

$("#search").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadRecordings, 300); };
$("#recClient").onchange = loadRecordings;

function projectOptions(client, current) {
  const cfg = STATE.settings.clients.find((c) => c.name === client);
  const names = (cfg?.projects || []).map((p) => p.name);
  if (current && !names.includes(current)) names.push(current);
  return `<option value="">${tr("no_project")}</option>` + names.map((n) => `<option ${n === current ? "selected" : ""}>${esc(n)}</option>`).join("") + `<option value="__new">${tr("new_project")}</option>`;
}

async function openRecording(id) {
  const r = await api("/api/recordings/" + encodeURIComponent(id));
  const d = $("#recDetail");
  d.classList.remove("hidden");
  $$("#recList .item").forEach((el) => el.classList.toggle("selected", el.dataset.id === id));
  const options = STATE.settings.clients.map((c) => `<option ${c.name === r.client ? "selected" : ""}>${esc(c.name)}</option>`).join("");
  d.innerHTML = `
    <div class="row wrap">
      <label class="inline">${tr("move_to")}</label>
      <select id="moveSel"><option value="">${tr("unsorted")}</option>${options}<option value="__new">${tr("add_client")}</option></select>
      <input id="moveNew" class="hidden" placeholder="${tr("new_client_ph")}">
      <select id="moveProj">${projectOptions(r.client, r.project)}</select>
      <input id="moveProjNew" class="hidden" placeholder="${tr("project")}">
      <button class="primary" id="moveBtn">OK</button>
    </div>
    <div id="suggestBox"></div>
    <div class="row wrap toolbar">
      <button id="followBtn" ${STATE.ai_ready ? "" : "disabled"}>✉ ${tr("followup_btn")}</button>
      <button id="speakBtn">👤 ${tr("speakers_btn")}</button>
      <button id="revealBtn">${tr("show_folder")}</button>
    </div>
    <div id="toolPanel"></div>
    <div class="md">${md(r.markdown)}</div>`;
  if (!r.client) $("#moveSel").value = "";
  const toggleInputs = () => {
    const c = $("#moveSel").value;
    $("#moveNew").classList.toggle("hidden", c !== "__new");
    $("#moveProj").classList.toggle("hidden", !c);
    $("#moveProjNew").classList.toggle("hidden", $("#moveProj").value !== "__new" || !c);
  };
  $("#moveSel").onchange = () => { $("#moveProj").innerHTML = projectOptions($("#moveSel").value, ""); toggleInputs(); };
  $("#moveProj").onchange = toggleInputs;
  toggleInputs();
  $("#moveBtn").onclick = async () => {
    const client = $("#moveSel").value === "__new" ? $("#moveNew").value.trim() : $("#moveSel").value;
    const project = !client ? "" : $("#moveProj").value === "__new" ? $("#moveProjNew").value.trim() : $("#moveProj").value;
    const res = await api(`/api/recordings/${encodeURIComponent(id)}/assign`, { method: "POST", body: { client, project } });
    toast(tr("moved"));
    await refreshState();
    await loadRecordings();
    await openRecording(id);
    if (res.suggestions && res.suggestions.length) showSuggestions(client, res.suggestions);
  };
  $("#revealBtn").onclick = () => api("/api/open-folder?path=" + encodeURIComponent(r.path), { method: "POST" });
  $("#followBtn").onclick = (e) => followUp(id, e.currentTarget);
  $("#speakBtn").onclick = () => speakersPanel(id, r.speakers);
}

function showSuggestions(client, words) {
  const box = $("#suggestBox");
  box.innerHTML = `<div class="card soft"><p>${esc(tr("suggest_title", { client }))}</p>
    <div class="tags">${words.map((w) => `<label class="tag pick"><input type="checkbox" checked value="${esc(w)}"> ${esc(w)}</label>`).join("")}</div>
    <div class="row end"><button class="ghost" id="sugNo">${tr("cancel")}</button><button class="primary" id="sugYes">${tr("add_kw")}</button></div></div>`;
  $("#sugNo").onclick = () => (box.innerHTML = "");
  $("#sugYes").onclick = async () => {
    const keywords = $$("input:checked", box).map((i) => i.value);
    await api(`/api/clients/${encodeURIComponent(client)}/keywords`, { method: "POST", body: { keywords } });
    await refreshState();
    box.innerHTML = "";
    toast(tr("saved"));
  };
}

function mailtoHref(to, subject, body) {
  const enc = encodeURIComponent;
  return `mailto:${to.join(",")}?subject=${enc(subject)}&body=${enc(body)}`;
}

async function followUp(id, btn) {
  busy(btn, true);
  try {
    const m = await api(`/api/recordings/${encodeURIComponent(id)}/followup`, { method: "POST" });
    $("#toolPanel").innerHTML = `<div class="card soft">
      <div class="small muted">${tr("to")}: ${esc(m.to.join(", ") || "-")}</div>
      <label>${tr("subject")}</label><input id="fuSubject" value="${esc(m.subject)}">
      <textarea id="fuBody" rows="12">${esc(m.body)}</textarea>
      <div class="row end"><button id="fuCopy">${tr("copy")}</button><a class="button primary" id="fuMail">${tr("mail_open")}</a></div>
      <div class="small muted">${tr("saved_at")}: ${esc(m.path)}</div></div>`;
    const update = () => ($("#fuMail").href = mailtoHref(m.to, $("#fuSubject").value, $("#fuBody").value));
    update();
    $("#fuSubject").oninput = $("#fuBody").oninput = update;
    $("#fuCopy").onclick = () => { navigator.clipboard?.writeText(`${$("#fuSubject").value}\n\n${$("#fuBody").value}`); toast("✓ " + tr("copy")); };
  } catch (e) {
    $("#toolPanel").innerHTML = `<div class="msg bad">${esc(e.message)}</div>`;
  }
  busy(btn, false);
}

function speakersPanel(id, speakers) {
  const panel = $("#toolPanel");
  panel.innerHTML = `<div class="card soft"><p class="small muted">${tr("speakers_help")}</p>
    ${speakers.map((s) => `<div class="row"><span class="speaker-label">${esc(s)}</span><input data-sp="${esc(s)}" placeholder="${esc(s)}"></div>`).join("") || `<p class="muted">–</p>`}
    <div class="row end">${STATE.ai_ready ? `<button id="spGuess">${tr("guess_btn")}</button>` : ""}<button class="primary" id="spSave">${tr("save_speakers")}</button></div></div>`;
  if ($("#spGuess")) $("#spGuess").onclick = async (e) => {
    const btn = e.currentTarget;
    busy(btn, true);
    try {
      const r = await api(`/api/recordings/${encodeURIComponent(id)}/speakers/guess`, { method: "POST" });
      $$("[data-sp]", panel).forEach((inp) => { if (r.mapping[inp.dataset.sp]) inp.value = r.mapping[inp.dataset.sp]; });
    } catch (err) { toast(err.message); }
    busy(btn, false);
  };
  $("#spSave").onclick = async () => {
    const mapping = {};
    $$("[data-sp]", panel).forEach((i) => { if (i.value.trim()) mapping[i.dataset.sp] = i.value.trim(); });
    await api(`/api/recordings/${encodeURIComponent(id)}/speakers`, { method: "POST", body: { mapping } });
    toast(tr("saved"));
    openRecording(id);
  };
}

// ---------------------------------------------------------------- action items

async function loadActions() {
  const sel = $("#actClient");
  const keep = sel.value;
  clientOptions(sel, { all: true, unsorted: true });
  sel.value = keep;
  const params = new URLSearchParams({ include_done: $("#actDone").checked });
  if (sel.value === "__unsorted") params.set("unsorted", "true");
  else if (sel.value) params.set("client", sel.value);
  const r = await api("/api/actions?" + params);
  const groups = {};
  r.actions.forEach((a) => (groups[a.client || tr("unsorted")] ||= []).push(a));
  $("#actList").innerHTML = r.actions.length
    ? Object.entries(groups).map(([client, items]) => `<h3>${esc(client)}</h3><ul class="actions">${items.map((a) => `
        <li class="${a.done ? "done" : ""}"><label><input type="checkbox" ${a.done ? "checked" : ""} data-pid="${esc(a.pocket_id)}" data-text="${esc(a.text)}"> <span>${esc(a.text)}</span></label>
        <a href="#recordings" class="small muted" data-open="${esc(a.pocket_id)}">${esc(a.title)} · ${esc(a.date)}</a></li>`).join("")}</ul>`).join("")
    : `<p class="muted">${tr("no_actions")}</p>`;
  $$("#actList input[type=checkbox]").forEach((cb) => (cb.onchange = async () => {
    await api("/api/actions", { method: "POST", body: { pocket_id: cb.dataset.pid, text: cb.dataset.text, done: cb.checked } });
    cb.closest("li").classList.toggle("done", cb.checked);
    toast(tr("saved"));
  }));
  $$("#actList [data-open]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); gotoRecording(a.dataset.open); }));
}

$("#actClient").onchange = loadActions;
$("#actDone").onchange = loadActions;

// ---------------------------------------------------------------- reports

function isoWeek(d = new Date()) {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - day);
  const year = t.getUTCFullYear();
  const week = Math.ceil(((t - Date.UTC(year, 0, 1)) / 86400000 + 1) / 7);
  return `${year}-W${String(week).padStart(2, "0")}`;
}

function renderReports() {
  $("#reportsAiNote").classList.toggle("hidden", STATE.ai_ready);
  $("#briefBtn").disabled = !STATE.ai_ready;
  clientOptions($("#briefClient"), { current: window._briefClient || $("#briefClient").value });
  if (!$("#weekPick").value) $("#weekPick").value = isoWeek();
  $("#weekAi").disabled = !STATE.ai_ready;
  if (!STATE.ai_ready) $("#weekAi").checked = false;
  if (window._briefClient) { window._briefClient = undefined; if (STATE.ai_ready) $("#briefBtn").click(); }
}

function showReport(r) {
  const out = $("#reportOut");
  out.classList.remove("hidden");
  out.innerHTML = `<div class="row between"><span class="small muted">${tr("saved_at")}: ${esc(r.path)}</span>
    <div class="row"><button id="repCopy">${tr("copy")}</button><button id="repOpen">${tr("show_folder")}</button></div></div><div class="md">${md(r.markdown)}</div>`;
  $("#repCopy").onclick = () => { navigator.clipboard?.writeText(r.markdown); toast("✓ " + tr("copy")); };
  $("#repOpen").onclick = () => api("/api/open-folder?path=" + encodeURIComponent(r.path), { method: "POST" });
  out.scrollIntoView({ behavior: "smooth", block: "start" });
}

$("#briefBtn").onclick = async (e) => {
  const btn = e.currentTarget;
  busy(btn, true);
  try { showReport(await api("/api/briefing", { method: "POST", body: { client: $("#briefClient").value } })); }
  catch (err) { toast(err.message); }
  busy(btn, false);
};

$("#weekBtn").onclick = async (e) => {
  const btn = e.currentTarget;
  busy(btn, true);
  try { showReport(await api("/api/weekly", { method: "POST", body: { week: $("#weekPick").value, ai: $("#weekAi").checked } })); }
  catch (err) { toast(err.message); }
  busy(btn, false);
};

// ---------------------------------------------------------------- ask

function renderAsk() {
  $("#askDisabled").classList.toggle("hidden", STATE.ai_ready);
  $("#askBtn").disabled = !STATE.ai_ready;
  const sel = $("#askClient");
  const cur = sel.value;
  clientOptions(sel, { all: true });
  sel.value = cur;
}

function addBubble(role, html) {
  const div = document.createElement("div");
  div.className = "bubble " + role;
  div.innerHTML = html;
  $("#chat").appendChild(div);
  div.scrollIntoView({ behavior: "smooth", block: "end" });
  return div;
}

function renderAnswer(blocks, sources) {
  // Text with numbered footnotes; hovering a number shows the quoted passage.
  let text = "";
  const used = new Map();
  blocks.forEach((b) => {
    text += b.text;
    const nums = [...new Set(b.citations.map((c) => c.source))];
    b.citations.forEach((c) => { if (!used.has(c.source)) used.set(c.source, []); used.get(c.source).push(c.cited_text); });
    if (nums.length) text += nums.map((n) => `⟦${n}⟧`).join("");
  });
  let html = md(text).replace(/⟦(\d+)⟧/g, (_, n) => {
    const quotes = used.get(+n) || [];
    return `<sup><a href="#recordings" class="cite" data-open="${esc(sources[+n]?.pocket_id || "")}" title="${esc(quotes.join(" … ").slice(0, 400))}">${+n + 1}</a></sup>`;
  });
  const list = [...used.keys()].sort((a, b) => a - b);
  if (list.length)
    html += `<ol class="sources">${list.map((n) => `<li value="${n + 1}"><a href="#recordings" data-open="${esc(sources[n]?.pocket_id || "")}">${esc(sources[n]?.title || "?")} (${esc(sources[n]?.date || "")})</a>
      <div class="quote">“${esc((used.get(n)[0] || "").trim().slice(0, 220))}”</div></li>`).join("")}</ol>`;
  else if (sources.length)
    html += `<div class="sources">${tr("sources")}: ${sources.slice(0, 6).map((s) => `<a href="#recordings" data-open="${esc(s.pocket_id)}">${esc(s.title)}</a>`).join(", ")}</div>`;
  return { html, plain: text.replace(/⟦\d+⟧/g, "") };
}

$("#askBtn").onclick = async () => {
  const q = $("#question").value.trim();
  if (!q) return;
  $("#question").value = "";
  addBubble("user", esc(q));
  const bubble = addBubble("assistant", `<span class="muted">${tr("thinking")}</span>`);
  $("#askBtn").disabled = true;
  let sources = [], skipped = 0, live = "";
  try {
    const res = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q, client: $("#askClient").value, history: chatHistory }) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 2);
        if (!line.startsWith("data: ")) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.type === "sources") { sources = ev.sources; skipped = ev.skipped; }
        else if (ev.type === "text") { live += ev.text; bubble.innerHTML = md(live) + `<span class="cursor">▍</span>`; }
        else if (ev.type === "error") throw new Error(ev.error);
        else if (ev.type === "done") {
          const { html, plain } = renderAnswer(ev.blocks, sources);
          bubble.innerHTML = html + (skipped ? `<div class="muted small">${tr("skipped", { n: skipped })}</div>` : "");
          chatHistory.push({ role: "user", content: q }, { role: "assistant", content: plain });
        }
      }
    }
    $$("[data-open]", bubble).forEach((a) => (a.onclick = (e) => { e.preventDefault(); gotoRecording(a.dataset.open); }));
  } catch (e) {
    bubble.innerHTML = `<span class="bad">${esc(e.message)}</span>`;
  }
  $("#askBtn").disabled = false;
};

$("#question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("#askBtn").click();
});
$("#askReset").onclick = () => { chatHistory = []; $("#chat").innerHTML = ""; };

// ---------------------------------------------------------------- settings

async function renderSettings() {
  const s = STATE.settings;
  $("#autoSync").checked = s.auto_sync;
  $("#interval").value = s.sync_interval_minutes;
  $("#since").value = s.sync_since;
  $("#keepRaw").checked = s.keep_raw_json;
  $("#minHits").value = s.keyword_min_hits;
  $("#aiClassify").checked = s.ai_classify;
  $("#aiCreate").checked = s.ai_may_create_clients;
  $("#aiStatus").checked = s.ai_client_status;
  $("#weeklyAuto").checked = s.weekly_auto;
  $("#semOn").checked = s.semantic_search;
  $("#autoStart").checked = STATE.autostart;
  $("#model").value = s.claude_model;
  $("#keysInfo").textContent = `Pocket: ${s.pocket_api_key_masked || "—"} · Anthropic: ${s.anthropic_api_key_masked || (s.anthropic_from_env ? "env" : "—")} · ${s.data_dir}`;
  $("#configPath").textContent = STATE.config_file;
  $("#calUrls").value = (await api("/api/calendar-urls")).urls.join("\n");
  renderSemantic();
}

async function renderSemantic() {
  const st = await api("/api/semantic").catch(() => null);
  if (st) $("#semStatus").textContent = tr("sem_status", { indexed: st.indexed, recordings: st.recordings, dl: st.model_downloaded ? tr("sem_dl_yes") : tr("sem_dl_no") });
  $("#semBuild").disabled = !STATE.settings.semantic_search || STATE.job_running;
  $("#semLog").classList.toggle("hidden", !STATE.job_running);
  $("#semLog").textContent = STATE.progress.join("\n");
}

function settingsBody() {
  return {
    auto_sync: $("#autoSync").checked,
    sync_interval_minutes: +$("#interval").value || 15,
    sync_since: $("#since").value,
    keep_raw_json: $("#keepRaw").checked,
    keyword_min_hits: +$("#minHits").value || 2,
    ai_classify: $("#aiClassify").checked,
    ai_may_create_clients: $("#aiCreate").checked,
    ai_client_status: $("#aiStatus").checked,
    weekly_auto: $("#weeklyAuto").checked,
    semantic_search: $("#semOn").checked,
    calendar_urls: $("#calUrls").value.split("\n").map((x) => x.trim()).filter(Boolean),
    claude_model: $("#model").value.trim(),
  };
}

$("#saveSettings").onclick = async () => {
  const wasSemantic = STATE.settings.semantic_search;
  await api("/api/settings", { method: "POST", body: settingsBody() });
  await refreshState();
  setMsg("#settingsMsg", tr("saved"));
  if (!wasSemantic && STATE.settings.semantic_search) $("#semBuild").click();
  renderSemantic();
};

$("#calTest").onclick = async () => {
  await api("/api/settings", { method: "POST", body: { calendar_urls: settingsBody().calendar_urls } });
  setMsg("#calMsg", tr("running"));
  const r = await api("/api/test-calendar", { method: "POST" });
  const bad = r.results.find((x) => !x.ok);
  if (!r.results.length) setMsg("#calMsg", "–", false);
  else if (bad) setMsg("#calMsg", tr("cal_bad") + bad.error, false);
  else setMsg("#calMsg", tr("cal_ok", { n: r.results.reduce((a, x) => a + x.events, 0) }));
};

$("#semBuild").onclick = async () => {
  await api("/api/semantic/build", { method: "POST" });
  await refreshState();
  renderSemantic();
  pollSync();
};

$("#autoStart").onchange = async () => {
  try {
    const r = await api("/api/autostart", { method: "POST", body: { enabled: $("#autoStart").checked } });
    $("#autoStart").checked = r.enabled;
    setMsg("#autoStartMsg", r.enabled ? tr("autostart_on") : tr("autostart_off"));
  } catch (e) { setMsg("#autoStartMsg", e.message, false); }
};

$("#rebuild").onclick = async () => {
  setMsg("#rebuildMsg", tr("running"));
  const r = await api("/api/rebuild", { method: "POST" });
  setMsg("#rebuildMsg", tr("rebuilt", r));
};

$$(".lang button").forEach(
  (b) =>
    (b.onclick = async () => {
      LANG = b.dataset.lang;
      await api("/api/settings", { method: "POST", body: { language: LANG } });
      await refreshState();
      applyLang();
      show(location.hash.slice(1) || "dashboard");
    })
);

// ---------------------------------------------------------------- boot

(async function boot() {
  await refreshState();
  applyLang();
  if (!location.hash) location.hash = STATE.pocket_ready ? "#dashboard" : "#setup";
  show(location.hash.slice(1));
})();
