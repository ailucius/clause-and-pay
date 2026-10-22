"""Shared fixtures: the sample contract's schedule built without a model call, a
workspace in the server's store, and fakes for Gemini and PayPal (all offline)."""
from pathlib import Path

import pytest

from app import server
from app.contract_reader import pdf_pages
from app.schedule import ClauseOut, MilestoneOut, ScheduleOut, check_schedule

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples" / "northwind-harbour-services-agreement.pdf"
EVIDENCE = ROOT / "samples" / "evidence"


def sample_schedule():
    """What the model reads from the sample contract (the quotes are the contract's own)."""
    pages = pdf_pages(SAMPLE.read_bytes())
    raw = ScheduleOut(
        contract_title="Harbour City Transport Data Study", contract_reference="HCC-2026-117",
        supplier_name="Northwind Analytics Ltd", client_name="Harbour City Council", currency="USD",
        total_value="48,000.00", net_days=30,
        payment_terms=ClauseOut(summary="30 days", quote="The Client shall pay each valid invoice within thirty (30) days of receipt.", page=2),
        late_payment=ClauseOut(summary="1.5% a month", quote="interest shall accrue on the overdue amount at the rate of 1.5% per month from the due date until the date of payment", page=2),
        invoice_condition=ClauseOut(summary="Invoice only after the client accepts the deliverable in writing.",
                                    quote="The Supplier may issue an invoice for a milestone only after the Client has accepted the related deliverable in writing.", page=1),
        milestones=[
            MilestoneOut(code="M1", name="Inception report", amount="7,200.00", currency="USD", due_as_written="9 October 2026",
                         due_date_iso="2026-10-09", payment_quote="M1 Inception report 7,200.00 9 October 2026", payment_page=3,
                         acceptance_criteria=["Set out the survey method", "A sample size of at least 1,200 passengers",
                                              "A work plan with dates for every later milestone"],
                         acceptance_quote="the report shall set out the survey method, a sample size of at least 1,200 passengers", acceptance_page=3),
            MilestoneOut(code="M2", name="Passenger survey dataset", amount="14,400.00", currency="USD", due_as_written="27 November 2026",
                         due_date_iso="2026-11-27", payment_quote="M2 Passenger survey dataset 14,400.00 27 November 2026", payment_page=3,
                         acceptance_criteria=["At least 1,200 completed and anonymised survey responses",
                                              "A data dictionary describing every field"],
                         acceptance_quote="the dataset shall contain at least 1,200 completed and anonymised survey responses", acceptance_page=3),
        ])
    return check_schedule(raw, pages)


@pytest.fixture
def schedule():
    return sample_schedule()


@pytest.fixture
def workspace(schedule):
    """A fresh workspace for the sample contract in the server's store; demo limits reset."""
    server.STORE.clear()
    server._hits.clear()
    server._seen_events.clear()
    w = server.Workspace(id="abcdef123456", filename=SAMPLE.name, schedule=schedule,
                         status={m.code: "planned" for m in schedule.milestones})
    server.keep(w)
    yield w
    server.STORE.clear()


class FakePayPal:
    """The async client's surface used by the server, recording each call."""

    def __init__(self):
        self.calls, self.n, self.status = [], 40, {}

    async def next_invoice_number(self):
        self.n += 1
        self.calls.append("number")
        return f"00{self.n}"

    async def create_draft(self, payload):
        self.calls.append("draft")
        iid = f"INV2-TEST-{self.n}"
        self.status[iid] = "DRAFT"
        return {"id": iid, "status": "DRAFT", "detail": payload["detail"]}

    async def send(self, invoice_id, subject=None, note=None):
        self.calls.append("send")
        self.status[invoice_id] = "SENT"

    async def get(self, invoice_id):
        self.calls.append("get")
        return {"id": invoice_id, "status": self.status.get(invoice_id, "SENT"),
                "detail": {"payment_term": {"due_date": "2026-11-21"},
                           "metadata": {"recipient_view_url": f"https://www.sandbox.paypal.com/invoice/p/#{invoice_id}"}},
                "payments": {"paid_amount": {"value": "7200.00", "currency_code": "USD"}}}

    async def remind(self, invoice_id, subject, note):
        self.calls.append("remind")

    async def verify_webhook(self, *, headers, raw_event, webhook_id):
        return True


@pytest.fixture
def fake_paypal(monkeypatch):
    fake = FakePayPal()
    monkeypatch.setattr(server, "paypal", lambda: fake)
    return fake
