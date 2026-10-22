"""Clause & Pay web app: one page, a small JSON API, state kept in memory per contract."""
from __future__ import annotations

import json
import logging
import asyncio
import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

import time
from collections import defaultdict, deque

import httpx

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, billing_agent, evidence
from .contract_reader import read_contract
from .llm import ai_configured, model_id
from .paypal_client import PayPalClient, PayPalError, paypal_configured
from .schedule import Schedule

ROOT = Path(__file__).resolve().parent
SAMPLE = ROOT.parent / "samples" / "northwind-harbour-services-agreement.pdf"
SAMPLE_EVIDENCE = ROOT.parent / "samples" / "evidence"
MAX_UPLOAD = 15 * 1024 * 1024
AGENT_TIMEOUT = float(os.environ.get("AGENT_TIMEOUT_SECONDS", "120"))
CHECK_TIMEOUT = float(os.environ.get("CHECK_TIMEOUT_SECONDS", "90"))
MIN_OVERRIDE_REASON = 10

log = logging.getLogger("clausepay")
app = FastAPI(title="Clause & Pay")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class Workspace(BaseModel):
    id: str
    filename: str
    schedule: Schedule
    status: dict[str, str] = {}  # milestone code -> planned | delivered | held | invoiced | paid
    checks: dict[str, dict] = {}
    invoices: dict[str, dict] = {}  # milestone code -> {id, number, status, due_date, link}
    proposals: dict[str, dict] = {}  # billing agent actions waiting for the user's approval
    log: list[dict] = []
    ledger: list[dict] = []  # every invoice event: drafted, sent, reminded, paid... with amounts and dates

    def note(self, text: str, kind: str = "info") -> None:
        self.log.append({"at": _now(), "kind": kind, "text": text})

    def record(self, event: str, code: str, *, source: str, rec: Optional[dict] = None,
               amount: Optional[str] = None, detail: str = "", at: Optional[str] = None) -> dict:
        """Add one invoice event to the ledger. `rec` is the milestone's invoice record."""
        rec = rec or self.invoices.get(code) or {}
        entry = {"at": at or _now(), "event": event, "code": code, "invoice": rec.get("number"),
                 "invoice_id": rec.get("id"), "amount": amount if amount is not None else rec.get("amount"),
                 "currency": rec.get("currency"), "source": source, "detail": detail}
        self.ledger.append(entry)
        return entry


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _long(iso: str) -> str:
    """2026-11-21 -> 21 November 2026 (as written on the page)."""
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    return f"{d.day} {d:%B %Y}"


STORE: dict[str, Workspace] = {}
MAX_WORKSPACES = int(os.environ.get("MAX_WORKSPACES", "300"))

# Usage limits for a public demo (per visitor IP, rolling hour; and per day for
# the whole server). Each AI call, each PayPal send and each evidence upload counts once.
LIMITS = {
    "ai": (int(os.environ.get("AI_CALLS_PER_HOUR_PER_IP", "20")), 3600),
    "paypal": (int(os.environ.get("PAYPAL_SENDS_PER_HOUR_PER_IP", "15")), 3600),
    # counted before an upload is even parsed, so refused files cannot be sent without end
    "upload": (int(os.environ.get("EVIDENCE_UPLOADS_PER_HOUR_PER_IP", "30")), 3600),
}
DAILY_CAPS = {
    "ai": (int(os.environ.get("AI_CALLS_PER_DAY", "300")), "The demo has reached today's limit for AI reads. It resets tomorrow."),
    "paypal": (int(os.environ.get("PAYPAL_SENDS_PER_DAY", "200")), "The demo has reached today's limit for PayPal sends. It resets tomorrow."),
}
# How many proxies in front of the app append to X-Forwarded-For. On Cloud Run, Google's
# front end appends the address it saw as the last entry; everything before it is
# whatever the client sent, so it is never trusted.
TRUSTED_PROXY_HOPS = int(os.environ.get("TRUSTED_PROXY_HOPS", "1"))
_hits: dict[tuple[str, str], deque] = defaultdict(deque)
_daily: dict[tuple[str, str], int] = defaultdict(int)


def client_ip(request: Request) -> str:
    fwd = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if fwd and TRUSTED_PROXY_HOPS > 0:
        return fwd[-min(TRUSTED_PROXY_HOPS, len(fwd))]
    return request.client.host if request.client else "unknown"


def spend(request: Request, kind: str) -> None:
    """Count one use of `kind` for this visitor, or refuse with 429."""
    limit, window = LIMITS[kind]
    now = time.time()
    q = _hits[(client_ip(request), kind)]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "This demo limits how often one visitor can run that step. Try again in a little while.")
    if kind in DAILY_CAPS:
        cap, message = DAILY_CAPS[kind]
        day = (date.today().isoformat(), kind)
        if _daily[day] >= cap:
            raise HTTPException(429, message)
        _daily[day] += 1
    q.append(now)


def keep(w: "Workspace") -> None:
    """Store a workspace, dropping the oldest once the demo holds too many."""
    STORE[w.id] = w
    while len(STORE) > MAX_WORKSPACES:
        STORE.pop(next(iter(STORE)))
_paypal: Optional[PayPalClient] = None


def paypal() -> PayPalClient:
    global _paypal
    if not paypal_configured():
        raise HTTPException(409, "PayPal is not configured on this server: set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET (sandbox).")
    if _paypal is None:
        _paypal = PayPalClient.from_env()
    return _paypal


def ws(cid: str) -> Workspace:
    w = STORE.get(cid)
    if not w:
        raise HTTPException(404, "Unknown contract. Read it again.")
    return w


def milestone(w: Workspace, code: str):
    for m in w.schedule.milestones:
        if m.code == code:
            return m
    raise HTTPException(404, f"No milestone {code} in this contract.")


def recipient_link(inv: dict) -> Optional[str]:
    """The page where the client views and pays the invoice. PayPal v2 puts it
    in detail.metadata.recipient_view_url (the links array has no payer view)."""
    url = ((inv.get("detail") or {}).get("metadata") or {}).get("recipient_view_url")
    if url:
        return url
    return next((l["href"] for l in inv.get("links", []) if l.get("rel") in ("payer-view", "recipient_view")), None)


# GET and HEAD: link checkers and preview bots ask with HEAD first.
@app.api_route("/", methods=["GET", "HEAD"])
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/config")
async def config():
    return {"paypal": paypal_configured(), "ai": ai_configured(), "model": model_id(),
            "environment": "live" if os.environ.get("PAYPAL_ENV", "sandbox").lower() == "live" else "sandbox"}


async def _read(data: bytes, filename: str) -> dict:
    if not ai_configured():
        raise HTTPException(409, "The AI model is not configured: set GEMINI_API_KEY.")
    try:
        sched = await read_contract(data)
    except ValueError as e:
        raise HTTPException(422, str(e))
    w = Workspace(id=uuid.uuid4().hex[:12], filename=filename, schedule=sched,
                  status={m.code: "planned" for m in sched.milestones})
    st = sched.stats()
    w.note(f"Read {filename}: {st['milestones']} milestones, {st['verified']} of {st['quotes']} quotes found on their page.")
    for issue in sched.issues:
        w.note(issue, "warn")
    keep(w)
    return w.model_dump()


@app.post("/api/contracts")
async def upload(request: Request, file: UploadFile = File(...)):
    spend(request, "ai")
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "The file is larger than 15 MB.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(415, "Upload a PDF.")
    return await _read(data, file.filename or "contract.pdf")


_sample_sched: Optional[Schedule] = None


@app.post("/api/contracts/sample")
async def sample(request: Request):
    """The sample is read by the model once per server start; every visitor
    then gets their own workspace from that reading (no AI call each time)."""
    global _sample_sched
    if _sample_sched is None:
        spend(request, "ai")
        out = await _read(SAMPLE.read_bytes(), SAMPLE.name)
        _sample_sched = Schedule.model_validate(out["schedule"])
        return out
    sched = _sample_sched.model_copy(deep=True)
    w = Workspace(id=uuid.uuid4().hex[:12], filename=SAMPLE.name, schedule=sched,
                  status={m.code: "planned" for m in sched.milestones})
    st = sched.stats()
    w.note(f"Read {SAMPLE.name}: {st['milestones']} milestones, {st['verified']} of {st['quotes']} quotes found on their page.")
    for issue in sched.issues:
        w.note(issue, "warn")
    keep(w)
    return w.model_dump()


@app.get("/api/contracts/{cid}")
async def get_contract(cid: str):
    return ws(cid).model_dump()


@app.get("/api/sample.pdf")
async def sample_pdf():
    return FileResponse(SAMPLE, media_type="application/pdf")


class Delivery(BaseModel):
    evidence: str = ""


def not_invoiced(w: Workspace, code: str) -> None:
    if code in w.invoices:
        raise HTTPException(409, f"{code} already has invoice {w.invoices[code]['number']}; its delivery is settled.")


async def _timed(coro, seconds: float, message: str):
    """Await a model call, or answer 504 with a plain message when it takes too long."""
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError:
        raise HTTPException(504, message)


@app.post("/api/contracts/{cid}/milestones/{code}/deliver")
async def deliver(cid: str, code: str, body: Delivery, request: Request):
    w = ws(cid)
    m = milestone(w, code)
    not_invoiced(w, code)
    spend(request, "ai")
    check = await _timed(agent.check_delivery(w.schedule, m, body.evidence), CHECK_TIMEOUT,
                         "The delivery check took too long. Try again.")
    not_invoiced(w, code)  # an invoice sent while the check ran is not undone by its result
    w.checks[code] = {**check.model_dump(), "kind": "note"}
    w.status[code] = "delivered" if check.recommendation == "send" else "held"
    w.note(f"{code} marked delivered. Agent recommends {check.recommendation.upper()}: {check.summary}",
           "ok" if check.recommendation == "send" else "warn")
    return w.model_dump()


@app.post("/api/contracts/{cid}/milestones/{code}/evidence")
async def deliver_file(cid: str, code: str, request: Request):
    """Delivery proof as a file (PDF, PNG, JPEG or .docx): Gemini checks it against the
    milestone's quoted acceptance criteria, item by item, quoting the file's own words.
    The file is read and dropped; it is never stored, served back or rendered."""
    w = ws(cid)
    m = milestone(w, code)
    not_invoiced(w, code)
    # Refuse an oversized upload from its header, before the body is parsed.
    try:
        length = int(request.headers.get("content-length", ""))
    except ValueError:
        raise HTTPException(411, "Send the file with its length.")
    if length > evidence.MAX_EVIDENCE_BYTES + 64 * 1024:
        raise HTTPException(413, f"The evidence file is larger than {evidence.MAX_EVIDENCE_BYTES // (1024 * 1024)} MB.")
    spend(request, "upload")
    try:
        form = await request.form(max_files=1, max_fields=1)
    except Exception:
        raise HTTPException(400, "Send one evidence file.")
    up = form.get("file")
    if up is None or isinstance(up, str):
        raise HTTPException(400, "Send one evidence file.")
    data = await up.read(evidence.MAX_EVIDENCE_BYTES + 1)
    try:  # opening and reading the file is CPU work: off the event loop
        ev = await asyncio.to_thread(evidence.load_evidence, data, up.filename)
    except evidence.EvidenceError as e:
        raise HTTPException(e.status, e.message)
    finally:
        await form.close()
    if not ai_configured():
        raise HTTPException(409, "The AI model is not configured on this server.")
    spend(request, "ai")
    try:
        check = await _timed(evidence.check_evidence(w.schedule, m, ev), CHECK_TIMEOUT,
                             "The evidence check took too long. Try again, or try a smaller file.")
    except HTTPException:
        raise
    except Exception as e:
        log.warning("evidence check failed for %s: %s", code, str(e)[:200])
        raise HTTPException(502, "The model could not read that file just now. Try again, or try a PDF.")
    not_invoiced(w, code)  # an invoice sent while the check ran is not undone by its result
    w.checks[code] = check.model_dump()
    w.status[code] = "delivered" if check.recommendation == "send" else "held"
    met = sum(1 for c in check.criteria if c.verdict == "met")
    unchecked = sum(1 for c in check.criteria if c.verdict == "unchecked")
    w.note(f"{code}: evidence file {ev.name} checked, {met} of {len(check.criteria)} items met"
           + (f", {unchecked} to confirm by eye" if unchecked else "") + ". "
           f"Agent recommends {check.recommendation.upper()}.", "ok" if check.recommendation == "send" else "warn")
    return w.model_dump()


@app.get("/api/sample-evidence/{name}")
async def sample_evidence(name: str):
    """The fictional evidence files in samples/evidence, for trying the check."""
    files = {p.name: p for p in SAMPLE_EVIDENCE.glob("*") if p.suffix in (".pdf", ".png", ".docx")}
    p = files.get(name)
    if not p:
        raise HTTPException(404, "No such sample.")
    media = {".pdf": "application/pdf", ".png": "image/png", ".docx": evidence.DOCX_MIME}[p.suffix]
    return FileResponse(p, media_type=media, filename=p.name)


class InvoiceRequest(BaseModel):
    recipient_email: str
    approve: bool = False
    override_hold: bool = False
    override_reason: str = ""


@app.post("/api/contracts/{cid}/milestones/{code}/invoice")
async def invoice(cid: str, code: str, body: InvoiceRequest, request: Request):
    w = ws(cid)
    m = milestone(w, code)
    if not body.approve:
        raise HTTPException(400, "Sending an invoice needs your approval.")
    if code in w.invoices:
        raise HTTPException(409, f"{code} already has invoice {w.invoices[code]['number']}.")
    state = w.status.get(code)
    if state not in ("delivered", "held"):
        raise HTTPException(409, "Check the delivery against the contract before invoicing.")
    reason = " ".join(body.override_reason.split())[:500]
    overridden = state == "held"
    if overridden and not body.override_hold:
        raise HTTPException(409, "The agent recommended holding this invoice. Confirm the override to send it anyway.")
    if overridden and len(reason) < MIN_OVERRIDE_REASON:
        raise HTTPException(422, "Say why you are sending it over the hold; the reason goes in the ledger.")
    if not m.amount:
        raise HTTPException(422, f"{code} has no readable amount; it cannot be invoiced.")
    pp = paypal()
    spend(request, "paypal")
    amount, rec = str(Decimal(m.amount).quantize(Decimal("0.01"))), None
    try:
        number = await pp.next_invoice_number()
        payload = agent.invoice_payload(w.schedule, m, invoice_number=number,
                                        recipient_email=body.recipient_email, invoice_date=date.today(),
                                        invoicer_email=os.environ.get("PAYPAL_INVOICER_EMAIL") or None)
        inv = await pp.create_draft(payload)
        drafted_at = _now()
        rec = {"id": inv["id"], "number": number, "status": inv.get("status"), "amount": amount,
               "currency": m.currency, "invoice_date": payload["detail"]["invoice_date"],
               "recipient": body.recipient_email, "reminders": 0}
        subject = f"{w.schedule.contract_reference or w.schedule.contract_title}: invoice for {m.code}"
        await pp.send(inv["id"], subject=subject)
        inv = await pp.get(inv["id"])
    except PayPalError as e:
        left = f" Draft {rec['number']} stays in PayPal, unsent." if rec else ""
        w.note(f"PayPal refused the invoice for {code}: {e.message}.{left}", "error")
        raise HTTPException(502, f"PayPal: {e.message}")
    except httpx.HTTPError:
        left = f" Draft {rec['number']} stays in PayPal, unsent." if rec else ""
        w.note(f"PayPal could not be reached while invoicing {code}.{left}", "error")
        raise HTTPException(502, "PayPal could not be reached. Try again.")
    rec.update(status=inv.get("status"), link=recipient_link(inv), sent_at=_now(),
               due_date=(inv.get("detail", {}).get("payment_term", {}) or {}).get("due_date"))
    # The ledger gets the invoice's events only once it is sent and recorded, so a
    # failed attempt and its retry never leave two sets of entries.
    if overridden:
        rec["override_reason"] = reason
        w.record("override", code, rec=rec, source="You", at=drafted_at,
                 detail=f"Invoiced over the agent's hold. Reason: {reason}")
    w.invoices[code] = rec
    w.record("drafted", code, source="App: PayPal Invoicing API (create draft)", at=drafted_at,
             detail=f"Draft {number} created for {body.recipient_email}.")
    w.record("sent", code, source="App: PayPal Invoicing API (send)",
             detail=f"Sent to {body.recipient_email}" + (f", due {_long(rec['due_date'])}." if rec.get("due_date") else "."))
    w.status[code] = "invoiced"
    w.note(f"Invoice {number} for {code} sent through PayPal ({m.currency} {Decimal(m.amount):,.2f})"
           + (f", over the agent's hold. Reason: {reason}" if overridden else "."), "warn" if overridden else "ok")
    return w.model_dump()


def mark_paid(w: Workspace, code: str, rec: dict, *, source: str, amount: Optional[str] = None, detail: str = "") -> bool:
    """Mark a milestone paid once; the ledger gets one paid event however the news arrives."""
    if w.status.get(code) == "paid":
        return False
    w.status[code] = "paid"
    rec["paid_at"] = _now()
    w.record("paid", code, rec=rec, source=source, amount=amount, detail=detail)
    return True


@app.post("/api/contracts/{cid}/milestones/{code}/refresh")
async def refresh(cid: str, code: str):
    w = ws(cid)
    rec = w.invoices.get(code)
    if not rec:
        raise HTTPException(404, f"{code} has no invoice yet.")
    try:
        inv = await paypal().get(rec["id"])
    except PayPalError as e:
        raise HTTPException(502, f"PayPal: {e.message}")
    rec["status"] = inv.get("status")
    rec["link"] = rec.get("link") or recipient_link(inv)
    rec["due_date"] = (inv.get("detail", {}).get("payment_term", {}) or {}).get("due_date") or rec.get("due_date")
    if rec["status"] in ("PAID", "MARKED_AS_PAID"):
        paid = ((inv.get("payments") or {}).get("paid_amount") or {}).get("value")
        if mark_paid(w, code, rec, source="App: PayPal invoice status (Check payment status)", amount=paid,
                     detail=f"PayPal status {rec['status']}."):
            w.note(f"PayPal reports invoice {rec['number']} paid. {code} marked paid.", "ok")
    elif rec["status"] == "CANCELLED" and not any(e["event"] == "cancelled" and e["code"] == code for e in w.ledger):
        w.record("cancelled", code, rec=rec, source="App: PayPal invoice status (Check payment status)")
    return w.model_dump()


class ReminderRequest(BaseModel):
    approve: bool = False
    today: Optional[str] = None  # for demos: pretend today is this date (YYYY-MM-DD)


@app.post("/api/contracts/{cid}/milestones/{code}/reminder")
async def reminder(cid: str, code: str, body: ReminderRequest, request: Request):
    w = ws(cid)
    m = milestone(w, code)
    rec = w.invoices.get(code)
    if not rec:
        raise HTTPException(404, f"{code} has no invoice yet.")
    today = date.fromisoformat(body.today) if body.today else date.today()
    due = date.fromisoformat(rec["due_date"]) if rec.get("due_date") else today
    subject, note = agent.reminder_text(w.schedule, m, invoice_number=rec["number"], due=due, today=today,
                                        monthly_rate_pct=agent.monthly_rate_from(w.schedule))
    if not body.approve:
        return {"subject": subject, "note": note, "days_overdue": agent.days_overdue(due, today)}
    spend(request, "paypal")
    try:
        await paypal().remind(rec["id"], subject=subject, note=note)
    except PayPalError as e:
        raise HTTPException(502, f"PayPal: {e.message}")
    rec["reminders"] = rec.get("reminders", 0) + 1
    rec["last_reminder_at"] = _now()
    w.record("reminded", code, rec=rec, source="App: PayPal Invoicing API (remind), approved by you",
             amount=rec.get("amount"), detail=subject)
    w.note(f"Reminder sent for invoice {rec['number']} ({code}).", "ok")
    return {"subject": subject, "note": note, "sent": True, "workspace": w.model_dump()}


# ---- PayPal webhook: invoices paid on PayPal mark their milestone paid ----
PAID_STATES = ("PAID", "MARKED_AS_PAID")
_seen_events: deque = deque(maxlen=500)


def _event_invoice(event: dict) -> tuple[Optional[str], Optional[str]]:
    inv = _event_resource(event)
    return inv.get("id"), inv.get("status")


def _event_resource(event: dict) -> dict:
    res = event.get("resource") or {}
    return res.get("invoice") if isinstance(res.get("invoice"), dict) else res


def _event_paid_amount(event: dict) -> Optional[str]:
    inv = _event_resource(event)
    return (((inv.get("payments") or {}).get("paid_amount") or {}).get("value")
            or (inv.get("amount") or {}).get("value"))


def apply_invoice_event(event: dict) -> Optional[str]:
    """Apply a verified invoicing event to the workspace that holds the invoice.
    Returns the milestone code it matched, or None. Safe to apply twice."""
    etype = event.get("event_type", "")
    inv_id, status = _event_invoice(event)
    if not inv_id:
        return None
    paid = etype == "INVOICING.INVOICE.PAID" or (etype == "INVOICING.INVOICE.UPDATED" and status in PAID_STATES)
    for w in STORE.values():
        for code, rec in w.invoices.items():
            if rec.get("id") != inv_id:
                continue
            if paid:
                rec["status"] = status if status in PAID_STATES else "PAID"
                if mark_paid(w, code, rec, source=f"PayPal webhook {etype} (signature verified)",
                             amount=_event_paid_amount(event), detail=f"Event {event.get('id')}"):
                    w.note(f"PayPal webhook {etype} (signature verified): invoice {rec['number']} paid. {code} marked paid.", "ok")
            elif etype == "INVOICING.INVOICE.CANCELLED":
                if rec.get("status") != "CANCELLED":
                    rec["status"] = "CANCELLED"
                    w.record("cancelled", code, rec=rec, source=f"PayPal webhook {etype} (signature verified)",
                             detail=f"Event {event.get('id')}")
                    w.note(f"Invoice {rec['number']} for {code} was cancelled in PayPal.", "warn")
            elif status:
                rec["status"] = status
            return code
    return None


@app.post("/api/paypal/webhook")
async def paypal_webhook(request: Request):
    raw = await request.body()
    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Not a JSON event.")
    webhook_id = os.environ.get("PAYPAL_WEBHOOK_ID")
    if not webhook_id:
        raise HTTPException(503, "The PayPal webhook is not configured on this server.")
    try:
        ok = await paypal().verify_webhook(headers=dict(request.headers), raw_event=raw, webhook_id=webhook_id)
    except PayPalError:
        ok = False
    if not ok:
        log.warning("PayPal webhook rejected: event %s (%s) did not verify", event.get("id"), event.get("event_type"))
        raise HTTPException(400, "The webhook signature did not verify.")
    if event.get("id") in _seen_events:
        return {"ok": True, "duplicate": True}
    _seen_events.append(event.get("id"))
    code = apply_invoice_event(event)
    log.info("PayPal webhook %s (%s) matched %s", event.get("id"), event.get("event_type"), code)
    return {"ok": True, "matched": code}


class Question(BaseModel):
    question: str


@app.post("/api/contracts/{cid}/ask")
async def ask_agent(cid: str, body: Question, request: Request):
    """Ask the billing agent (Gemini + PayPal Agent Toolkit tools)."""
    w = ws(cid)
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "Ask a question.")
    if len(q) > 600:
        raise HTTPException(400, "Keep the question under 600 characters.")
    paypal()  # a clear 409 when PayPal is not configured
    spend(request, "ai")
    ctx = billing_agent.contract_context(w.schedule, w.invoices, w.status, date.today())
    try:
        out = await asyncio.wait_for(billing_agent.ask(q, ctx, allowed={i["id"] for i in w.invoices.values()}),
                                     timeout=AGENT_TIMEOUT)
    except asyncio.TimeoutError:
        w.note("The billing agent took too long to answer.", "error")
        raise HTTPException(504, "The billing agent took too long. Try a narrower question.")
    except Exception as e:
        w.note(f"The billing agent could not answer: {str(e)[:160]}", "error")
        raise HTTPException(502, "The billing agent could not answer just now. Try again.")
    for prop in out["proposals"]:
        iid = prop["args"].get("invoice_id")
        code = next((c for c, i in w.invoices.items() if i["id"] == iid), None)
        label = f"invoice {w.invoices[code]['number']} ({code})" if code else "the invoice"
        prop["summary"] = f"Send a PayPal reminder for {label}"
        w.proposals[prop["id"]] = prop
    used = ", ".join(sorted({t["tool"] for t in out["trace"]})) or "no PayPal tools"
    w.note(f"Billing agent answered \"{q[:80]}\" using {used}.")
    return {"answer": out["answer"], "trace": out["trace"], "proposals": out["proposals"], "workspace": w.model_dump()}


@app.post("/api/contracts/{cid}/proposals/{pid}/approve")
async def approve_proposal(cid: str, pid: str, request: Request):
    w = ws(cid)
    prop = w.proposals.get(pid)
    if not prop:
        raise HTTPException(404, "That action is no longer waiting for approval.")
    paypal()
    spend(request, "paypal")
    try:
        await billing_agent.approve(prop)
    except Exception as e:
        w.note(f"PayPal refused the agent's {prop['method']}: {str(e)[:160]}", "error")
        raise HTTPException(502, "PayPal refused that action.")
    w.proposals.pop(pid, None)
    iid = prop["args"].get("invoice_id")
    code = next((c for c, i in w.invoices.items() if i["id"] == iid), None)
    if code:
        rec = w.invoices[code]
        rec["reminders"] = rec.get("reminders", 0) + 1
        rec["last_reminder_at"] = _now()
        w.record("reminded", code, rec=rec, source="Billing agent proposal, approved by you; sent with the PayPal Agent Toolkit (send_invoice_reminder)",
                 detail=str(prop["args"].get("subject") or "")[:200])
    w.note(f"Approved and sent through the PayPal Agent Toolkit: {prop['summary']}.", "ok")
    return w.model_dump()


@app.post("/api/contracts/{cid}/proposals/{pid}/dismiss")
async def dismiss_proposal(cid: str, pid: str):
    w = ws(cid)
    if w.proposals.pop(pid, None):
        w.note("Dismissed an action the billing agent proposed.")
    return w.model_dump()


@app.post("/api/contracts/{cid}/milestones/{code}/sandbox-payment")
async def sandbox_payment(cid: str, code: str, request: Request):
    """Demo only: log the client's bank transfer against the invoice in PayPal's
    sandbox, with the Agent Toolkit's record_payment_for_invoice tool. PayPal then
    marks the invoice paid and its webhook updates the milestone."""
    w = ws(cid)
    m = milestone(w, code)
    rec = w.invoices.get(code)
    if not rec:
        raise HTTPException(404, f"{code} has no invoice yet.")
    if rec.get("status") in ("PAID", "MARKED_AS_PAID"):
        raise HTTPException(409, f"Invoice {rec['number']} is already paid.")
    paypal()
    spend(request, "paypal")
    params = {"invoice_id": rec["id"], "method": "BANK_TRANSFER", "payment_date": date.today().isoformat(),
              "amount": {"currency_code": m.currency, "value": m.amount},
              "note": "Sandbox demo: the client's bank transfer, recorded to show PayPal confirming payment."}
    try:
        await asyncio.to_thread(billing_agent.paypal_api().run, "record_payment_for_invoice", params)
    except Exception as e:
        w.note(f"PayPal refused the sandbox payment for {code}: {str(e)[:160]}", "error")
        raise HTTPException(502, "PayPal refused the sandbox payment.")
    w.record("payment_recorded", code, rec=rec, amount=m.amount,
             source="Sandbox demo: PayPal Agent Toolkit (record_payment_for_invoice)",
             detail="The client's bank transfer, logged in the PayPal sandbox. PayPal confirms it next.")
    w.note(f"Sandbox: the client's payment for invoice {rec['number']} recorded in PayPal. Waiting for PayPal to confirm it.")
    return w.model_dump()

