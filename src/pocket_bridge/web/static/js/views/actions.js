// Actiepunten: every open action item from all conversations, grouped per client.
import { $, $$, api, esc, icon, t, tp, toast, errText, fmtDate } from "../core.js";

let showDone = false;

export async function render(root, param) {
  const cl = await api("/api/clients");
  const client = param || "";
  const qs = new URLSearchParams();
  if (client === "__unsorted") qs.set("unsorted", "true");
  else if (client) qs.set("client", client);
  if (showDone) qs.set("include_done", "true");
  const data = await api(`/api/actions?${qs}`);
  const groups = {};
  for (const a of data.actions) (groups[a.client || ""] ||= []).push(a);
  const open = data.actions.filter((a) => !a.done).length;
  root.innerHTML = `
    <div class="page-head"><div><h1>${esc(t("nav_actions"))}</h1><p class="lead">${esc(t("actions_help"))}</p></div></div>
    <div class="row" style="margin-bottom:16px">
      <label class="sr-only" for="acClient">${esc(t("filter_client"))}</label>
      <select id="acClient" style="max-width:280px"><option value="">${esc(t("all_clients"))}</option><option value="__unsorted" ${client === "__unsorted" ? "selected" : ""}>${esc(t("no_client"))}</option>
        ${cl.clients.map((c) => `<option ${c.name === client ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
      <label class="check" style="padding:0"><input type="checkbox" id="acDone" ${showDone ? "checked" : ""}> ${esc(t("show_done"))}</label>
      <span class="muted small" aria-live="polite">${esc(tp("open_actions", open))}</span>
    </div>
    ${!data.actions.length ? `<div class="card empty"><div class="bubble-icon">${icon("circle-check")}</div><h2>${esc(t("actions_empty_title"))}</h2><p>${esc(t("actions_empty"))}</p></div>` :
      Object.keys(groups).sort((a, b) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b))).map((g) => `
      <section class="card" style="margin-bottom:16px" aria-labelledby="ag-${esc(g || "none")}">
        <h2 id="ag-${esc(g || "none")}" style="font-size:1.125rem">${g ? `<a href="#/clients/${encodeURIComponent(g)}">${esc(g)}</a>` : esc(t("no_client"))}</h2>
        <ul class="actions-list">${groups[g].map((a, i) => {
          const id = `ac-${esc(g)}-${i}`.replace(/\s/g, "_");
          return `<li class="${a.done ? "done" : ""}"><input type="checkbox" id="${id}" ${a.done ? "checked" : ""} data-id="${esc(a.pocket_id)}" data-text="${esc(a.text)}">
            <span><label class="txt" for="${id}">${esc(a.text)}</label>
            <a class="src" href="#/conversations/${encodeURIComponent(a.pocket_id)}">${esc(a.title)} · ${esc(fmtDate(a.date, { time: false, weekday: false }))}</a></span></li>`;
        }).join("")}</ul>
      </section>`).join("")}`;
  $("#acClient", root).onchange = (e) => (location.hash = e.target.value ? `#/actions/${encodeURIComponent(e.target.value)}` : "#/actions");
  $("#acDone", root).onchange = (e) => { showDone = e.target.checked; render(root, param); };
  $$("input[data-id]", root).forEach((cb) => (cb.onchange = async () => {
    const li = cb.closest("li");
    li.classList.toggle("done", cb.checked);
    try {
      await api("/api/actions", { method: "POST", body: { pocket_id: cb.dataset.id, text: cb.dataset.text, done: cb.checked } });
      toast(cb.checked ? t("action_done") : t("action_reopened"), { action: { label: t("undo"), fn: async () => {
        await api("/api/actions", { method: "POST", body: { pocket_id: cb.dataset.id, text: cb.dataset.text, done: !cb.checked } });
        render(root, param);
      } } });
    } catch (e) {
      cb.checked = !cb.checked;
      li.classList.toggle("done", cb.checked);
      toast(errText(e), { type: "error" });
    }
  }));
}
