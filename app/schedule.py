"""The payment schedule: what the model returns, and the checked version the app uses."""
from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional

from pydantic import BaseModel, Field

from .verify import check_quote

# ---- what the model is asked to return (also the JSON schema sent to Gemini) ----


class ClauseOut(BaseModel):
    summary: str = Field(description="One plain sentence saying what the clause means.")
    quote: str = Field(description="The contract's own sentence, copied word for word.")
    page: int = Field(description="Page number from the === PAGE n === marker the quote sits under.")


class MilestoneOut(BaseModel):
    code: str = Field(description="The milestone's label in the contract, e.g. M1. Make one up (M1, M2...) if none.")
    name: str = Field(description="Short name of the deliverable.")
    amount: str = Field(description="The amount for this milestone exactly as written, e.g. 7,200.00")
    currency: str = Field(description="ISO 4217 code, e.g. USD, EUR, GBP.")
    due_as_written: str = Field(description="When it is due, as the contract says it.")
    due_date_iso: Optional[str] = Field(None, description="YYYY-MM-DD when the contract gives a calendar date, else null.")
    payment_quote: str = Field(description="Verbatim text that sets this milestone's amount (a table row may be quoted as its cells joined by spaces).")
    payment_page: int
    acceptance_criteria: list[str] = Field(description="Each condition the deliverable must meet, short and plain.")
    acceptance_quote: Optional[str] = Field(None, description="Verbatim sentence with the acceptance criteria, or null.")
    acceptance_page: Optional[int] = None


class ScheduleOut(BaseModel):
    contract_title: str
    contract_reference: Optional[str] = None
    supplier_name: str = Field(description="The party that is paid (issues invoices).")
    client_name: str = Field(description="The party that pays.")
    currency: str
    total_value: Optional[str] = Field(None, description="Total contract value as written, or null.")
    net_days: Optional[int] = Field(None, description="Days the client has to pay an invoice, e.g. 30, or null.")
    payment_terms: Optional[ClauseOut] = None
    late_payment: Optional[ClauseOut] = Field(None, description="Interest or penalty for late payment, or null.")
    invoice_condition: Optional[ClauseOut] = Field(None, description="Any condition before an invoice may be issued, or null.")
    milestones: list[MilestoneOut]


# ---- the checked schedule the app works with ----


class Cited(BaseModel):
    quote: str
    page: Optional[int]
    status: str  # verified | moved | not_found | empty

    @property
    def ok(self) -> bool:
        return self.status in ("verified", "moved")


class Clause(BaseModel):
    summary: str
    cite: Cited


class Milestone(BaseModel):
    code: str
    name: str
    amount: Optional[str]  # "7200.00", None when it could not be read as a number
    currency: str
    due_as_written: str
    due_date: Optional[str]  # YYYY-MM-DD
    payment_cite: Cited
    acceptance_criteria: list[str]
    acceptance_cite: Optional[Cited]
    issues: list[str] = []


class Schedule(BaseModel):
    contract_title: str
    contract_reference: Optional[str]
    supplier_name: str
    client_name: str
    currency: str
    total_value: Optional[str]
    net_days: Optional[int]
    payment_terms: Optional[Clause]
    late_payment: Optional[Clause]
    invoice_condition: Optional[Clause]
    milestones: list[Milestone]
    pages: int
    issues: list[str] = []

    def stats(self) -> dict:
        cites = [c for c in self.all_cites()]
        ok = sum(1 for c in cites if c.ok)
        return {"milestones": len(self.milestones), "quotes": len(cites), "verified": ok,
                "verified_rate": round(ok / len(cites), 3) if cites else 0.0}

    def all_cites(self):
        for c in (self.payment_terms, self.late_payment, self.invoice_condition):
            if c:
                yield c.cite
        for m in self.milestones:
            yield m.payment_cite
            if m.acceptance_cite:
                yield m.acceptance_cite


def parse_amount(text: str | None) -> Optional[str]:
    """'USD 7,200.00' -> '7200.00'; '7.200,50 EUR' -> '7200.50'. None when no number."""
    if not text:
        return None
    t = re.sub(r"[^\d.,]", "", text)
    if not t or not re.search(r"\d", t):
        return None
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        head, _, tail = t.rpartition(",")
        t = (head.replace(",", "") + "." + tail) if len(tail) in (1, 2) else t.replace(",", "")
    try:
        return str(Decimal(t).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None


def parse_iso_date(text: str | None) -> Optional[str]:
    if not text:
        return None
    try:
        return date.fromisoformat(text.strip()[:10]).isoformat()
    except ValueError:
        return None


def _cite(quote: str | None, page: int | None, pages: list[str]) -> Cited:
    chk = check_quote(quote or "", page, pages)
    return Cited(quote=(quote or "").strip(), page=chk.page, status=chk.status)


def _clause(c: ClauseOut | None, pages: list[str]) -> Optional[Clause]:
    if c is None or not (c.quote or "").strip():
        return None
    return Clause(summary=c.summary, cite=_cite(c.quote, c.page, pages))


def check_schedule(raw: ScheduleOut, pages: list[str]) -> Schedule:
    """Verify every quote against the page text and normalise amounts and dates."""
    currency = (raw.currency or "").strip().upper()[:3]
    milestones: list[Milestone] = []
    for m in raw.milestones:
        issues = []
        amount = parse_amount(m.amount)
        if amount is None:
            issues.append("The amount could not be read as a number.")
        cur = (m.currency or currency).strip().upper()[:3]
        due = parse_iso_date(m.due_date_iso)
        pay = _cite(m.payment_quote, m.payment_page, pages)
        if not pay.ok:
            issues.append("The payment wording was not found in the contract: check it by hand.")
        acc = _cite(m.acceptance_quote, m.acceptance_page, pages) if m.acceptance_quote else None
        if acc is not None and not acc.ok:
            issues.append("The acceptance wording was not found in the contract: check it by hand.")
        milestones.append(Milestone(
            code=m.code.strip() or f"M{len(milestones) + 1}", name=m.name.strip(), amount=amount,
            currency=cur, due_as_written=m.due_as_written.strip(), due_date=due, payment_cite=pay,
            acceptance_criteria=[c.strip() for c in m.acceptance_criteria if c.strip()],
            acceptance_cite=acc, issues=issues))
    sched = Schedule(
        contract_title=raw.contract_title.strip(), contract_reference=(raw.contract_reference or None),
        supplier_name=raw.supplier_name.strip(), client_name=raw.client_name.strip(), currency=currency,
        total_value=parse_amount(raw.total_value), net_days=raw.net_days,
        payment_terms=_clause(raw.payment_terms, pages), late_payment=_clause(raw.late_payment, pages),
        invoice_condition=_clause(raw.invoice_condition, pages), milestones=milestones, pages=len(pages))
    total = sum(Decimal(m.amount) for m in milestones if m.amount)
    if sched.total_value and milestones and total != Decimal(sched.total_value):
        sched.issues.append(f"The milestones add up to {total:.2f}, the contract total is {sched.total_value}.")
    return sched
