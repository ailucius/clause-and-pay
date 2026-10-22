// Clause & Pay page. Plain JS, no build step. Every value from the server is
// inserted as text (textContent), never as HTML. AG Grid cell renderers below
// always return DOM nodes built with el(), never HTML strings.
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
const post = (path, data, extra = {}) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data || {}), ...extra });

function money(cur, v) {
  if (v === null || v === undefined || v === "") return "amount unclear";
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

// The app's milestone states, as shown in the schedule.
const STATE_WORDS = { planned: "Planned", delivered: "Delivered", held: "Held", invoiced: "Invoiced", paid: "Paid" };

// PayPal's status codes, said in plain words.
const PP_WORDS = { DRAFT: "Draft", SENT: "Sent, awaiting payment", SCHEDULED: "Scheduled", UNPAID: "Unpaid",
  PAID: "Paid", MARKED_AS_PAID: "Paid", PARTIALLY_PAID: "Partly paid", CANCELLED: "Cancelled",
  REFUNDED: "Refunded", PARTIALLY_REFUNDED: "Partly refunded", PAYMENT_PENDING: "Payment pending" };
const ppWord = (s) => PP_WORDS[s] || (s || "").toLowerCase().replace(/_/g, " ");
const longDate = (iso) => { if (!iso) return ""; const d = new Date(`${iso.slice(0, 10)}T12:00:00`); return isNaN(d) ? iso : d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" }); };
const shortDate = (iso) => { if (!iso) return ""; const d = new Date(`${iso.slice(0, 10)}T12:00:00`); return isNaN(d) ? iso : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }); };
const isPaid = (s) => s === "PAID" || s === "MARKED_AS_PAID";
const localTime = (iso) => { const d = new Date(iso); return isNaN(d) ? iso.slice(11, 16) : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); };
const dateTime = (iso) => { const d = new Date(iso); return isNaN(d) ? iso : `${d.toLocaleDateString("en-GB", { day: "numeric", month: "short" })}, ${localTime(iso)}`; };
const kb = (n) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
const msOf = (code) => W.schedule.milestones.find((x) => x.code === code);
const todayISO = () => new Date().toISOString().slice(0, 10);
const daysBetween = (a, b) => Math.round((new Date(`${b}T12:00:00`) - new Date(`${a}T12:00:00`)) / 86400000);

// Invoiced, paid and still to collect, across the contract's milestones.
function ledgerTotals() {
  let invoiced = 0, paid = 0;
  for (const m of W.schedule.milestones) {
    const inv = W.invoices[m.code]; const v = Number(m.amount) || 0;
    if (inv) { invoiced += v; if (isPaid(inv.status) || W.status[m.code] === "paid") paid += v; }
  }
  return { invoiced, paid, open: invoiced - paid };
}

// A message that says what is happening while a slow step runs, with the seconds
// waited so far. `steps` is a list of [from_second, text]; "{s}" becomes the seconds.
function progress(node, steps, t0 = Date.now()) {
  const tick = () => {
    const s = Math.floor((Date.now() - t0) / 1000);
    const step = steps.filter(([from]) => s >= from).pop();
    node.textContent = step[1].replace("{s}", s);
  };
  tick();
  const id = setInterval(tick, 1000);
  return () => { clearInterval(id); return Math.max(1, Math.round((Date.now() - t0) / 1000)); };
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
    (() => { const l = ledgerTotals(); return el("div", { class: "ledger", "aria-label": "Money on this contract" },
      el("div", {}, el("span", { class: "k" }, "Invoiced"), el("span", { class: "v" }, money(s.currency, l.invoiced))),
      el("div", { class: l.paid > 0 ? "paid" : "" }, el("span", { class: "k" }, "Paid"), el("span", { class: "v" }, money(s.currency, l.paid))),
      el("div", { class: l.open > 0 ? "open" : "" }, el("span", { class: "k" }, "To collect"), el("span", { class: "v" }, money(s.currency, l.open)))); })(),
    ...s.issues.map((i) => el("p", { class: "issue" }, i)),
    clause("Payment terms", s.payment_terms),
    clause("Before invoicing", s.invoice_condition),
    clause("Late payment", s.late_payment));
}

// ---- AG Grid: the payment schedule, the invoices and the ledger ----
// AG Grid Community, pinned in index.html. Without it (the CDN blocked), the
// schedule falls back to a plain list and the invoices and ledger to plain tables.
const AG = window.agGrid && window.agGrid.createGrid ? window.agGrid : null;
const GRIDS = { ms: null, inv: null, led: null };
let BOOKS_TAB = "inv";

function gridTheme() {
  return AG.themeQuartz.withParams({
    fontFamily: "inherit", fontSize: 13.5, headerFontSize: 12.5, headerFontWeight: 600, spacing: 6,
    backgroundColor: "var(--panel)", foregroundColor: "var(--ink)", textColor: "var(--ink)",
    borderColor: "var(--line)", accentColor: "var(--navy-2)",
    headerBackgroundColor: "var(--chip)", headerTextColor: "var(--muted)",
    rowHoverColor: "color-mix(in srgb, var(--chip) 55%, transparent)",
    selectedRowBackgroundColor: "color-mix(in srgb, var(--navy-2) 14%, transparent)",
    wrapperBorderRadius: 8, borderRadius: 6, browserColorScheme: "inherit",
  });
}

const pill = (text, cls) => el("span", { class: `pill ${cls}` }, text);
const stateCell = (p) => (p.value ? el("span", { class: `state ${p.value}` }, STATE_WORDS[p.value] || p.value) : null);
const ppCell = (p) => (p.value ? pill(ppWord(p.value), isPaid(p.value) ? "ok" : p.value === "CANCELLED" ? "bad" : "info") : null);
const safeLink = (u) => (typeof u === "string" && /^https:\/\/([a-z0-9-]+\.)*paypal\.com\//i.test(u) ? u : null);

const EVENT_WORDS = { drafted: "Drafted", sent: "Sent", override: "Hold overridden", reminded: "Reminder sent",
  payment_recorded: "Payment recorded (sandbox)", paid: "Paid", cancelled: "Cancelled" };
const EVENT_CLS = { drafted: "muted", sent: "info", override: "warn", reminded: "info", payment_recorded: "muted", paid: "ok", cancelled: "bad" };

function milestoneRows() {
  return W.schedule.milestones.map((m) => {
    const inv = W.invoices[m.code];
    return { ...m, amountNum: m.amount === null ? null : Number(m.amount), state: W.status[m.code] || "planned",
      invoice: inv ? inv.number : null };
  });
}

function invoiceRows() {
  const today = todayISO();
  return Object.entries(W.invoices).map(([code, i]) => {
    const m = msOf(code) || {};
    const open = !isPaid(i.status) && i.status !== "CANCELLED";
    return { code, milestone: `${code} ${m.name || ""}`.trim(), number: i.number, id: i.id,
      amount: Number(i.amount ?? m.amount), currency: i.currency || m.currency, status: i.status,
      invoice_date: i.invoice_date || null, sent_at: i.sent_at || null, due_date: i.due_date || null,
      days: open && i.due_date ? daysBetween(today, i.due_date) : null,
      reminders: i.reminders || 0, paid_at: i.paid_at || null, link: safeLink(i.link), override: i.override_reason || "" };
  });
}

function ledgerRows() {
  return (W.ledger || []).map((e, n) => {
    const m = msOf(e.code) || {};
    return { n, ...e, milestone: `${e.code} ${m.name || ""}`.trim(), amountNum: e.amount === null || e.amount === undefined ? null : Number(e.amount),
      currency: e.currency || m.currency };
  });
}

const moneyCol = (field, headerName = "Amount") => ({ field, headerName, type: "rightAligned", width: 130, minWidth: 118,
  valueFormatter: (p) => (p.value === null || p.value === undefined || isNaN(p.value) ? "" : money(p.data.currency, p.value)) });

function msColumns() {
  return [
    { field: "code", headerName: "", width: 54, minWidth: 46, maxWidth: 64, cellClass: "g-code", sortable: false },
    { field: "name", headerName: "Milestone", flex: 2, minWidth: 120, wrapText: true, autoHeight: true,
      cellRenderer: (p) => el("div", {}, el("strong", {}, p.data.name),
        el("div", { class: "when g-due-inline" }, `Due ${p.data.due_as_written}`),
        el("div", { class: "g-amt-inline" }, money(p.data.currency, p.data.amount)),
        ...(p.data.issues || []).map((i) => el("div", { class: "issue" }, i))) },
    { colId: "due", field: "due_date", headerName: "Due", flex: 1, minWidth: 116, wrapText: true, autoHeight: true,
      valueFormatter: (p) => p.data.due_as_written },
    { ...moneyCol("amountNum"), colId: "amount", headerName: "Amount" },
    { field: "state", headerName: "Status", width: 100, minWidth: 92, maxWidth: 120, cellRenderer: stateCell,
      valueFormatter: (p) => STATE_WORDS[p.value] || p.value },
  ];
}

function invColumns() {
  return [
    { field: "number", headerName: "Invoice", width: 92, minWidth: 88, pinned: "left" },
    { field: "milestone", headerName: "Milestone", width: 190, minWidth: 140, wrapText: true, autoHeight: true },
    moneyCol("amount"),
    { field: "status", headerName: "PayPal status", width: 180, minWidth: 160, cellRenderer: ppCell, valueFormatter: (p) => ppWord(p.value) },
    { field: "sent_at", headerName: "Sent", width: 112, minWidth: 104, valueFormatter: (p) => shortDate(p.value) },
    { field: "due_date", headerName: "Due", width: 170, minWidth: 140,
      valueFormatter: (p) => shortDate(p.value),
      cellRenderer: (p) => p.value ? el("span", {}, shortDate(p.value),
        p.data.days === null ? null : el("span", { class: `g-days ${p.data.days < 0 ? "late" : ""}` },
          p.data.days < 0 ? ` ${-p.data.days} d overdue` : p.data.days === 0 ? " due today" : ` in ${p.data.days} d`)) : null },
    { field: "reminders", headerName: "Reminders", width: 104, minWidth: 96, type: "rightAligned" },
    { field: "paid_at", headerName: "Paid", width: 112, minWidth: 104, valueFormatter: (p) => shortDate(p.value) },
    { field: "link", headerName: "Client's page", width: 130, minWidth: 124, sortable: false,
      cellRenderer: (p) => p.value ? el("a", { class: "link", href: p.value, target: "_blank", rel: "noopener" }, "Open in PayPal") : null },
  ];
}

function ledColumns() {
  return [
    // Newest first; events recorded in the same second keep the order they happened in.
    { field: "at", headerName: "When", width: 128, minWidth: 124, sort: "desc", valueFormatter: (p) => dateTime(p.value),
      comparator: (a, b, na, nb) => (a < b ? -1 : a > b ? 1 : na.data.n - nb.data.n) },
    { field: "event", headerName: "Event", width: 206, minWidth: 200, valueFormatter: (p) => EVENT_WORDS[p.value] || p.value,
      cellRenderer: (p) => pill(EVENT_WORDS[p.value] || p.value, EVENT_CLS[p.value] || "muted") },
    // The invoice with its milestone code; the milestone's name is in the CSV and on hover.
    { field: "invoice", headerName: "Invoice", width: 104, minWidth: 100, tooltipField: "milestone",
      valueFormatter: (p) => (p.value ? `${p.value} · ${p.data.code}` : p.data.code) },
    moneyCol("amountNum"),
    { field: "currency", headerName: "Currency", hide: true }, // in the CSV, next to the plain amount
    { field: "milestone", headerName: "Milestone", hide: true },
    // Source and Detail share the rest of the width and never go narrower than their
    // longest words (a tool name, an email address), so text wraps between words.
    { field: "source", headerName: "Source", flex: 1, minWidth: 250, wrapText: true, autoHeight: true },
    { field: "detail", headerName: "Detail", flex: 1.2, minWidth: 270, wrapText: true, autoHeight: true },
  ];
}

const NO_ROWS = {
  inv: "No invoices yet. Check a delivery, then approve its invoice.",
  led: "Nothing in the ledger yet. Every invoice event will appear here.",
};

function makeGrid(host, opts) {
  // A click focuses a cell too, and AG Grid outlines the focused cell. Keep that outline for
  // keyboard users and drop it after a mouse or touch click: the class says how focus came.
  host.addEventListener("pointerdown", () => host.classList.add("pointer-focus"), true);
  host.addEventListener("keydown", () => host.classList.remove("pointer-focus"), true);
  return AG.createGrid(host, {
    theme: gridTheme(), domLayout: "autoHeight", animateRows: true, enableCellTextSelection: true,
    suppressDragLeaveHidesColumns: true, suppressMovableColumns: true,
    defaultColDef: { sortable: true, resizable: true, filter: false },
    // Columns share the card's width; below their minimum widths the grid scrolls sideways.
    // (A grid on a hidden tab has no width yet: it is fitted when it is shown.)
    onGridSizeChanged: (e) => { if (e.clientWidth > 0) e.api.sizeColumnsToFit(); },
    ...opts,
  });
}

function ensureGrids() {
  if (!AG || GRIDS.ms) return;
  $("#timeline").hidden = true;
  GRIDS.ms = makeGrid($("#msgrid"), {
    columnDefs: msColumns(), rowData: milestoneRows(), getRowId: (p) => p.data.code,
    defaultColDef: { sortable: true, resizable: false, filter: false },
    rowSelection: { mode: "singleRow", checkboxes: false, enableClickSelection: true },
    onSelectionChanged: (e) => {
      const r = e.api.getSelectedRows()[0];
      if (r && r.code !== SEL) { SEL = r.code; renderPanel(); }
    },
    onCellKeyDown: (e) => { if (e.event && e.event.key === "Enter") e.node.setSelected(true); },
    // A narrow schedule shows the due date under the name instead of in its own column.
    onGridSizeChanged: (e) => fitSchedule(e.api, e.clientWidth),
    onFirstDataRendered: (e) => fitSchedule(e.api, $("#msgrid").clientWidth),
  });
  GRIDS.inv = makeGrid($("#invgrid"), {
    columnDefs: invColumns(), rowData: invoiceRows(), getRowId: (p) => p.data.code,
    overlayNoRowsTemplate: `<span class="g-empty">${NO_ROWS.inv}</span>`,
  });
  GRIDS.led = makeGrid($("#ledgrid"), {
    columnDefs: ledColumns(), rowData: ledgerRows(), getRowId: (p) => String(p.data.n),
    onGridSizeChanged: () => {}, // flex columns fill the width; fitting would squeeze them below their words
    overlayNoRowsTemplate: `<span class="g-empty">${NO_ROWS.led}</span>`,
  });
}

// A narrow schedule moves the due date, then the amount, under the milestone's name.
function fitSchedule(gridApi, width) {
  const host = document.querySelector("#msgrid");
  const noDue = width < 540, noAmount = width < 420;
  gridApi.setColumnsVisible(["due"], !noDue);
  gridApi.setColumnsVisible(["amount"], !noAmount);
  host.classList.toggle("no-due", noDue);
  host.classList.toggle("no-amount", noAmount);
}

function renderSchedule() {
  if (!AG) { renderTimeline(); return; }
  ensureGrids();
  GRIDS.ms.setGridOption("rowData", milestoneRows());
  const node = SEL && GRIDS.ms.getRowNode(SEL);
  if (node && !node.isSelected()) node.setSelected(true);
}

// Fallback without AG Grid: the schedule as a list of buttons.
function renderTimeline() {
  const s = W.schedule;
  $("#timeline").hidden = false;
  $("#timeline").replaceChildren(...s.milestones.map((m) => {
    const state = W.status[m.code] || "planned";
    return el("li", {}, el("button", { class: `ms ${SEL === m.code ? "sel" : ""}`, onclick: () => { SEL = m.code; render(); } },
      el("span", { class: "code" }, m.code),
      el("span", {}, el("strong", {}, m.name), el("div", { class: "when" }, `Due: ${m.due_as_written}`),
        ...m.issues.map((i) => el("div", { class: "issue" }, i))),
      el("span", { class: "msright" }, el("div", { class: "amt" }, money(m.currency, m.amount)),
        el("span", { class: `state ${state}` }, STATE_WORDS[state] || state))));
  }));
}

// Fallback without AG Grid: a plain table, values as text.
function plainTable(host, allCols, rows, empty) {
  if (!rows.length) { host.replaceChildren(el("p", { class: "hint" }, empty)); return; }
  const cols = allCols.filter((c) => !c.hide);
  const text = (c, r) => {
    const v = r[c.field];
    return c.valueFormatter ? c.valueFormatter({ value: v, data: r }) : (v ?? "");
  };
  host.replaceChildren(el("div", { class: "plain-wrap" }, el("table", { class: "plain" },
    el("thead", {}, el("tr", {}, ...cols.map((c) => el("th", { scope: "col" }, c.headerName)))),
    el("tbody", {}, ...rows.map((r) => el("tr", {}, ...cols.map((c) => el("td", {}, String(text(c, r))))))))));
}

function renderBooks() {
  const inv = invoiceRows(), led = ledgerRows();
  $("#tab-inv").textContent = `Invoices (${inv.length})`;
  $("#tab-led").textContent = `Ledger (${led.length})`;
  if (AG) {
    ensureGrids();
    GRIDS.inv.setGridOption("rowData", inv);
    GRIDS.led.setGridOption("rowData", led);
  } else {
    plainTable($("#invgrid"), invColumns().filter((c) => c.field !== "link"), inv, NO_ROWS.inv);
    plainTable($("#ledgrid"), ledColumns(), [...led].reverse(), NO_ROWS.led);
  }
}

const reducedMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
// Bring the invoices and ledger card to the top of the screen when the table is not fully
// on screen. The grid on a newly shown tab sizes its rows over a few frames, so this waits
// until the card's height has held still for three frames (at most about 40 frames).
function revealBooks() {
  const card = $("#books");
  let last = -1, still = 0, frames = 0;
  const step = () => {
    const h = card.offsetHeight;
    still = h === last ? still + 1 : 0;
    last = h;
    if (still < 3 && ++frames < 40) { requestAnimationFrame(step); return; }
    const r = card.getBoundingClientRect();
    if (r.top >= 0 && r.bottom <= window.innerHeight) return;
    card.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
  };
  requestAnimationFrame(step);
}

function showBooks(tab) {
  BOOKS_TAB = tab;
  for (const t of ["inv", "led"]) {
    const on = t === tab;
    $(`#tab-${t}`).setAttribute("aria-selected", on ? "true" : "false");
    $(`#tab-${t}`).tabIndex = on ? 0 : -1;
    $(`#${t}pane`).hidden = !on;
  }
}

// The ledger as CSV. Text that a spreadsheet would run as a formula is quoted.
function ledgerCsv() {
  if (GRIDS.led) GRIDS.led.exportDataAsCsv(ledgerCsvParams());
}
function ledgerCsvParams() {
  const ref = (W.schedule.contract_reference || "contract").replace(/[^A-Za-z0-9-]+/g, "-");
  return {
    fileName: `clause-and-pay-ledger-${ref}.csv`, allColumns: true,
    processCellCallback: (p) => {
      const field = p.column.getColDef().field;
      if (field === "amountNum") return p.value === null || p.value === undefined ? "" : Number(p.value).toFixed(2);
      if (field === "at" || field === "invoice") return p.value ?? "";
      const v = typeof p.formatValue === "function" ? p.formatValue(p.value) : p.value;
      const s = v === null || v === undefined ? "" : String(v);
      return /^[=+\-@\t\r]/.test(s) ? `'${s}` : s;
    },
  };
}

function renderLog() {
  $("#log").replaceChildren(...[...W.log].reverse().map((e) =>
    el("li", {}, el("time", { datetime: e.at }, localTime(e.at)), el("span", { class: e.kind }, e.text))));
}

// ---- the selected milestone: show the delivery, check it, invoice it ----

const EVIDENCE_TYPES = ".pdf,.png,.jpg,.jpeg,.docx,application/pdf,image/png,image/jpeg,application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const MAX_EVIDENCE = 8 * 1024 * 1024;
const SAMPLES = [
  { file: "m1-acceptance-email.pdf", label: "M1: client's acceptance email (PDF)", code: "M1" },
  { file: "m1-acceptance-certificate.png", label: "M1: signed certificate (image)", code: "M1" },
  { file: "m2-dataset-handover.docx", label: "M2: dataset handover note (Word)", code: "M2" },
];
let EVMODE = "file"; // how the delivery is shown: an evidence file, or a typed note
let OPEN_AGAIN = null; // the milestone whose "check different evidence" section is open
const CHECK_STEPS = [
  [0, "Reading the file..."],
  [2, "Checking it against the contract's acceptance criteria, item by item ({s} s)..."],
  [12, "Still checking ({s} s). Every quote is matched against the file's own text."],
  [30, "Taking longer than usual ({s} s). Large files and images take longest."],
];
const VERDICT_WORDS = { met: "Met", not_met: "Not met", not_shown: "Not shown", unchecked: "Confirm by eye" };
const ADVICE = "This check is advice, not a decision. The model can be wrong, and a file can be written to mislead it: a quote found in the file shows the words are there, not that they are true. Read the quotes, then decide.";
const QUOTE_WORDS = {
  verified: ["ok", "Found in the file"], moved: ["ok", "Found in the file"],
  not_found: ["bad", "Not found in the file"], too_short: ["bad", "Too short to check"],
  not_text_checked: ["warn", "Read from the image: not text-checked"],
};

function evidenceResult(check) {
  const met = check.criteria.filter((c) => c.verdict === "met").length;
  const unchecked = check.criteria.filter((c) => c.verdict === "unchecked").length;
  const f = check.file;
  const fileLine = `${f.name} (${f.label}${f.pages ? `, ${f.pages} page${f.pages === 1 ? "" : "s"}` : ""}, ${kb(f.size)})`
    + (check.evidence_kind ? `: ${check.evidence_kind}` : "");
  return el("div", { class: `result ${check.recommendation}` },
    el("strong", {}, check.recommendation === "send" ? "Ready to invoice" : "Hold the invoice"),
    el("p", { class: "filemeta" }, `Checked ${fileLine}. The file was read for this check and not kept.`),
    el("p", {}, check.summary),
    check.criteria.length ? el("p", { class: "count" }, `${met} of ${check.criteria.length} met`
      + (unchecked ? `, ${unchecked} to confirm by eye` : "")) : null,
    el("ol", { class: "items" }, ...check.criteria.map((c) => {
      const [qcls, qword] = QUOTE_WORDS[c.quote_status] || [];
      return el("li", {},
        el("div", { class: "itemhead" },
          el("span", { class: `verdict ${c.verdict}` }, VERDICT_WORDS[c.verdict] || c.verdict),
          el("span", {}, c.criterion, c.source === "condition" ? el("span", { class: "tag" }, "condition for invoicing") : null)),
        c.quote ? el("p", { class: "quote" }, `“${c.quote}”`) : null,
        c.quote ? el("span", { class: "cite" },
          f.text_checked && c.page ? el("span", { class: "page" }, `page ${c.page}`) : null,
          qword ? el("span", { class: `ver ${qcls}` }, qword) : null) : null,
        c.reason ? el("p", { class: "hint reason" }, c.reason) : null);
    })),
    el("p", { class: "advice" }, ADVICE));
}

function noteResult(check) {
  return el("div", { class: `result ${check.recommendation}` },
    el("strong", {}, check.recommendation === "send" ? "Ready to invoice" : "Hold the invoice"),
    el("p", { class: "filemeta" }, "Checked your delivery note."),
    el("p", {}, check.summary),
    el("ul", {}, ...check.criteria.map((c) => el("li", {}, `${c.met.toUpperCase()}: ${c.criterion}. ${c.evidence}`))),
    el("p", { class: "advice" }, ADVICE));
}

function evidenceForm(m, err) {
  const status = el("p", { class: "progress", role: "status", "aria-live": "polite" });
  const tabs = el("div", { class: "seg", role: "group", "aria-label": "How to show the delivery" },
    ...[["file", "Upload evidence"], ["note", "Type a note"]].map(([k, label]) =>
      el("button", { type: "button", class: `segbtn ${EVMODE === k ? "on" : ""}`, "aria-pressed": EVMODE === k ? "true" : "false",
        onclick: () => { EVMODE = k; renderPanel(); } }, label)));
  const nodes = [el("div", { class: "what" }, "Show the delivery"), tabs];

  if (EVMODE === "note") {
    const ev = el("textarea", { id: "evidence", placeholder: "What did you deliver, and has the client accepted it? e.g. 'Inception report sent 5 Oct. Survey method, 1,250 passenger sample, dated work plan for M2 to M4. Accepted in writing by Ada Brennan on 9 Oct.'" });
    const checkBtn = el("button", { class: "btn primary", disabled: !CFG.ai, onclick: async () => {
      err.textContent = ""; checkBtn.disabled = true;
      const stop = progress(status, CHECK_STEPS.slice(1));
      try { W = await post(`/api/contracts/${W.id}/milestones/${m.code}/deliver`, { evidence: ev.value }); stop(); OPEN_AGAIN = null; render(); }
      catch (e) { stop(); status.textContent = ""; err.textContent = e.message; checkBtn.disabled = false; }
    } }, "Mark delivered and check");
    nodes.push(el("label", { class: "f", for: "evidence" }, "Delivery note"), ev, el("div", { class: "row" }, checkBtn), status);
    return nodes;
  }

  const input = el("input", { type: "file", id: "evfile", accept: EVIDENCE_TYPES, class: "visually-hidden", disabled: !CFG.ai });
  const chosen = el("span", { class: "chosen" }, "No file chosen");
  const upload = async (file) => {
    err.textContent = "";
    if (!file) return;
    if (file.size > MAX_EVIDENCE) { err.textContent = "The evidence file is larger than 8 MB."; return; }
    chosen.textContent = file.name;
    const controls = [...tabs.querySelectorAll("button"), ...form.querySelectorAll("button"), input];
    for (const b of controls) b.disabled = true;
    pick.classList.add("disabled");
    const stop = progress(status, CHECK_STEPS);
    try {
      const fd = new FormData(); fd.append("file", file, file.name);
      W = await api(`/api/contracts/${W.id}/milestones/${m.code}/evidence`, { method: "POST", body: fd });
      stop(); OPEN_AGAIN = null; render();
    } catch (e) {
      stop(); status.textContent = ""; err.textContent = e.message;
      for (const b of controls) b.disabled = !CFG.ai;
      pick.classList.toggle("disabled", !CFG.ai);
      input.value = "";
    }
  };
  input.addEventListener("change", () => upload(input.files[0]));
  const pick = el("label", { class: `btn primary ${CFG.ai ? "" : "disabled"}`, for: "evfile" }, "Choose an evidence file");
  const samples = SAMPLES.slice().sort((a, b) => (b.code === m.code) - (a.code === m.code));
  const form = el("div", { class: "evform" },
    el("p", { class: "hint" }, "The client's acceptance email, a signed certificate or the deliverable itself: PDF, PNG, JPEG or Word (.docx), up to 8 MB. The agent checks it against this milestone's acceptance criteria and quotes the file's own words; words from a PDF or Word file are checked against its text, an image's are yours to confirm by eye. The file is read for the check, never stored or shown."),
    el("div", { class: "row" }, input, pick, chosen),
    el("div", { class: "samples" }, el("span", { class: "hint" }, "Or try a fictional sample:"),
      ...samples.map((s) => el("button", { type: "button", class: "chip", disabled: !CFG.ai, onclick: async () => {
        err.textContent = "";
        try {
          const r = await fetch(`/api/sample-evidence/${s.file}`);
          if (!r.ok) throw new Error("The sample could not be loaded.");
          const blob = await r.blob();
          upload(new File([blob], s.file, { type: blob.type }));
        } catch (e) { err.textContent = e.message; }
      } }, s.label))),
    status);
  if (!CFG.ai) form.append(el("p", { class: "hint" }, "Needs the AI model configured on this server."));
  nodes.push(form);
  return nodes;
}

function invoiceForm(m, state, err) {
  const email = el("input", { type: "email", id: "recipient", autocomplete: "off", placeholder: "client's PayPal email (sandbox buyer)" });
  const reason = el("textarea", { id: "reason", rows: "2", placeholder: "e.g. I read the signed certificate: it accepts M1 in writing on 9 October." });
  const reasonBox = el("div", { hidden: true }, el("label", { class: "f", for: "reason" }, "Why send it over the hold? (recorded in the ledger)"), reason);
  const override = state === "held" ? el("input", { type: "checkbox", id: "override" }) : null;
  const sync = () => {
    if (!override) return;
    reasonBox.hidden = !override.checked;
    send.disabled = !CFG.paypal || !override.checked || reason.value.trim().length < 10;
  };
  const send = el("button", { class: "btn primary", disabled: !CFG.paypal || state === "held", onclick: async () => {
    err.textContent = "";
    if (!email.value.includes("@")) { err.textContent = "Enter the client's email."; return; }
    send.disabled = true; send.textContent = "Creating the invoice in PayPal...";
    try {
      W = await post(`/api/contracts/${W.id}/milestones/${m.code}/invoice`,
        { recipient_email: email.value, approve: true, override_hold: !!(override && override.checked), override_reason: reason.value });
      render();
    } catch (e) { err.textContent = e.message; send.textContent = "Approve and send invoice"; sync(); if (!override) send.disabled = !CFG.paypal; }
  } }, "Approve and send invoice");
  if (override) { override.addEventListener("change", sync); reason.addEventListener("input", sync); }
  return [
    el("label", { class: "f", for: "recipient" }, "Send the invoice to"), email,
    override ? el("label", { class: "check", for: "override" }, override, el("span", {}, "Send anyway: I have read the evidence myself and take responsibility")) : null,
    reasonBox,
    el("div", { class: "row" }, send),
    !CFG.paypal && el("p", { class: "hint" }, "PayPal is not configured on this server, so invoices cannot be sent. Add sandbox keys (see the README)."),
  ];
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

  if (check) nodes.push(check.kind === "file" ? evidenceResult(check) : noteResult(check));
  if (!inv && (state === "delivered" || state === "held")) nodes.push(...invoiceForm(m, state, err));
  if (!inv && !check) nodes.push(...evidenceForm(m, err));
  // After a check, a new file or note can replace it; the form folds away until wanted.
  if (!inv && check) nodes.push(el("details", { class: "again", open: OPEN_AGAIN === m.code,
    ontoggle: (e) => { OPEN_AGAIN = e.currentTarget.open ? m.code : null; } },
    el("summary", {}, "Check different evidence (replaces this check)"), ...evidenceForm(m, err)));

  if (inv) {
    const refresh = el("button", { class: "btn", onclick: async () => {
      err.textContent = ""; busy(refresh, true, "Asking PayPal...");
      try { W = await post(`/api/contracts/${W.id}/milestones/${m.code}/refresh`); render(); }
      catch (e) { err.textContent = e.message; busy(refresh, false, "Check payment status"); }
    } }, "Check payment status");
    const today = el("input", { type: "date", id: "today" });
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
    const link = safeLink(inv.link);
    nodes.push(el("div", { class: "result send" },
      el("strong", {}, isPaid(inv.status) ? `Invoice ${inv.number}` : `Invoice ${inv.number}: ${ppWord(inv.status)}`),
      isPaid(inv.status) ? el("span", { class: "stamp", "aria-label": "Paid" }, "Paid") : null,
      el("p", {}, inv.due_date && !isPaid(inv.status) ? `Due ${longDate(inv.due_date)}` : ""),
      inv.override_reason ? el("p", { class: "hint" }, `Sent over the agent's hold. Your reason: ${inv.override_reason}`) : null,
      el("div", { class: "row links" },
        link && el("a", { class: "link", href: link, target: "_blank", rel: "noopener" }, "Open the invoice in PayPal"),
        el("a", { class: "link", href: "#books", onclick: (e) => { e.preventDefault(); showBooks("led"); revealBooks(); } },
          "See every event in the ledger"))),
      isPaid(inv.status) ? null : el("div", { class: "row" }, refresh, OPEN_STATES.includes(inv.status) ? el("button", { class: "btn", onclick: async (ev) => {
        err.textContent = ""; ev.currentTarget.disabled = true; ev.currentTarget.textContent = "Recording in the PayPal sandbox...";
        const b = ev.currentTarget;
        try { W = await post(`/api/contracts/${W.id}/milestones/${m.code}/sandbox-payment`); render(); }
        catch (e) { err.textContent = e.message; b.disabled = false; b.textContent = "Sandbox: record the client's payment"; }
      } }, "Sandbox: record the client's payment") : null),
      OPEN_STATES.includes(inv.status) ? el("p", { class: "hint" }, "Demo only: logs the client's bank transfer in PayPal's sandbox, so you can watch PayPal confirm the payment here.") : null,
      ...(isPaid(inv.status) ? [] : [
        el("label", { class: "f", for: "today" }, "Overdue? Draft a reminder that quotes the contract. Optional: pretend today is"),
        today, el("div", { class: "row" }, draft, sendRem), preview]));
  }
  nodes.push(err);
  p.replaceChildren(...nodes.filter(Boolean));
}

// Ask the billing agent: Gemini answers from the contract and looks invoices up
// with PayPal's Agent Toolkit. A reminder it wants to send is held as a
// proposal until the user approves it here.
const AGENT = { q: "", busy: false, answer: null, trace: [], err: "", secs: 0, t0: 0 };
const AGENT_TIMEOUT_MS = 130000; // the server gives up at 120 s
const AGENT_STEPS = [
  [0, "Reading your question and the contract..."],
  [3, "Looking invoices up in PayPal through the Agent Toolkit, where the answer needs them ({s} s)..."],
  [15, "Still working ({s} s). The model and several PayPal look-ups can take up to a minute."],
  [50, "Taking longer than usual ({s} s). You can wait, or ask a narrower question."],
];
const EXAMPLES = [
  "Which of my invoices are still unpaid?",
  "What interest can I charge if M1 is paid late?",
  "Draft a payment reminder for M1 that quotes the contract.",
];
let AGENT_STOP = null;
function renderAgent() {
  const box = $("#agent");
  const q = el("textarea", { id: "ask", rows: "2", placeholder: "Ask about this contract and its PayPal invoices", "aria-label": "Your question" });
  q.value = AGENT.q;
  q.addEventListener("input", () => { AGENT.q = q.value; });
  const ready = CFG.paypal && CFG.ai;
  const status = el("p", { class: "progress", id: "agentprogress", role: "status", "aria-live": "polite" });
  const go = el("button", { class: "btn primary", disabled: AGENT.busy || !ready, onclick: async () => {
    if (!AGENT.q.trim() || AGENT.busy) return;
    AGENT.busy = true; AGENT.err = ""; AGENT.answer = null; AGENT.t0 = Date.now(); renderAgent();
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), AGENT_TIMEOUT_MS);
    try {
      const r = await post(`/api/contracts/${W.id}/ask`, { question: AGENT.q }, { signal: ctl.signal });
      AGENT.answer = r.answer; AGENT.trace = r.trace || []; W = r.workspace;
    } catch (e) {
      AGENT.err = e.name === "AbortError" ? "The billing agent took too long. Try again, or ask a narrower question." : e.message;
    } finally {
      clearTimeout(timer);
      if (AGENT_STOP) AGENT_STOP();
      AGENT_STOP = null; AGENT.busy = false; AGENT.secs = Math.max(1, Math.round((Date.now() - AGENT.t0) / 1000));
      render();
    }
  } }, AGENT.busy ? "Working..." : "Ask");
  const examples = el("div", { class: "examples" }, ...EXAMPLES.map((t) =>
    el("button", { class: "chip", type: "button", disabled: AGENT.busy, onclick: () => { AGENT.q = t; renderAgent(); $("#ask").focus(); } }, t)));
  const nodes = [
    el("h2", {}, "Ask the billing agent"),
    el("p", { class: "hint" }, "Answers come from the contract, with pages, and from PayPal through its Agent Toolkit. Anything the agent wants to send waits for your approval."),
    q, examples, el("div", { class: "row" }, go), status,
    !ready && el("p", { class: "hint" }, "Needs PayPal and the AI configured on this server."),
  ];
  if (AGENT.err) nodes.push(el("p", { class: "err", role: "alert" }, AGENT.err));
  if (AGENT.answer) {
    nodes.push(el("div", { class: "answer" },
      el("p", {}, AGENT.answer),
      el("div", { class: "tools" },
        AGENT.trace.length ? el("span", { class: "hint" }, "PayPal Agent Toolkit:") : null,
        ...AGENT.trace.map((t) => el("span", { class: "badge on" }, t.tool)),
        AGENT.secs ? el("span", { class: "hint" }, `answered in ${AGENT.secs} s`) : null)));
  }
  for (const p of Object.values(W.proposals || {})) {
    nodes.push(el("div", { class: "result hold proposal" },
      el("strong", {}, "Waiting for your approval"),
      el("p", {}, p.summary),
      p.args.subject ? el("p", { class: "hint" }, `Subject: ${p.args.subject}`) : null,
      p.args.note ? el("div", { class: "preview" }, p.args.note) : null,
      el("div", { class: "row" },
        el("button", { class: "btn primary", onclick: async (ev) => {
          const b = ev.currentTarget; b.disabled = true; b.textContent = "Sending through PayPal...";
          try { W = await post(`/api/contracts/${W.id}/proposals/${p.id}/approve`); render(); }
          catch (e) { AGENT.err = e.message; renderAgent(); }
        } }, "Approve and send"),
        el("button", { class: "btn", onclick: async () => {
          try { W = await post(`/api/contracts/${W.id}/proposals/${p.id}/dismiss`); render(); } catch (e) { AGENT.err = e.message; renderAgent(); }
        } }, "Dismiss"))));
  }
  box.replaceChildren(...nodes.filter(Boolean));
  // While the agent works, the message keeps counting from the moment the question was asked,
  // also when the box is redrawn in the meantime.
  if (AGENT_STOP) { AGENT_STOP(); AGENT_STOP = null; }
  if (AGENT.busy) AGENT_STOP = progress(status, AGENT_STEPS, AGENT.t0);
}

function render() {
  if (!W) return;
  $("#start").hidden = true; $("#contract").hidden = false;
  if (!SEL && W.schedule.milestones.length) SEL = W.schedule.milestones[0].code;
  renderSummary(); renderSchedule(); renderPanel(); renderAgent(); renderBooks(); renderLog();
  watchPayments();
}

// While an invoice is open in PayPal, ask the server every 10 s whether the
// PayPal webhook has marked it paid, so the page updates without a click. The
// page is redrawn only when something changed, so typing is not interrupted.
const OPEN_STATES = ["SENT", "UNPAID", "PARTIALLY_PAID", "SCHEDULED"];
let POLL = null;
const fingerprint = (w) => JSON.stringify([w.status, Object.values(w.invoices).map((i) => i.status), w.log.length, (w.ledger || []).length]);
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

async function start(promise, name = "the contract") {
  const msg = $("#startmsg");
  $("#sample").disabled = true;
  const stop = progress(msg, [[0, `Reading ${name} and checking every quote against its page...`],
    [8, `Reading ${name} and checking every quote against its page ({s} s)...`],
    [25, `Still reading ({s} s). A long contract takes up to a minute.`]]);
  try { W = await promise; stop(); remember(); render(); }
  catch (e) { stop(); msg.textContent = e.message; }
  finally { $("#sample").disabled = false; }
}

// The contract's id goes in the address, so a reload (or a second tab) opens the same contract.
function remember() { history.replaceState(null, "", `#${W.id}`); }
async function reopen() {
  const id = location.hash.slice(1);
  if (!/^[0-9a-f]{12}$/.test(id)) return;
  try { W = await api(`/api/contracts/${id}`); render(); }
  catch { history.replaceState(null, "", location.pathname); $("#startmsg").textContent = "That contract is no longer on the server (the demo keeps contracts in memory). Read it again."; }
}

$("#sample").addEventListener("click", () => start(post("/api/contracts/sample")));
$("#file").addEventListener("change", (ev) => {
  const f = ev.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  start(api("/api/contracts", { method: "POST", body: fd }), f.name);
});
$("#tab-inv").addEventListener("click", () => { showBooks("inv"); revealBooks(); });
$("#tab-led").addEventListener("click", () => { showBooks("led"); revealBooks(); });
$("#books [role=tablist]").addEventListener("keydown", (e) => {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  const next = BOOKS_TAB === "inv" ? "led" : "inv"; showBooks(next); $(`#tab-${next}`).focus();
});
$("#ledsearch").addEventListener("input", (e) => { if (GRIDS.led) GRIDS.led.setGridOption("quickFilterText", e.target.value); });
$("#ledcsv").addEventListener("click", ledgerCsv);
if (!AG) { $("#ledsearch").hidden = true; $("#ledcsv").hidden = true; }

api("/api/config").then((c) => { CFG = c; renderStatus(); if (W) render(); }).catch(() => renderStatus()).finally(reopen);
