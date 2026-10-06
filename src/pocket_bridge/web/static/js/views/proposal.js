// Review of a client proposal: confirm, rename, merge, drop. Nothing changes until "Toevoegen en indelen".
import { $, $$, api, esc, icon, t, tp, toast, errText, run, fmtDate, fmtNum, fmtEur, confirmDialog, announce } from "../core.js";
import { S, refresh } from "../state.js";

const CONF = { high: "conf_high", medium: "conf_medium", low: "conf_low" };

/** Route #/proposal: the standalone review page (reached from Klanten or Overzicht). */
export async function render(root, _param, ctx) {
  const p = (await api("/api/discover/proposal")).proposal;
  if (!p) {
    root.innerHTML = `<div class="card empty"><div class="bubble-icon">${icon("wand-sparkles")}</div><h2>${esc(t("prop_none"))}</h2>
      <p>${esc(t("prop_none_help"))}</p><a class="btn ai" href="#/clients/discover">${icon("sparkles")} ${esc(t("disc_go_short"))}</a></div>`;
    return;
  }
  renderReview(root, p, { onApplied: () => {}, onDismissed: () => ctx.go("#/clients") });
}

export function renderReview(root, proposal, { onboarding = false, onApplied, onDismissed }) {
  const recs = proposal.recordings || {};
  const items = proposal.clients.map((c, i) => ({
    ...c,
    idx: i,
    accept: c.confidence !== "low" || !!c.existing_client,
    mergeInto: "",
    keywords: [...(c.keywords || [])],
    email_domains: [...(c.email_domains || [])],
    projects: (c.projects || []).map((p) => ({ ...p })),
  }));
  const ignore = {};
  for (const o of proposal.other || []) if (o.kind === "personal" || o.kind === "internal") for (const id of o.recording_ids) ignore[id] = o.kind;
  const label = proposal.source === "rules" ? t("prop_rules") : proposal.source === "example" ? t("prop_example") : t("prop_claude");
  const newCount = items.filter((c) => !c.existing_client).length;

  root.innerHTML = `
    <div class="ai-head" style="margin-bottom:8px">
      <span class="ai-orb">${icon(proposal.source === "rules" ? "wand-sparkles" : "sparkles")}</span>
      <div>
        <p class="eyebrow" style="margin:0">${esc(label)}</p>
        <h1 style="margin:0 0 4px">${esc(tp("prop_title", newCount))}</h1>
        <p class="lead" style="margin:0">${esc(tp("prop_lead", proposal.counts.recordings))}</p>
      </div>
    </div>
    ${proposal.source === "example" ? `<div class="msg warn">${icon("info")}<p>${esc(t("prop_example_note"))}</p></div>` : ""}
    ${(proposal.warnings || []).map((w) => `<div class="msg warn">${icon("info")}<p>${esc(t("prop_warn_" + w.code, { name: w.name || "" }))}</p></div>`).join("")}
    ${proposal.usage ? `<p class="help">${esc(proposal.usage.eur < 0.01 ? t("prop_cost_small") : t("prop_cost", { eur: fmtEur(proposal.usage.eur) }))}</p>` : ""}
    <form id="propForm" class="stack" style="margin-top:20px" novalidate>
      <div class="stack" id="propList"></div>
      <div id="otherList" class="stack"></div>
      <div class="sticky-foot" style="margin-left:0;margin-right:0;border-radius:var(--r-lg)">
        <span id="propSummary" class="muted" aria-live="polite"></span><span class="spacer"></span>
        <button type="button" class="btn ghost" id="propDismiss">${esc(t("prop_dismiss"))}</button>
        <button type="submit" class="btn cta" id="propApply">${icon("check")} ${esc(t("prop_apply"))}</button>
      </div>
    </form>`;

  const list = $("#propList", root);

  const recLine = (id) => {
    const r = recs[id] || {};
    return `<li><time>${esc(fmtDate(r.date, { time: false, weekday: false }))}</time><span>${esc(r.title || id)}${r.meeting && r.meeting !== r.title ? ` <span class="muted">· ${esc(r.meeting)}</span>` : ""}</span></li>`;
  };

  function drawItem(c) {
    const el = document.createElement("div");
    el.className = "proposal" + (c.accept ? "" : " off");
    el.dataset.idx = c.idx;
    const nameId = `pn${c.idx}`;
    const mergeOpts = [...new Set([...(window.__clientNames || []), ...items.filter((x) => x.idx !== c.idx && !x.mergeInto).map((x) => x.name)])];
    el.innerHTML = `
      <input type="checkbox" ${c.accept ? "checked" : ""} aria-label="${esc(t("prop_accept", { name: c.name }))}" data-k="accept">
      <div class="pbody">
        <label class="sr-only" for="${nameId}">${esc(t("prop_name"))}</label>
        <input id="${nameId}" class="name-input" value="${esc(c.name)}" data-k="name" ${c.existing_client ? "readonly" : ""}>
        <div class="badges">
          ${c.existing_client ? `<span class="badge plain">${esc(t("prop_existing", { name: c.existing_client }))}</span>` : `<span class="badge ai">${esc(t("prop_new"))}</span>`}
          <span class="badge plain">${esc(t(CONF[c.confidence] || "conf_low"))}</span>
          ${c.relationship && c.relationship !== "client" ? `<span class="badge human">${esc(t("rel_" + c.relationship))}</span>` : ""}
        </div>
        ${c.reason ? `<p class="why">${esc(c.reason)}</p>` : ""}
        ${c.similar_to ? `<div class="msg warn" style="margin:0 0 10px">${icon("git-merge")}<p>${esc(t("prop_similar", { name: c.similar_to }))} <button type="button" class="link-btn" data-merge-to="${esc(c.similar_to)}">${esc(t("prop_merge_with", { name: c.similar_to }))}</button></p></div>` : ""}
        <dl class="facts">
          <dt>${esc(t("prop_conversations"))}</dt>
          <dd><details><summary>${esc(tp("conv", c.recording_ids.length))}</summary><ul class="recs">${c.recording_ids.map(recLine).join("")}</ul></details></dd>
          ${c.keywords.length ? `<dt>${esc(t("prop_keywords"))}</dt><dd><div class="chips">${c.keywords.map((k, i) => `<span class="chip">${esc(k)}<button type="button" data-rm="keywords" data-i="${i}" aria-label="${esc(t("remove_x", { x: k }))}">${icon("x", "sm")}</button></span>`).join("")}</div></dd>` : ""}
          ${c.email_domains.length ? `<dt>${esc(t("prop_domains"))}</dt><dd><div class="chips">${c.email_domains.map((k, i) => `<span class="chip">@${esc(k)}<button type="button" data-rm="email_domains" data-i="${i}" aria-label="${esc(t("remove_x", { x: k }))}">${icon("x", "sm")}</button></span>`).join("")}</div></dd>` : ""}
          ${c.projects.length ? `<dt>${esc(t("prop_projects"))}</dt><dd><div class="chips">${c.projects.map((p, i) => `<span class="chip">${icon("briefcase", "sm")} ${esc(p.name)} · ${fmtNum(p.recording_ids.length)}<button type="button" data-rm="projects" data-i="${i}" aria-label="${esc(t("remove_x", { x: p.name }))}">${icon("x", "sm")}</button></span>`).join("")}</div></dd>` : ""}
          ${c.existing_client ? "" : `<dt><label for="pm${c.idx}">${esc(t("prop_merge"))}</label></dt>
          <dd><select id="pm${c.idx}" data-k="mergeInto" class="input-mid" style="min-height:40px"><option value="">${esc(t("prop_merge_none"))}</option>
            ${mergeOpts.filter((n) => n !== c.name).map((n) => `<option ${c.mergeInto === n ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></dd>`}
        </dl>
      </div>`;
    el.addEventListener("change", (e) => {
      const k = e.target.dataset.k;
      if (k === "accept") { c.accept = e.target.checked; el.classList.toggle("off", !c.accept); }
      if (k === "mergeInto") { c.mergeInto = e.target.value; if (c.mergeInto) { c.accept = true; } }
      summary();
    });
    el.addEventListener("input", (e) => { if (e.target.dataset.k === "name") { c.name = e.target.value; summary(); } });
    el.addEventListener("click", (e) => {
      const rm = e.target.closest("[data-rm]");
      if (rm) {
        c[rm.dataset.rm].splice(+rm.dataset.i, 1);
        const fresh = drawItem(c);
        el.replaceWith(fresh);
        $(`[data-rm]`, fresh)?.focus();
      }
      const mt = e.target.closest("[data-merge-to]");
      if (mt) {
        c.mergeInto = mt.dataset.mergeTo;
        c.accept = true;
        const fresh = drawItem(c);
        el.replaceWith(fresh);
        summary();
      }
    });
    return el;
  }

  // Existing client names for the merge menu
  api("/api/clients").then((r) => {
    window.__clientNames = r.clients.filter((c) => !c.folder_only).map((c) => c.name);
    list.innerHTML = "";
    items.forEach((c) => list.append(drawItem(c)));
    summary();
  }).catch(() => { items.forEach((c) => list.append(drawItem(c))); summary(); });

  // Not a client
  const other = $("#otherList", root);
  const groups = proposal.other || [];
  if (groups.length) {
    other.innerHTML = `<h2 style="margin-top:24px;font-size:1.25rem">${esc(t("prop_other_title"))}</h2><p class="help">${esc(t("prop_other_help"))}</p>` +
      groups.map((g) => `
        <div class="other-group">
          <div class="row between"><b>${esc(t("other_" + g.kind))} · ${esc(tp("conv", g.recording_ids.length))}</b>
          ${g.kind !== "unclear" ? `<label class="check small" style="padding:0"><input type="checkbox" data-ignore="${esc(g.kind)}" checked> ${esc(t("prop_ignore"))}</label>` : ""}</div>
          ${g.reason && g.kind !== "personal" ? `<p class="help" style="margin-top:4px">${esc(g.reason)}</p>` : ""}
          <details><summary class="small">${esc(t("show_list"))}</summary><ul class="recs" style="list-style:none;padding:0;font-size:.875rem">${g.recording_ids.map(recLine).join("")}</ul></details>
        </div>`).join("");
    $$("[data-ignore]", other).forEach((cb) => (cb.onchange = () => {
      const kind = cb.dataset.ignore;
      for (const g of groups.filter((x) => x.kind === kind)) for (const id of g.recording_ids) {
        if (cb.checked) ignore[id] = kind; else delete ignore[id];
      }
    }));
  }

  function payload() {
    // Merges: fold the merged proposals into their target before sending
    const out = items.map((c) => ({ ...c, keywords: [...c.keywords], email_domains: [...c.email_domains], projects: [...c.projects], recording_ids: [...c.recording_ids] }));
    for (const c of out.filter((x) => x.mergeInto)) {
      const target = out.find((x) => x !== c && x.name === c.mergeInto && !x.mergeInto);
      if (target) {
        target.accept = true;
        target.recording_ids.push(...c.recording_ids);
        target.keywords.push(c.name, ...c.keywords);
        target.email_domains.push(...c.email_domains);
        target.projects.push(...c.projects);
      } else {
        out.push({ name: c.mergeInto, existing_client: c.mergeInto, accept: true, keywords: [c.name, ...c.keywords],
          email_domains: c.email_domains, projects: c.projects, recording_ids: c.recording_ids, pocket_tags: c.pocket_tags || [] });
      }
      c.accept = false;
      c._merged = true;
    }
    return out.filter((c) => !c._merged).map(({ idx, mergeInto, _merged, ...rest }) => rest);
  }

  function summary() {
    const p = payload().filter((c) => c.accept);
    const n = p.reduce((a, c) => a + c.recording_ids.length, 0);
    $("#propSummary", root).textContent = t("prop_summary", { c: tp("client", p.length), n: tp("conv", n) });
    const btn = $("#propApply", root);
    const created = p.filter((c) => !c.existing_client).length;
    btn.innerHTML = `${icon("check")} ${esc(created ? t("prop_apply_n", { c: tp("client", created), n: tp("conv", n) }) : t("prop_apply_only", { n: tp("conv", n) }))}`;
    if (p.length) btn.removeAttribute("aria-disabled"); else btn.setAttribute("aria-disabled", "true");
  }

  $("#propDismiss", root).onclick = async () => {
    const ok = await confirmDialog({ title: t("prop_dismiss_q"), body: t("prop_dismiss_body"), ok: t("prop_dismiss"), danger: false });
    if (!ok) return;
    await api("/api/discover/dismiss", { method: "POST" });
    await refresh();
    toast(t("prop_dismissed"));
    onDismissed?.();
  };

  $("#propForm", root).onsubmit = (e) => {
    e.preventDefault();
    const btn = $("#propApply", root);
    if (btn.getAttribute("aria-disabled") === "true") { toast(t("prop_pick_one"), { type: "error" }); return; }
    const bad = items.find((c) => c.accept && !c.name.trim());
    if (bad) { toast(t("err_bad_name"), { type: "error" }); $(`#pn${bad.idx}`, root)?.focus(); return; }
    run(btn, async () => {
      const res = await api("/api/discover/apply", { method: "POST", body: { clients: payload(), ignore } });
      await refresh();
      showResult(root, res, { onboarding, onApplied });
    }, { busy: t("prop_applying") });
  };
}

function showResult(root, res, { onboarding, onApplied }) {
  root.innerHTML = `
    <div class="ai-head"><span class="ai-orb">${icon("circle-check")}</span>
      <div><h1 tabindex="-1">${esc(t("prop_done_title"))}</h1>
      <p class="lead">${esc(t("prop_done_lead", { c: tp("client", res.created.length), u: res.updated.length ? t("prop_done_updated", { n: res.updated.length }) : "", n: tp("conv", res.moved) }))}</p></div></div>
    ${res.unsorted_left ? `<div class="msg warn">${icon("inbox")}<p>${esc(tp("prop_done_left", res.unsorted_left))}</p></div>` : `<div class="msg ok">${icon("circle-check")}<p>${esc(t("prop_done_all"))}</p></div>`}
    <div class="row" style="margin-top:16px">
      ${res.undo_id ? `<button class="btn" id="undoBtn">${icon("undo-2")} ${esc(t("undo"))}</button>` : ""}
      ${onboarding ? "" : `<a class="btn primary" href="#/clients">${esc(t("prop_to_clients"))}</a>
      ${res.unsorted_left ? `<a class="btn" href="#/clients/unsorted">${esc(t("sort_rest"))}</a>` : ""}`}
    </div>`;
  $("h1", root).focus({ preventScroll: true });
  announce(t("prop_done_title"));
  $("#undoBtn", root)?.addEventListener("click", (e) => run(e.currentTarget, async () => {
    const r = await api("/api/discover/undo", { method: "POST", body: { undo_id: res.undo_id } });
    await refresh();
    toast(t("undone", { n: r.restored }));
    root.innerHTML = `<div class="msg">${icon("undo-2")}<p>${esc(t("prop_undone"))}</p></div>`;
    if (onboarding) location.reload();
  }));
  onApplied?.(res);
}
