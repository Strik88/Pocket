// Klanten: overview cards, a client page (stand van zaken, briefing, gesprekken, actiepunten) and the editor.
import { $, $$, api, esc, icon, t, tp, toast, errText, run, fmtDate, fmtRelative, md, openDialog, confirmDialog, avatarClass, initials, announce } from "../core.js";
import { S, refresh } from "../state.js";
import { renderDiscover } from "./discover.js";
import { renderUnsorted } from "./unsorted.js";
import { recItem } from "./overview.js";

export async function render(root, param, ctx) {
  if (param === "discover") {
    root.innerHTML = `<a class="small" href="#/clients">${icon("arrow-left", "sm")} ${esc(t("nav_clients"))}</a><div class="card" style="margin-top:12px" id="discBox"></div>`;
    return renderDiscover($("#discBox", root), { onApplied: () => {} });
  }
  if (param === "unsorted") {
    root.innerHTML = `<a class="small" href="#/clients">${icon("arrow-left", "sm")} ${esc(t("nav_clients"))}</a><div style="margin-top:12px" id="uBox"></div>`;
    return renderUnsorted($("#uBox", root));
  }
  if (param) return renderClient(root, param, ctx);
  return renderList(root, ctx);
}

async function renderList(root, ctx) {
  const st = S.state;
  const data = await api("/api/clients");
  const clients = data.clients;
  const discoverBtn = `<a class="btn ai" href="#/clients/discover">${icon("sparkles")} ${esc(st.ai_ready ? t("disc_go_short") : t("disc_go_rules"))}</a>`;
  root.innerHTML = `
    <div class="page-head">
      <div><h1>${esc(t("nav_clients"))}</h1><p class="lead">${esc(t("clients_lead"))}</p></div>
      <div class="row">${discoverBtn}<button class="btn" id="addClient">${icon("plus")} ${esc(t("add_client"))}</button></div>
    </div>
    ${st.proposal ? `<div class="card tint-ai row between" style="margin-bottom:24px"><div class="row">${icon("sparkles")}<span>${esc(t("att_proposal", { n: st.proposal.clients }))}</span></div><a class="btn ai small" href="#/proposal">${esc(t("review"))}</a></div>` : ""}
    ${!clients.length ? `<div class="card empty"><div class="bubble-icon">${icon("building-2")}</div><h2>${esc(t("clients_empty_title"))}</h2>
      <p>${esc(t("clients_empty"))}</p><div class="row">${discoverBtn}<button class="btn" id="addClient2">${esc(t("add_client_self"))}</button></div></div>` : `
    <div class="grid cards">
      ${data.unsorted ? `<div class="card client-card unsorted"><div class="head"><span class="avatar c2">${icon("inbox")}</span><h3><a href="#/clients/unsorted">${esc(t("unsorted_title"))}</a></h3></div>
        <p class="status">${esc(tp("unsorted_card", data.unsorted))}</p><span class="meta"><span>${esc(t("sort_now"))} →</span></span></div>` : ""}
      ${clients.map(card).join("")}
    </div>`}`;
  const add = () => openEditor(null, async (name) => { await refresh(); ctx.go(`#/clients/${encodeURIComponent(name)}`); });
  $("#addClient", root).onclick = add;
  $("#addClient2", root)?.addEventListener("click", add);
}

function card(c) {
  const status = (c.status || "").replace(/^#+.*$/gm, "").replace(/[*_`>-]/g, "").trim();
  return `<article class="card client-card">
    <div class="head"><span class="avatar ${avatarClass(c.name)}" aria-hidden="true">${esc(initials(c.name))}</span>
      <h3><a href="#/clients/${encodeURIComponent(c.name)}">${esc(c.name)}</a></h3></div>
    <p class="status">${status ? esc(status.slice(0, 260)) : `<span class="muted">${esc(c.folder_only ? t("folder_only") : t("no_status_yet"))}</span>`}</p>
    <div class="meta"><span>${esc(tp("conv", c.recordings))}</span>${c.open_actions ? `<span>${esc(tp("open_actions", c.open_actions))}</span>` : ""}
      ${c.last_date ? `<span>${esc(t("last_contact", { d: fmtDate(c.last_date, { time: false, weekday: false }) }))}</span>` : ""}</div>
  </article>`;
}

// -- Client page ------------------------------------------------------------------------------

async function renderClient(root, name, ctx) {
  const st = S.state;
  const [data, recs, acts] = await Promise.all([
    api("/api/clients"),
    api(`/api/recordings?client=${encodeURIComponent(name)}&limit=200`),
    api(`/api/actions?client=${encodeURIComponent(name)}`),
  ]);
  const c = data.clients.find((x) => x.name.toLowerCase() === name.toLowerCase());
  if (!c) {
    root.innerHTML = `<div class="card empty"><h2>${esc(t("client_not_found"))}</h2><a class="btn" href="#/clients">${esc(t("nav_clients"))}</a></div>`;
    return;
  }
  document.title = `${c.name} · Pocket Bridge by Striks`;
  const byProject = {};
  for (const r of recs.recordings) (byProject[r.project || ""] ||= []).push(r);
  const projects = Object.keys(byProject).sort((a, b) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b)));

  root.innerHTML = `
    <a class="small" href="#/clients">${icon("arrow-left", "sm")} ${esc(t("nav_clients"))}</a>
    <div class="page-head" style="margin-top:12px">
      <div class="row" style="align-items:center;gap:16px">
        <span class="avatar ${avatarClass(c.name)}" style="width:56px;height:56px;font-size:1.25rem" aria-hidden="true">${esc(initials(c.name))}</span>
        <div><h1 style="margin:0">${esc(c.name)}</h1>
        <p class="muted" style="margin:0">${esc(tp("conv", c.recordings))}${c.open_actions ? " · " + esc(tp("open_actions", c.open_actions)) : ""}${c.last_date ? " · " + esc(t("last_contact", { d: fmtDate(c.last_date, { time: false, weekday: false }) })) : ""}</p></div>
      </div>
      <div class="row">
        <button class="btn ai" id="briefBtn" ${st.ai_ready || st.settings.demo_mode ? "" : 'aria-disabled="true" aria-describedby="briefWhy"'}>${icon("sparkles")} ${esc(t("briefing_btn"))}</button>
        <button class="btn" id="editBtn">${icon("pencil")} ${esc(t("edit"))}</button>
      </div>
    </div>
    ${st.ai_ready || st.settings.demo_mode ? "" : `<p class="help" id="briefWhy">${esc(t("needs_claude"))} <a href="#/settings/connections">${esc(t("connect_claude"))}</a></p>`}
    <div id="briefOut"></div>
    <div class="grid two section" style="margin-top:8px">
      <section class="card ai-made" aria-labelledby="stH">
        <div class="card-head"><div><div class="ai-label">${icon("sparkles", "sm")} ${esc(t("by_claude"))}</div><h2 id="stH" style="font-size:1.25rem">${esc(t("status_title"))}</h2></div>
          ${st.ai_ready ? `<button class="btn small" id="stRefresh">${icon("refresh-cw", "sm")} ${esc(t("refresh"))}</button>` : ""}</div>
        <div id="stBody" class="md">${c.status ? md(c.status, { headingStart: 3 }) : `<p class="muted">${esc(st.ai_ready ? t("status_empty") : t("status_nokey"))}</p>`}</div>
        ${c.status_updated ? `<p class="help">${esc(t("updated_at", { when: fmtRelative(c.status_updated.replace(" ", "T")) }))}</p>` : ""}
      </section>
      <section class="card" aria-labelledby="acH">
        <div class="card-head"><h2 id="acH" style="font-size:1.25rem">${esc(t("nav_actions"))}</h2><a href="#/actions/${encodeURIComponent(c.name)}">${esc(t("all"))}</a></div>
        ${acts.actions.length ? `<ul class="actions-list">${acts.actions.slice(0, 8).map((a) => `<li><span class="txt">${esc(a.text)}<span class="src">${esc(a.title)} · ${esc(fmtDate(a.date, { time: false, weekday: false }))}</span></span></li>`).join("")}</ul>`
          : `<p class="muted">${esc(t("no_open_actions"))}</p>`}
      </section>
    </div>
    <section class="section" aria-labelledby="cvH">
      <h2 id="cvH" style="font-size:1.25rem">${esc(t("nav_conversations"))}</h2>
      ${projects.map((p) => `<div class="card" style="margin-bottom:16px;padding:12px 16px">
        ${projects.length > 1 || p ? `<h3 style="font-size:1rem;margin:8px 8px 4px">${icon("briefcase", "sm")} ${esc(p || t("no_project"))}</h3>` : ""}
        <ul class="list">${byProject[p].map(recItem).join("")}</ul></div>`).join("") || `<p class="muted">${esc(t("empty_conversations_client"))}</p>`}
    </section>`;

  $("#editBtn", root).onclick = () => openEditor(c, async (newName) => {
    await refresh();
    if (newName === null) return ctx.go("#/clients");
    ctx.go(`#/clients/${encodeURIComponent(newName)}`);
  });
  $("#stRefresh", root)?.addEventListener("click", (e) => run(e.currentTarget, async () => {
    const r = await api(`/api/clients/${encodeURIComponent(c.name)}/status`, { method: "POST" });
    $("#stBody", root).innerHTML = md(r.status || "", { headingStart: 3 });
    toast(t("status_updated"));
  }, { busy: t("claude_busy") }));
  $("#briefBtn", root).onclick = (e) => {
    if (e.currentTarget.getAttribute("aria-disabled") === "true") { toast(t("needs_claude"), { type: "error" }); return; }
    const out = $("#briefOut", root);
    out.innerHTML = `<div class="card ai-made" style="margin-top:16px"><div class="ai-label">${icon("sparkles", "sm")} ${esc(t("briefing_btn"))}</div>
      <p class="muted">${esc(t("briefing_wait"))}</p><div class="skel" style="width:80%"></div><div class="skel" style="width:65%"></div><div class="skel" style="width:72%"></div></div>`;
    run(e.currentTarget, async () => {
      const r = await api("/api/briefing", { method: "POST", body: { client: c.name } });
      out.innerHTML = `<div class="card ai-made" style="margin-top:16px">
        <div class="card-head"><div class="ai-label">${icon("sparkles", "sm")} ${esc(t("briefing_for", { c: c.name }))}</div>
        <div class="row"><button class="btn small" id="bCopy">${icon("copy", "sm")} ${esc(t("copy"))}</button><button class="btn small ghost" id="bClose" aria-label="${esc(t("close"))}">${icon("x", "sm")}</button></div></div>
        <div class="md prose">${md(r.markdown)}</div>
        ${r.example ? `<p class="help">${esc(t("example_result"))}</p>` : r.path ? `<p class="help">${esc(t("saved_in_dossier"))}</p>` : ""}</div>`;
      $("#bCopy", out).onclick = () => navigator.clipboard.writeText(r.markdown).then(() => toast(t("copied")));
      $("#bClose", out).onclick = () => (out.innerHTML = "");
      announce(t("briefing_ready"));
    }, { busy: t("claude_busy") }).then(() => { if (out.querySelector(".skel")) out.innerHTML = ""; });
  };
}

// -- Editor (drawer) ------------------------------------------------------------------------------

function chipInput(id, values, { prefix = "", placeholder = "" } = {}) {
  return `<div class="chip-input" data-chips="${id}">
    ${values.map((v) => `<span class="chip">${esc(prefix + v)}<button type="button" aria-label="${esc(t("remove_x", { x: v }))}" data-v="${esc(v)}">${icon("x", "sm")}</button></span>`).join("")}
    <input id="${id}" placeholder="${esc(placeholder)}" autocomplete="off"></div>`;
}

function bindChips(root, id, values, prefix = "") {
  const box = $(`[data-chips="${id}"]`, root);
  const input = $(`#${id}`, root);
  const redraw = () => {
    $$(".chip", box).forEach((c) => c.remove());
    for (const v of values) {
      const span = document.createElement("span");
      span.className = "chip";
      span.innerHTML = `${esc(prefix + v)}<button type="button" aria-label="${esc(t("remove_x", { x: v }))}">${icon("x", "sm")}</button>`;
      $("button", span).onclick = () => { values.splice(values.indexOf(v), 1); redraw(); input.focus(); };
      box.insertBefore(span, input);
    }
  };
  const add = () => {
    for (const part of input.value.split(",")) {
      const v = part.trim().replace(/^@/, "");
      if (v && !values.some((x) => x.toLowerCase() === v.toLowerCase())) values.push(v);
    }
    input.value = "";
    redraw();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); }
    if (e.key === "Backspace" && !input.value && values.length) { values.pop(); redraw(); }
  });
  input.addEventListener("blur", add);
  redraw();
  return add;
}

export function openEditor(client, onSaved) {
  const c = client ? JSON.parse(JSON.stringify(client)) : { name: "", keywords: [], email_domains: [], pocket_tags: [], projects: [], notes: "" };
  const isNew = !client;
  const { dialog, close } = openDialog(`
    <form id="edForm" style="display:contents" novalidate>
    <div class="d-body">
      <div class="row between"><h2 style="margin:0">${esc(isNew ? t("new_client") : t("edit_client", { name: client.name }))}</h2>
        <button type="button" class="btn ghost small" data-close aria-label="${esc(t("close"))}">${icon("x")}</button></div>
      <div class="field" style="margin-top:20px"><label for="edName">${esc(t("name"))}</label><input id="edName" value="${esc(c.name)}" required></div>
      <h3 style="font-size:1rem;margin-top:24px">${esc(t("recognition"))}</h3>
      <p class="help" style="margin-bottom:12px">${esc(t("recognition_help"))}</p>
      <div class="field"><label for="edKw">${esc(t("keywords"))}</label>${chipInput("edKw", [], { placeholder: t("keywords_ph") })}<p class="help">${esc(t("keywords_help"))}</p></div>
      <div class="field"><label for="edDom">${esc(t("email_domains"))}</label>${chipInput("edDom", [], { placeholder: "acme.nl" })}<p class="help">${esc(t("email_domains_help"))}</p></div>
      <div class="field"><label for="edTags">${esc(t("pocket_tags"))}</label>${chipInput("edTags", [])}<p class="help">${esc(t("pocket_tags_help"))}</p></div>
      <h3 style="font-size:1rem;margin-top:24px">${esc(t("projects"))}</h3>
      <p class="help" style="margin-bottom:8px">${esc(t("projects_help"))}</p>
      <div id="edProjects" class="stack" style="gap:8px"></div>
      <button type="button" class="btn small" id="edAddProj" style="margin-top:8px">${icon("plus", "sm")} ${esc(t("add_project"))}</button>
      <div class="field" style="margin-top:24px"><label for="edNotes">${esc(t("notes"))}</label><textarea id="edNotes">${esc(c.notes || "")}</textarea><p class="help">${esc(t("notes_help"))}</p></div>
      ${isNew || client.folder_only ? "" : `<div style="margin-top:32px;padding-top:16px;border-top:1px solid var(--border)"><button type="button" class="btn danger" id="edDelete">${icon("trash-2")} ${esc(t("delete_client"))}</button></div>`}
    </div>
    <div class="d-foot"><button type="button" class="btn" data-close>${esc(t("cancel"))}</button><button type="submit" class="btn primary" id="edSave">${esc(t("save"))}</button></div>
    </form>`, { drawer: true, label: isNew ? t("new_client") : t("edit_client", { name: c.name }) });

  const flush = [bindChips(dialog, "edKw", c.keywords), bindChips(dialog, "edDom", c.email_domains, "@"), bindChips(dialog, "edTags", c.pocket_tags)];
  const projBox = $("#edProjects", dialog);
  const drawProjects = () => {
    projBox.innerHTML = c.projects.map((p, i) => `
      <div class="row" style="gap:8px;flex-wrap:nowrap">
        <label class="sr-only" for="pj${i}">${esc(t("project_name"))}</label><input id="pj${i}" data-pi="${i}" data-f="name" value="${esc(p.name)}" placeholder="${esc(t("project_name"))}">
        <label class="sr-only" for="pk${i}">${esc(t("keywords"))}</label><input id="pk${i}" data-pi="${i}" data-f="kw" value="${esc((p.keywords || []).join(", "))}" placeholder="${esc(t("keywords"))}">
        <button type="button" class="btn ghost small" data-rmp="${i}" aria-label="${esc(t("remove_x", { x: p.name || t("project") }))}">${icon("x")}</button>
      </div>`).join("");
    $$("[data-pi]", projBox).forEach((inp) => (inp.oninput = () => {
      const p = c.projects[+inp.dataset.pi];
      if (inp.dataset.f === "name") p.name = inp.value;
      else p.keywords = inp.value.split(",").map((s) => s.trim()).filter(Boolean);
    }));
    $$("[data-rmp]", projBox).forEach((b) => (b.onclick = () => { c.projects.splice(+b.dataset.rmp, 1); drawProjects(); }));
  };
  drawProjects();
  $("#edAddProj", dialog).onclick = () => { c.projects.push({ name: "", keywords: [] }); drawProjects(); $(`#pj${c.projects.length - 1}`, dialog).focus(); };
  $("#edName", dialog).focus();

  $("#edForm", dialog).onsubmit = (e) => {
    e.preventDefault();
    flush.forEach((f) => f());
    c.name = $("#edName", dialog).value.trim();
    c.notes = $("#edNotes", dialog).value;
    if (!c.name) { toast(t("err_bad_name"), { type: "error" }); $("#edName", dialog).focus(); return; }
    run($("#edSave", dialog), async () => {
      const body = { name: c.name, keywords: c.keywords, email_domains: c.email_domains, pocket_tags: c.pocket_tags,
        projects: c.projects.filter((p) => p.name.trim()), notes: c.notes };
      const q = isNew ? "" : `?original_name=${encodeURIComponent(client.name)}`;
      await api(`/api/clients${q}`, { method: "POST", body });
      close();
      toast(t("saved"));
      onSaved?.(c.name);
    });
  };
  $("#edDelete", dialog)?.addEventListener("click", async () => {
    const ok = await confirmDialog({ title: t("delete_q", { name: client.name }), body: tp("delete_body", client.recordings || 0, { name: client.name }), ok: t("delete"), danger: true });
    if (!ok) return;
    try {
      const r = await api(`/api/clients/${encodeURIComponent(client.name)}`, { method: "DELETE" });
      close();
      toast(t("deleted", { name: client.name }), { action: r.removed ? { label: t("undo"), fn: async () => {
        await api("/api/clients", { method: "POST", body: r.removed });
        await refresh();
        toast(t("restored"));
        window.pb.render();
      } } : null });
      onSaved?.(null);
    } catch (e) { toast(errText(e), { type: "error" }); }
  });
}
