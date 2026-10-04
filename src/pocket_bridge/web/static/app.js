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
      const box = li[1] ? `<input type="checkbox" disabled ${li[1].includes("x") || li[1].includes("X") ? "checked" : ""}> ` : "";
      html += `<li>${box}${inline(li[2])}</li>`;
      return;
    }
    if (inList) { html += "</ul>"; inList = false; }
    const h = line.match(/^(#{1,4}) (.*)$/);
    if (h) html += `<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`;
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

function show(view) {
  if (!$("#view-" + view)) view = "dashboard";
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + view));
  $$(".sidebar a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  ({ dashboard: renderDashboard, clients: loadClients, recordings: loadRecordings, ask: renderAsk, settings: renderSettings, setup: renderSetup }[view] || (() => {}))();
}

window.addEventListener("hashchange", () => show(location.hash.slice(1)));

// ---------------------------------------------------------------- state

async function refreshState() {
  STATE = await api("/api/state");
  LANG = STATE.settings.language || "nl";
  $("#version").textContent = "v" + STATE.version;
  return STATE;
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
  const done = {
    1: STATE.pocket_ready,
    2: !!s.data_dir,
    3: s.clients.length > 0,
    4: STATE.ai_ready,
    5: STATE.claude_desktop_connected,
  };
  $$(".step").forEach((el) => el.classList.toggle("done", !!done[el.dataset.step]));
  renderSetupClients();
}

function renderSetupClients() {
  const box = $("#setupClients");
  box.innerHTML = STATE.settings.clients
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
    toast("✓ copied");
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
  if (STATE.sync_running) pollSync();
  api("/api/recordings?limit=8").then((r) => ($("#recentList").innerHTML = recItems(r.recordings)));
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
    if (STATE.sync_running) pollSync();
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
        <div class="tags">${c.folder_only ? `<span class="tag">${tr("folder_only")}</span>` : ""}${c.keywords.map((k) => `<span class="tag">${esc(k)}</span>`).join("")}${c.pocket_tags.map((k) => `<span class="tag pt">#${esc(k)}</span>`).join("")}</div>
        <div class="row between card-foot"><a href="#recordings" data-filter="${esc(c.name)}">${tr("nav_recordings")} →</a>
          <div class="row">${c.folder_only ? "" : `<button data-edit="${i}">${tr("edit")}</button>`}<button data-del="${esc(c.name)}" class="ghost">${tr("delete")}</button></div></div>
      </div>`
    ).join("") + (r.unsorted ? `<div class="card client"><h3>${tr("unsorted")}</h3><div class="muted small">${r.unsorted} ${tr("conversations")}</div><div class="row between card-foot"><a href="#recordings" data-filter="__unsorted">${tr("nav_recordings")} →</a></div></div>` : "");
  $$("[data-edit]").forEach((b) => (b.onclick = () => openClientForm(CLIENTS[+b.dataset.edit])));
  $$("[data-del]").forEach((b) => (b.onclick = async () => {
    if (!confirm(tr("confirm_delete", { name: b.dataset.del }))) return;
    await api("/api/clients/" + encodeURIComponent(b.dataset.del), { method: "DELETE" });
    await refreshState();
    loadClients();
  }));
  $$("[data-filter]").forEach((a) => (a.onclick = () => (window._recFilter = a.dataset.filter)));
}

function openClientForm(c = null) {
  const f = $("#clientForm");
  f.classList.remove("hidden");
  f.innerHTML = `
    <label>${tr("name")}</label><input id="cfName" value="${esc(c?.name || "")}">
    <label>${tr("keywords")}</label><input id="cfKeywords" value="${esc((c?.keywords || []).join(", "))}" placeholder="Acme, Jan de Vries, Project Phoenix">
    <label>${tr("pocket_tags")}</label><input id="cfTags" value="${esc((c?.pocket_tags || []).join(", "))}">
    <label>${tr("notes")}</label><textarea id="cfNotes" rows="2">${esc(c?.notes || "")}</textarea>
    <div class="row end"><button id="cfCancel">${tr("cancel")}</button><button class="primary" id="cfSave">${tr("save")}</button></div>`;
  $("#cfName").focus();
  $("#cfCancel").onclick = () => f.classList.add("hidden");
  $("#cfSave").onclick = async () => {
    const split = (v) => v.split(",").map((x) => x.trim()).filter(Boolean);
    const body = { name: $("#cfName").value.trim(), keywords: split($("#cfKeywords").value), pocket_tags: split($("#cfTags").value), notes: $("#cfNotes").value.trim() };
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
        <div class="muted small">${esc((r.date || "").slice(0, 16).replace("T", " "))} · ${esc(r.client || tr("unsorted"))}${r.open_actions ? " · " + r.open_actions + " ☐" : ""}</div>
        ${r.snippet ? `<div class="snippet">${md(r.snippet)}</div>` : ""}
      </div>`
    )
    .join("");
}

function fillClientSelect(sel, withAll, current = "") {
  const names = STATE.settings.clients.map((c) => c.name);
  sel.innerHTML =
    (withAll ? `<option value="">${tr("all_clients")}</option><option value="__unsorted">${tr("unsorted")}</option>` : "") +
    names.map((n) => `<option ${n === current ? "selected" : ""}>${esc(n)}</option>`).join("");
}

let searchTimer;
async function loadRecordings() {
  const sel = $("#recClient");
  const keep = window._recFilter ?? sel.value;
  window._recFilter = undefined;
  fillClientSelect(sel, true);
  sel.value = keep || "";
  const q = $("#search").value.trim();
  const params = new URLSearchParams({ q, limit: 300 });
  if (sel.value === "__unsorted") params.set("unsorted", "true");
  else if (sel.value) params.set("client", sel.value);
  const r = await api("/api/recordings?" + params);
  $("#recList").innerHTML = recItems(r.recordings);
  $$("#recList .item").forEach((el) => (el.onclick = () => openRecording(el.dataset.id)));
}

$("#search").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadRecordings, 250); };
$("#recClient").onchange = loadRecordings;

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
      <button class="primary" id="moveBtn">OK</button>
      <button id="revealBtn">${tr("show_folder")}</button>
    </div>
    <div class="md">${md(r.markdown)}</div>`;
  if (!r.client) $("#moveSel").value = "";
  $("#moveSel").onchange = () => $("#moveNew").classList.toggle("hidden", $("#moveSel").value !== "__new");
  $("#moveBtn").onclick = async () => {
    const client = $("#moveSel").value === "__new" ? $("#moveNew").value.trim() : $("#moveSel").value;
    await api(`/api/recordings/${encodeURIComponent(id)}/assign`, { method: "POST", body: { client } });
    toast(tr("moved"));
    await refreshState();
    await loadRecordings();
    openRecording(id);
  };
  $("#revealBtn").onclick = () => api("/api/open-folder?path=" + encodeURIComponent(r.path), { method: "POST" });
}

// ---------------------------------------------------------------- ask

function renderAsk() {
  $("#askDisabled").classList.toggle("hidden", STATE.ai_ready);
  $("#askBtn").disabled = !STATE.ai_ready;
  const sel = $("#askClient");
  const cur = sel.value;
  sel.innerHTML = `<option value="">${tr("all_clients")}</option>` + STATE.settings.clients.map((c) => `<option>${esc(c.name)}</option>`).join("");
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

$("#askBtn").onclick = async () => {
  const q = $("#question").value.trim();
  if (!q) return;
  $("#question").value = "";
  addBubble("user", esc(q));
  const wait = addBubble("assistant", `<span class="muted">${tr("thinking")}</span>`);
  $("#askBtn").disabled = true;
  try {
    const r = await api("/api/ask", { method: "POST", body: { question: q, client: $("#askClient").value, history: chatHistory } });
    const src = r.sources.length
      ? `<div class="sources">${tr("sources")}: ${r.sources.map((s) => `<a href="#recordings" data-open="${esc(s.pocket_id)}">${esc(s.title)} (${esc(s.date)})</a>`).join(", ")}</div>`
      : "";
    wait.innerHTML = md(r.answer) + src + (r.skipped ? `<div class="muted small">${tr("skipped", { n: r.skipped })}</div>` : "");
    $$("[data-open]", wait).forEach((a) => (a.onclick = () => setTimeout(() => openRecording(a.dataset.open), 200)));
    chatHistory.push({ role: "user", content: q }, { role: "assistant", content: r.answer });
  } catch (e) {
    wait.innerHTML = `<span class="bad">${esc(e.message)}</span>`;
  }
  $("#askBtn").disabled = false;
};

$("#question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("#askBtn").click();
});
$("#askReset").onclick = () => { chatHistory = []; $("#chat").innerHTML = ""; };

// ---------------------------------------------------------------- settings

function renderSettings() {
  const s = STATE.settings;
  $("#autoSync").checked = s.auto_sync;
  $("#interval").value = s.sync_interval_minutes;
  $("#since").value = s.sync_since;
  $("#keepRaw").checked = s.keep_raw_json;
  $("#minHits").value = s.keyword_min_hits;
  $("#aiClassify").checked = s.ai_classify;
  $("#aiCreate").checked = s.ai_may_create_clients;
  $("#model").value = s.claude_model;
  $("#keysInfo").textContent = `Pocket: ${s.pocket_api_key_masked || "—"} · Anthropic: ${s.anthropic_api_key_masked || (s.anthropic_from_env ? "env" : "—")} · ${s.data_dir}`;
  $("#configPath").textContent = STATE.config_file;
}

$("#saveSettings").onclick = async () => {
  await api("/api/settings", {
    method: "POST",
    body: {
      auto_sync: $("#autoSync").checked,
      sync_interval_minutes: +$("#interval").value || 15,
      sync_since: $("#since").value,
      keep_raw_json: $("#keepRaw").checked,
      keyword_min_hits: +$("#minHits").value || 2,
      ai_classify: $("#aiClassify").checked,
      ai_may_create_clients: $("#aiCreate").checked,
      claude_model: $("#model").value.trim(),
    },
  });
  await refreshState();
  setMsg("#settingsMsg", tr("saved"));
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
