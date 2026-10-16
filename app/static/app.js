// Clause & Pay page. Plain JS, no build step. Every value from the server is
// inserted as text (textContent), never as HTML.
const $ = (s) => document.querySelector(s);
let CFG = { paypal: false, ai: false };
let W = null; // current workspace
let SEL = null; // selected milestone code

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) n.append(k.nodeType ? k : String(k));
  return n;
}

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  let body = null;
  try { body = await r.json(); } catch { /* empty */ }
  if (!r.ok) throw new Error((body && body.detail) || `Request failed (${r.status})`);
  return body;
}
const post = (path, data) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data || {}) });

function money(cur, v) {
  if (v === null || v === undefined) return "amount unclear";
  const n = Number(v);
  return `${cur} ${n.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function cite(c) {
  if (!c) return null;
  const ok = c.status === "verified" || c.status === "moved";
  return el("div", {},
    el("p", { class: "quote" }, `“${c.quote}”`),
    el("span", { class: "cite" },
      el("span", { class: "page" }, `page ${c.page ?? "?"}`),
      el("span", { class: `ver ${ok ? "ok" : "bad"}` }, ok ? "Found on the page" : "Not found: check by hand")));
}

// The app's milestone states, as shown on the timeline.
const STATE_WORDS = { planned: "Planned", delivered: "Delivered", held: "Held", invoiced: "Invoiced", paid: "Paid" };

// PayPal's status codes, said in plain words.
const PP_WORDS = { DRAFT: "Draft", SENT: "Sent, awaiting payment", SCHEDULED: "Scheduled", UNPAID: "Unpaid",
  PAID: "Paid", MARKED_AS_PAID: "Paid", PARTIALLY_PAID: "Partly paid", CANCELLED: "Cancelled",
  REFUNDED: "Refunded", PARTIALLY_REFUNDED: "Partly refunded", PAYMENT_PENDING: "Payment pending" };
const ppWord = (s) => PP_WORDS[s] || (s || "").toLowerCase().replace(/_/g, " ");
const longDate = (iso) => { const d = new Date(`${iso}T12:00:00`); return isNaN(d) ? iso : d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" }); };
const isPaid = (s) => s === "PAID" || s === "MARKED_AS_PAID";
const localTime = (iso) => { const d = new Date(iso); return isNaN(d) ? iso.slice(11, 16) : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); };

// Invoiced, paid and still to collect, across the contract's milestones.
function ledger() {
  let invoiced = 0, paid = 0;
  for (const m of W.schedule.milestones) {
    const inv = W.invoices[m.code]; const v = Number(m.amount) || 0;
    if (inv) { invoiced += v; if (isPaid(inv.status) || W.status[m.code] === "paid") paid += v; }
  }
  return { invoiced, paid, open: invoiced - paid };
}

function renderStatus() {
  const s = $("#status");
  s.replaceChildren(
    el("span", { class: `badge ${CFG.ai ? "on" : "off"}` }, CFG.ai ? `AI: ${CFG.model}` : "AI not configured"),
    el("span", { class: `badge ${CFG.paypal ? "on" : "off"}` }, CFG.paypal ? `PayPal ${CFG.environment} connected` : "PayPal not configured"));
}

function renderSummary() {
  const s = W.schedule;
  const st = { quotes: 0, ok: 0 };
  const all = [s.payment_terms, s.late_payment, s.invoice_condition].filter(Boolean).map((c) => c.cite)
    .concat(s.milestones.flatMap((m) => [m.payment_cite, m.acceptance_cite].filter(Boolean)));
  for (const c of all) { st.quotes++; if (c.status === "verified" || c.status === "moved") st.ok++; }
  const clause = (label, c) => c && el("div", { class: "clause" }, el("div", { class: "what" }, `${label}: ${c.summary}`), cite(c.cite));
  $("#summary").replaceChildren(
    el("h2", {}, s.contract_title),
    el("dl", { class: "facts" },
      el("dt", {}, "Reference"), el("dd", {}, s.contract_reference || "none given"),
      el("dt", {}, "You (supplier)"), el("dd", {}, s.supplier_name),
      el("dt", {}, "Client"), el("dd", {}, s.client_name),
      el("dt", {}, "Contract value"), el("dd", {}, s.total_value ? money(s.currency, s.total_value) : "not stated"),
      el("dt", {}, "Quotes checked"), el("dd", {}, `${st.ok} of ${st.quotes} found on their page`)),
    (() => { const l = ledger(); return el("div", { class: "ledger", "aria-label": "Money on this contract" },
      el("div", {}, el("span", { class: "k" }, "Invoiced"), el("span", { class: "v" }, money(s.currency, l.invoiced))),
      el("div", { class: l.paid > 0 ? "paid" : "" }, el("span", { class: "k" }, "Paid"), el("span", { class: "v" }, money(s.currency, l.paid))),
      el("div", { class: l.open > 0 ? "open" : "" }, el("span", { class: "k" }, "To collect"), el("span", { class: "v" }, money(s.currency, l.open)))); })(),
    ...s.issues.map((i) => el("p", { class: "issue" }, i)),
    clause("Payment terms", s.payment_terms),
    clause("Before invoicing", s.invoice_condition),
    clause("Late payment", s.late_payment));
}

function renderTimeline() {
  const s = W.schedule;
  $("#timeline").replaceChildren(...s.milestones.map((m) => {
    const state = W.status[m.code] || "planned";
    return el("li", {}, el("button", { class: `ms ${SEL === m.code ? "sel" : ""}`, onclick: () => { SEL = m.code; render(); } },
      el("span", { class: "code" }, m.code),
      el("span", {}, el("strong", {}, m.name), el("div", { class: "when" }, `Due: ${m.due_as_written}`),
        ...m.issues.map((i) => el("div", { class: "issue" }, i))),
      el("span", { style: "text-align:right" }, el("div", { class: "amt" }, money(m.currency, m.amount)),
        el("span", { class: `state ${state}` }, STATE_WORDS[state] || state))));
  }));
}

function renderLog() {
  $("#log").replaceChildren(...[...W.log].reverse().map((e) =>
    el("li", {}, el("time", { datetime: e.at }, localTime(e.at)), el("span", { class: e.kind }, e.text))));
}

function renderPanel() {
  const p = $("#panel");
  const m = W.schedule.milestones.find((x) => x.code === SEL);
  if (!m) { p.replaceChildren(el("p", { class: "hint" }, "Select a milestone to check the delivery and send its invoice.")); return; }
  const state = W.status[m.code] || "planned";
  const check = W.checks[m.code];
  const inv = W.invoices[m.code];
  const err = el("p", { class: "err", role: "alert" });
  const busy = (b, on, label) => { b.disabled = on; if (label) b.textContent = label; };

  const nodes = [
    el("h3", {}, `${m.code}: ${m.name}`),
    el("p", { class: "when" }, `${money(m.currency, m.amount)} · due ${m.due_as_written}`),
    el("div", { class: "clause" }, el("div", { class: "what" }, "Payment wording"), cite(m.payment_cite)),
    el("div", { class: "what" }, "Acceptance criteria"),
    el("ul", { class: "criteria" }, ...m.acceptance_criteria.map((c) => el("li", {}, c))),
    m.acceptance_cite && el("div", { class: "clause" }, cite(m.acceptance_cite)),
  ];

  if (!inv) {
    const ev = el("textarea", { id: "evidence", placeholder: "What did you deliver, and has the client accepted it? e.g. 'Inception report sent 5 Oct. Survey method, 1,250 passenger sample, dated work plan for M2 to M4. Accepted in writing by Ada Brennan on 9 Oct.'" });
    const checkBtn = el("button", { class: "btn primary", onclick: async () => {
      err.textContent = ""; busy(checkBtn, true, "Checking against the contract...");
      try { W = await post(`/api/contracts/${W.id}/milestones/${m.code}/deliver`, { evidence: ev.value }); render(); }
      catch (e) { err.textContent = e.message; busy(checkBtn, false, "Mark delivered and check"); }
    } }, "Mark delivered and check");
    nodes.push(el("label", { class: "f", for: "evidence" }, "Delivery note"), ev, el("div", { class: "row" }, checkBtn));
  }

  if (check) {
    nodes.push(el("div", { class: `result ${check.recommendation}` },
      el("strong", {}, check.recommendation === "send" ? "Ready to invoice" : "Hold the invoice"),
      el("p", {}, check.summary),
      el("ul", {}, ...check.criteria.map((c) => el("li", {}, `${c.met.toUpperCase()}: ${c.criterion}. ${c.evidence}`)))));
  }

  if (!inv && (state === "delivered" || state === "held")) {
    const email = el("input", { type: "email", id: "recipient", placeholder: "client's PayPal email (sandbox buyer)" });
    const override = state === "held" ? el("label", { class: "hint" }, el("input", { type: "checkbox", id: "override" }), " Send anyway (I take responsibility)") : null;
    const send = el("button", { class: "btn primary", disabled: !CFG.paypal, onclick: async () => {
      err.textContent = "";
      if (!email.value.includes("@")) { err.textContent = "Enter the client's email."; return; }
      busy(send, true, "Creating the invoice in PayPal...");
      try {
        W = await post(`/api/contracts/${W.id}/milestones/${m.code}/invoice`,
          { recipient_email: email.value, approve: true, override_hold: !!(override && override.querySelector("input").checked) });
        render();
      } catch (e) { err.textContent = e.message; busy(send, false, "Approve and send invoice"); }
    } }, "Approve and send invoice");
    nodes.push(el("label", { class: "f", for: "recipient" }, "Send the invoice to"), email, override,
      el("div", { class: "row" }, send),
      !CFG.paypal && el("p", { class: "hint" }, "PayPal is not configured on this server, so invoices cannot be sent. Add sandbox keys (see the README)."));
  }

  if (inv) {
    const refresh = el("button", { class: "btn", onclick: async () => {
      err.textContent = "";
      try { W = await post(`/api/contracts/${W.id}/milestones/${m.code}/refresh`); render(); } catch (e) { err.textContent = e.message; }
    } }, "Check payment status");
    const today = el("input", { type: "date", id: "today", "aria-label": "Pretend today is" });
    const preview = el("div", { class: "preview", hidden: true });
    const draft = el("button", { class: "btn", onclick: async () => {
      err.textContent = "";
      try {
        const r = await post(`/api/contracts/${W.id}/milestones/${m.code}/reminder`, { approve: false, today: today.value || null });
        preview.hidden = false; preview.textContent = `${r.subject}\n\n${r.note}`; sendRem.hidden = false;
      } catch (e) { err.textContent = e.message; }
    } }, "Draft a reminder");
    const sendRem = el("button", { class: "btn primary", hidden: true, onclick: async () => {
      err.textContent = ""; busy(sendRem, true, "Sending...");
      try { const r = await post(`/api/contracts/${W.id}/milestones/${m.code}/reminder`, { approve: true, today: today.value || null }); W = r.workspace; render(); }
      catch (e) { err.textContent = e.message; busy(sendRem, false, "Approve and send reminder"); }
    } }, "Approve and send reminder");
    nodes.push(el("div", { class: "result send" },
      el("strong", {}, isPaid(inv.status) ? `Invoice ${inv.number}` : `Invoice ${inv.number}: ${ppWord(inv.status)}`),
      isPaid(inv.status) ? el("span", { class: "stamp", "aria-label": "Paid" }, "Paid") : null,
      el("p", {}, inv.due_date && !isPaid(inv.status) ? `Due ${longDate(inv.due_date)}` : ""),
      inv.link && el("a", { class: "link", href: inv.link, target: "_blank", rel: "noopener" }, "Open the invoice in PayPal")),
      isPaid(inv.status) ? null : el("div", { class: "row" }, refresh, OPEN_STATES.includes(inv.status) ? el("button", { class: "btn", onclick: async (ev) => {
        err.textContent = ""; ev.target.disabled = true; ev.target.textContent = "Recording in the PayPal sandbox...";
        try { W = await post(`/api/contracts/${W.id}/milestones/${m.code}/sandbox-payment`); render(); }
        catch (e) { err.textContent = e.message; ev.target.disabled = false; ev.target.textContent = "Sandbox: record the client's payment"; }
      } }, "Sandbox: record the client's payment") : null),
      OPEN_STATES.includes(inv.status) ? el("p", { class: "hint" }, "Demo only: logs the client's bank transfer in PayPal's sandbox, so you can watch PayPal confirm the payment here.") : null,
      ...(isPaid(inv.status) ? [] : [
        el("label", { class: "f", for: "today" }, "Overdue? Draft a reminder that quotes the contract (optional: pretend today is)"),
        today, el("div", { class: "row" }, draft, sendRem), preview]));
  }
  nodes.push(err);
  p.replaceChildren(...nodes.filter(Boolean));
}

// Ask the billing agent: Gemini answers from the contract and looks invoices up
// with PayPal's Agent Toolkit. A reminder it wants to send is held as a
// proposal until the user approves it here.
const AGENT = { q: "", busy: false, answer: null, trace: [], err: "" };
const EXAMPLES = [
  "Which of my invoices are still unpaid?",
  "What interest can I charge if M1 is paid late?",
  "Draft a payment reminder for M1 that quotes the contract.",
];
function renderAgent() {
  const box = $("#agent");
  const q = el("textarea", { id: "ask", rows: "2", placeholder: "Ask about this contract and its PayPal invoices", "aria-label": "Your question" });
  q.value = AGENT.q;
  q.addEventListener("input", () => { AGENT.q = q.value; });
  const ready = CFG.paypal && CFG.ai;
  const go = el("button", { class: "btn primary", disabled: AGENT.busy || !ready, onclick: async () => {
    if (!AGENT.q.trim()) return;
    AGENT.busy = true; AGENT.err = ""; renderAgent();
    try {
      const r = await post(`/api/contracts/${W.id}/ask`, { question: AGENT.q });
      AGENT.answer = r.answer; AGENT.trace = r.trace || []; W = r.workspace; AGENT.busy = false; render();
    } catch (e) { AGENT.err = e.message; AGENT.busy = false; renderAgent(); }
  } }, AGENT.busy ? "Checking PayPal and the contract..." : "Ask");
  const examples = el("div", { class: "examples" }, ...EXAMPLES.map((t) =>
    el("button", { class: "chip", type: "button", onclick: () => { AGENT.q = t; renderAgent(); $("#ask").focus(); } }, t)));
  const nodes = [
    el("h2", {}, "Ask the billing agent"),
    el("p", { class: "hint" }, "Answers come from the contract, with pages, and from PayPal through its Agent Toolkit. Anything the agent wants to send waits for your approval."),
    q, examples, el("div", { class: "row" }, go),
    !ready && el("p", { class: "hint" }, "Needs PayPal and the AI configured on this server."),
  ];
  if (AGENT.err) nodes.push(el("p", { class: "err", role: "alert" }, AGENT.err));
  if (AGENT.answer) {
    nodes.push(el("div", { class: "answer" },
      el("p", {}, AGENT.answer),
      AGENT.trace.length ? el("div", { class: "tools" }, el("span", { class: "hint" }, "PayPal Agent Toolkit:"),
        ...AGENT.trace.map((t) => el("span", { class: "badge on" }, t.tool))) : null));
  }
  for (const p of Object.values(W.proposals || {})) {
    nodes.push(el("div", { class: "result hold proposal" },
      el("strong", {}, "Waiting for your approval"),
      el("p", {}, p.summary),
      p.args.subject ? el("p", { class: "hint" }, `Subject: ${p.args.subject}`) : null,
      p.args.note ? el("div", { class: "preview" }, p.args.note) : null,
      el("div", { class: "row" },
        el("button", { class: "btn primary", onclick: async (ev) => {
          ev.target.disabled = true;
          try { W = await post(`/api/contracts/${W.id}/proposals/${p.id}/approve`); render(); }
          catch (e) { AGENT.err = e.message; renderAgent(); }
        } }, "Approve and send"),
        el("button", { class: "btn", onclick: async () => {
          try { W = await post(`/api/contracts/${W.id}/proposals/${p.id}/dismiss`); render(); } catch (e) { AGENT.err = e.message; renderAgent(); }
        } }, "Dismiss"))));
  }
  box.replaceChildren(...nodes.filter(Boolean));
}

function render() {
  if (!W) return;
  $("#start").hidden = true; $("#contract").hidden = false;
  if (!SEL && W.schedule.milestones.length) SEL = W.schedule.milestones[0].code;
  renderSummary(); renderTimeline(); renderPanel(); renderAgent(); renderLog();
  watchPayments();
}

// While an invoice is open in PayPal, ask the server every 10 s whether the
// PayPal webhook has marked it paid, so the page updates without a click. The
// page is redrawn only when something changed, so typing is not interrupted.
const OPEN_STATES = ["SENT", "UNPAID", "PARTIALLY_PAID", "SCHEDULED"];
let POLL = null;
const fingerprint = (w) => JSON.stringify([w.status, Object.values(w.invoices).map((i) => i.status), w.log.length]);
function watchPayments() {
  const open = Object.values(W.invoices || {}).some((i) => OPEN_STATES.includes(i.status));
  if (!open) { if (POLL) { clearTimeout(POLL); POLL = null; } return; }
  if (POLL) return;
  POLL = setTimeout(async () => {
    POLL = null;
    try {
      const fresh = await api(`/api/contracts/${W.id}`);
      if (fingerprint(fresh) !== fingerprint(W)) { W = fresh; render(); return; }
    } catch { /* keep the last view */ }
    watchPayments();
  }, 10000);
}

async function start(promise) {
  const msg = $("#startmsg");
  msg.textContent = "Reading the contract and checking every quote against its page...";
  $("#sample").disabled = true;
  try { W = await promise; render(); }
  catch (e) { msg.textContent = e.message; }
  finally { $("#sample").disabled = false; }
}

$("#sample").addEventListener("click", () => start(post("/api/contracts/sample")));
$("#file").addEventListener("change", (ev) => {
  const f = ev.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  start(api("/api/contracts", { method: "POST", body: fd }));
});

api("/api/config").then((c) => { CFG = c; renderStatus(); }).catch(() => renderStatus());
