// Vraag Claude: questions about your conversations, answered with citations you can check.
import { $, $$, api, esc, icon, t, toast, errText, md, fmtDate, ApiError } from "../core.js";
import { S } from "../state.js";

let history_ = [];  // [{role, content}] kept while the app is open
let thread = [];   // rendered bubbles: {role, html}

export async function render(root, param, ctx) {
  const st = S.state;
  const canAsk = st.ai_ready || st.settings.demo_mode;
  const cl = await api("/api/clients");
  const clients = cl.clients.map((c) => c.name);
  const chips = st.demo_questions?.length ? st.demo_questions : defaultChips(cl.clients);

  root.innerHTML = `
    <div class="page-head"><div><h1>${esc(t("nav_ask"))}</h1><p class="lead">${esc(t("ask_help"))}</p></div>
      ${thread.length ? `<button class="btn ghost" id="askReset">${icon("refresh-cw")} ${esc(t("ask_reset"))}</button>` : ""}</div>
    ${!canAsk ? `<div class="card empty"><div class="bubble-icon">${icon("key-round")}</div><h2>${esc(t("ask_nokey_title"))}</h2>
      <p>${esc(t("ask_nokey"))}</p><a class="btn primary" href="#/settings/connections">${esc(t("connect_claude"))}</a></div>` : `
    <div class="chat" id="chat" role="log" aria-live="polite" aria-label="${esc(t("ask_chat"))}">${thread.map((b) => b.html).join("")}</div>
    <div id="askEmpty" ${thread.length ? "hidden" : ""}>
      <p class="eyebrow">${esc(t("ask_try"))}</p>
      <div class="suggest-chips">${chips.map((q) => `<button type="button" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
      ${st.settings.demo_mode && !st.ai_ready ? `<p class="help" style="margin-top:12px">${esc(t("ask_demo_note"))}</p>` : ""}
    </div>
    <form class="composer" id="askForm">
      <div class="box">
        <label class="sr-only" for="question">${esc(t("ask_label"))}</label>
        <textarea id="question" rows="1" placeholder="${esc(t("ask_placeholder"))}" aria-describedby="askHint"></textarea>
        <div class="row">
          <label class="sr-only" for="askClient">${esc(t("ask_scope"))}</label>
          <select id="askClient" style="max-width:240px;min-height:40px"><option value="">${esc(t("ask_all"))}</option>${clients.map((c) => `<option>${esc(c)}</option>`).join("")}</select>
          <span class="help" id="askHint">${esc(t("ask_hint"))}</span><span class="spacer"></span>
          <button type="button" class="btn ghost" id="askStop" hidden>${icon("x")} ${esc(t("stop"))}</button>
          <button type="submit" class="btn ai" id="askBtn">${icon("sparkles")} ${esc(t("ask_send"))}</button>
        </div>
      </div>
    </form>`}`;
  if (!canAsk) return;

  const q = $("#question", root);
  const grow = () => { q.style.height = "auto"; q.style.height = Math.min(200, q.scrollHeight) + "px"; };
  q.addEventListener("input", grow);
  q.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#askForm", root).requestSubmit(); }
  });
  $$("[data-q]", root).forEach((b) => (b.onclick = () => { q.value = b.dataset.q; $("#askForm", root).requestSubmit(); }));
  $("#askReset", root)?.addEventListener("click", () => { history_ = []; thread = []; render(root, "", ctx); });
  $("#askForm", root).onsubmit = (e) => { e.preventDefault(); ask(root, q.value.trim()); };
  if (param) {
    history.replaceState(null, "", "#/ask");
    ask(root, param);
  }
  q.focus();
}

function defaultChips(clients) {
  const top = [...clients].filter((c) => !c.folder_only).sort((a, b) => (b.last_date || "").localeCompare(a.last_date || ""));
  const out = [];
  if (top[0]) out.push(t("chip_status", { c: top[0].name }), t("chip_actions", { c: top[0].name }));
  if (top[1]) out.push(t("chip_agreements", { c: top[1].name }));
  out.push(t("chip_week"));
  return out.slice(0, 4);
}

let controller = null;

async function ask(root, question) {
  if (!question || controller) return;
  const chat = $("#chat", root);
  const client = $("#askClient", root).value;
  $("#askEmpty", root).hidden = true;
  $("#question", root).value = "";
  $("#question", root).style.height = "auto";
  const userHtml = `<div class="bubble user">${esc(question)}</div>`;
  thread.push({ role: "user", html: userHtml });
  chat.insertAdjacentHTML("beforeend", userHtml);
  const bubble = document.createElement("div");
  bubble.className = "bubble claude";
  bubble.innerHTML = `<div class="who">${icon("sparkles")} Claude</div><div class="body"><span class="muted">${esc(t("ask_reading"))}</span><span class="cursor"></span></div>`;
  chat.append(bubble);
  bubble.scrollIntoView({ block: "end", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });

  controller = new AbortController();
  const stop = $("#askStop", root), send = $("#askBtn", root);
  stop.hidden = false; send.setAttribute("aria-disabled", "true");
  stop.onclick = () => controller?.abort();
  let text = "", sources = [], example = false;
  const body = $(".body", bubble);
  try {
    const res = await fetch("/api/ask", {
      method: "POST", signal: controller.signal, headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, client, history: history_ }),
    });
    if (!res.ok) {
      let code = "error";
      try { code = (await res.json()).detail?.code || code; } catch { /* ignore */ }
      throw new ApiError(code);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).replace(/^data: /, "");
        buf = buf.slice(i + 2);
        if (!line.trim()) continue;
        const ev = JSON.parse(line);
        if (ev.type === "sources") {
          sources = ev.sources; example = !!ev.example;
          body.innerHTML = `<span class="muted small">${esc(t("ask_read_n", { n: sources.length }))}</span><div class="md"></div><span class="cursor"></span>`;
        } else if (ev.type === "text") {
          text += ev.text;
          $(".md", body).innerHTML = md(text, { headingStart: 4 });
        } else if (ev.type === "done") {
          body.innerHTML = renderAnswer(ev.blocks, sources, ev.example || example);
        } else if (ev.type === "refused") {
          body.innerHTML = `<p>${esc(t("ask_refused"))}</p>`;
        } else if (ev.type === "error") {
          throw new ApiError(ev.code || "claude_error");
        }
      }
    }
    history_.push({ role: "user", content: question }, { role: "assistant", content: text });
  } catch (e) {
    if (e.name === "AbortError") body.innerHTML = (text ? `<div class="md">${md(text, { headingStart: 4 })}</div>` : "") + `<p class="muted small">${esc(t("ask_stopped"))}</p>`;
    else body.innerHTML = `<div class="msg err" style="margin:0">${icon("circle-alert")}<p>${esc(errText(e))}${e.code === "claude_no_key_demo" ? "" : ` <button class="link-btn" data-retry>${esc(t("try_again"))}</button>`}</p></div>`;
    $("[data-retry]", body)?.addEventListener("click", () => { bubble.remove(); thread.pop(); ask(root, question); });
  } finally {
    controller = null;
    stop.hidden = true; send.removeAttribute("aria-disabled");
    thread.push({ role: "claude", html: bubble.outerHTML });
  }
}

function renderAnswer(blocks, sources, example) {
  let html = "";
  const used = new Map();
  for (const b of blocks) {
    let refs = "";
    for (const c of b.citations || []) {
      const src = sources[c.source];
      if (!src) continue;
      if (!used.has(c.source)) used.set(c.source, { n: used.size + 1, quotes: [] });
      const u = used.get(c.source);
      if (c.cited_text && !u.quotes.includes(c.cited_text)) u.quotes.push(c.cited_text);
      refs += `<a class="cite" href="#/conversations/${encodeURIComponent(src.pocket_id)}" aria-label="${esc(t("source_n", { n: u.n, title: src.title }))}" title="${esc(c.cited_text || "")}">${u.n}</a>`;
    }
    html += esc(b.text) + refs;
  }
  // Render markdown on the text while keeping our citation links
  const tokens = [];
  const safe = html.replace(/<a class="cite"[\s\S]*?<\/a>/g, (m) => { tokens.push(m); return `\u0000${tokens.length - 1}\u0000`; });
  const unescaped = safe.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'");
  let out = md(unescaped, { headingStart: 4 }).replace(/\u0000(\d+)\u0000/g, (_, i) => tokens[+i]);
  if (used.size) {
    out += `<ol class="sources" aria-label="${esc(t("sources"))}">` + [...used.entries()].map(([si, u]) => {
      const s = sources[si];
      return `<li><b>${u.n}.</b> <a href="#/conversations/${encodeURIComponent(s.pocket_id)}">${esc(s.title)}</a> <span class="muted">· ${esc(fmtDate(s.date, { time: false }))}${s.client ? " · " + esc(s.client) : ""}</span>
        ${u.quotes.slice(0, 2).map((q) => `<q>${esc(q.trim())}</q>`).join("")}</li>`;
    }).join("") + "</ol>";
  }
  if (example) out += `<p class="help" style="margin-top:10px">${esc(t("example_result"))}</p>`;
  return `<div class="md">${out}</div>`;
}
