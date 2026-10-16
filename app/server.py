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

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, billing_agent
from .contract_reader import read_contract
from .llm import ai_configured, model_id
from .paypal_client import PayPalClient, PayPalError, paypal_configured
from .schedule import Schedule

ROOT = Path(__file__).resolve().parent
SAMPLE = ROOT.parent / "samples" / "northwind-harbour-services-agreement.pdf"
MAX_UPLOAD = 15 * 1024 * 1024

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

    def note(self, text: str, kind: str = "info") -> None:
        self.log.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, "text": text})


STORE: dict[str, Workspace] = {}
MAX_WORKSPACES = int(os.environ.get("MAX_WORKSPACES", "300"))

# Usage limits for a public demo (per visitor IP, rolling hour; and per day for
# the whole server). Each AI call and each PayPal send counts once.
LIMITS = {
    "ai": (int(os.environ.get("AI_CALLS_PER_HOUR_PER_IP", "20")), 3600),
    "paypal": (int(os.environ.get("PAYPAL_SENDS_PER_HOUR_PER_IP", "15")), 3600),
}
DAILY_AI_CAP = int(os.environ.get("AI_CALLS_PER_DAY", "300"))
_hits: dict[tuple[str, str], deque] = defaultdict(deque)
_daily: dict[str, int] = defaultdict(int)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else "") or (request.client.host if request.client else "unknown")


def spend(request: Request, kind: str) -> None:
    """Count one use of `kind` for this visitor, or refuse with 429."""
    limit, window = LIMITS[kind]
    now = time.time()
    q = _hits[(client_ip(request), kind)]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "This demo limits how often one visitor can run that step. Try again in a little while.")
    if kind == "ai":
        day = date.today().isoformat()
        if _daily[day] >= DAILY_AI_CAP:
            raise HTTPException(429, "The demo has reached today's limit for AI reads. It resets tomorrow.")
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


@app.get("/")
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


@app.post("/api/contracts/{cid}/milestones/{code}/deliver")
async def deliver(cid: str, code: str, body: Delivery, request: Request):
    w = ws(cid)
    m = milestone(w, code)
    spend(request, "ai")
    check = await agent.check_delivery(w.schedule, m, body.evidence)
    w.checks[code] = check.model_dump()
    w.status[code] = "delivered" if check.recommendation == "send" else "held"
    w.note(f"{code} marked delivered. Agent recommends {check.recommendation.upper()}: {check.summary}",
           "ok" if check.recommendation == "send" else "warn")
    return w.model_dump()


class InvoiceRequest(BaseModel):
    recipient_email: str
    approve: bool = False
    override_hold: bool = False


@app.post("/api/contracts/{cid}/milestones/{code}/invoice")
async def invoice(cid: str, code: str, body: InvoiceRequest, request: Request):
    w = ws(cid)
    m = milestone(w, code)
    if not body.approve:
        raise HTTPException(400, "Sending an invoice needs your approval.")
    if code in w.invoices:
        raise HTTPException(409, f"{code} already has invoice {w.invoices[code]['number']}.")
    if w.status.get(code) == "held" and not body.override_hold:
        raise HTTPException(409, "The agent recommended holding this invoice. Confirm the override to send it anyway.")
    pp = paypal()
    spend(request, "paypal")
    try:
        number = await pp.next_invoice_number()
        payload = agent.invoice_payload(w.schedule, m, invoice_number=number,
                                        recipient_email=body.recipient_email, invoice_date=date.today(),
                                        invoicer_email=os.environ.get("PAYPAL_INVOICER_EMAIL") or None)
        inv = await pp.create_draft(payload)
        subject = f"{w.schedule.contract_reference or w.schedule.contract_title}: invoice for {m.code}"
        await pp.send(inv["id"], subject=subject)
        inv = await pp.get(inv["id"])
    except PayPalError as e:
        w.note(f"PayPal refused the invoice for {code}: {e.message}", "error")
        raise HTTPException(502, f"PayPal: {e.message}")
    link = recipient_link(inv)
    due = (inv.get("detail", {}).get("payment_term", {}) or {}).get("due_date")
    w.invoices[code] = {"id": inv["id"], "number": number, "status": inv.get("status"), "due_date": due, "link": link}
    w.status[code] = "invoiced"
    w.note(f"Invoice {number} for {code} sent through PayPal ({m.currency} {Decimal(m.amount):,.2f}).", "ok")
    return w.model_dump()


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
        if w.status.get(code) != "paid":
            w.note(f"{code} paid: invoice {rec['number']}.", "ok")
        w.status[code] = "paid"
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
    w.note(f"Reminder sent for invoice {rec['number']} ({code}).", "ok")
    return {"subject": subject, "note": note, "sent": True, "workspace": w.model_dump()}


# ---- PayPal webhook: invoices paid on PayPal mark their milestone paid ----
log = logging.getLogger("clausepay")
PAID_STATES = ("PAID", "MARKED_AS_PAID")
_seen_events: deque = deque(maxlen=500)


def _event_invoice(event: dict) -> tuple[Optional[str], Optional[str]]:
    res = event.get("resource") or {}
    inv = res.get("invoice") if isinstance(res.get("invoice"), dict) else res
    return inv.get("id"), inv.get("status")


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
                if w.status.get(code) != "paid":
                    w.status[code] = "paid"
                    w.note(f"{code} paid: invoice {rec['number']}. Paid through PayPal (webhook).", "ok")
            elif etype == "INVOICING.INVOICE.CANCELLED":
                if rec.get("status") != "CANCELLED":
                    rec["status"] = "CANCELLED"
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
        out = await billing_agent.ask(q, ctx, allowed={i["id"] for i in w.invoices.values()})
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
    w.note(f"Sandbox: the client's payment for invoice {rec['number']} recorded in PayPal. Waiting for PayPal to confirm it.")
    return w.model_dump()

