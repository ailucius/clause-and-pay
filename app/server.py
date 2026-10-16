"""Clause & Pay web app: one page, a small JSON API, state kept in memory per contract."""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent
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
    log: list[dict] = []

    def note(self, text: str, kind: str = "info") -> None:
        self.log.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, "text": text})


STORE: dict[str, Workspace] = {}
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
    STORE[w.id] = w
    return w.model_dump()


@app.post("/api/contracts")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "The file is larger than 15 MB.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(415, "Upload a PDF.")
    return await _read(data, file.filename or "contract.pdf")


@app.post("/api/contracts/sample")
async def sample():
    return await _read(SAMPLE.read_bytes(), SAMPLE.name)


@app.get("/api/contracts/{cid}")
async def get_contract(cid: str):
    return ws(cid).model_dump()


@app.get("/api/sample.pdf")
async def sample_pdf():
    return FileResponse(SAMPLE, media_type="application/pdf")


class Delivery(BaseModel):
    evidence: str = ""


@app.post("/api/contracts/{cid}/milestones/{code}/deliver")
async def deliver(cid: str, code: str, body: Delivery):
    w = ws(cid)
    m = milestone(w, code)
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
async def invoice(cid: str, code: str, body: InvoiceRequest):
    w = ws(cid)
    m = milestone(w, code)
    if not body.approve:
        raise HTTPException(400, "Sending an invoice needs your approval.")
    if code in w.invoices:
        raise HTTPException(409, f"{code} already has invoice {w.invoices[code]['number']}.")
    if w.status.get(code) == "held" and not body.override_hold:
        raise HTTPException(409, "The agent recommended holding this invoice. Confirm the override to send it anyway.")
    pp = paypal()
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
    link = next((l["href"] for l in inv.get("links", []) if l.get("rel") in ("payer-view", "recipient_view")), None)
    due = (inv.get("detail", {}).get("payment_term", {}) or {}).get("due_date")
    w.invoices[code] = {"id": inv["id"], "number": number, "status": inv.get("status"), "due_date": due, "link": link}
    w.status[code] = "invoiced"
    w.note(f"Invoice {number} for {code} sent through PayPal ({m.currency} {m.amount}).", "ok")
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
async def reminder(cid: str, code: str, body: ReminderRequest):
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
    try:
        await paypal().remind(rec["id"], subject=subject, note=note)
    except PayPalError as e:
        raise HTTPException(502, f"PayPal: {e.message}")
    w.note(f"Reminder sent for invoice {rec['number']} ({code}).", "ok")
    return {"subject": subject, "note": note, "sent": True, "workspace": w.model_dump()}
