import asyncio
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app import agent
from app.contract_reader import numbered_text, pdf_pages
from app.paypal_client import PayPalClient, PayPalError, remind_payload, send_payload
from app.schedule import ClauseOut, MilestoneOut, ScheduleOut, check_schedule, parse_amount, parse_iso_date
from app.verify import check_quote, normalise

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "northwind-harbour-services-agreement.pdf"


@pytest.fixture(scope="module")
def pages():
    return pdf_pages(SAMPLE.read_bytes())


# ---- quote verification ----

def test_normalise_quotes_dashes_and_hyphenated_breaks():
    assert normalise("The “Client” – shall pay") == 'the "client" - shall pay'
    assert normalise("instal-\n ments  due") == "instalments due"


def test_quote_on_cited_page_is_verified(pages):
    c = check_quote("The Client shall pay each valid invoice within thirty (30) days of receipt.", 2, pages)
    assert c.status == "verified" and c.page == 2 and c.ok


def test_quote_on_another_page_is_moved_and_page_corrected(pages):
    c = check_quote("The Client shall pay each valid invoice within thirty (30) days of receipt", 1, pages)
    assert c.status == "moved" and c.page == 2 and c.ok


def test_invented_quote_is_not_found(pages):
    c = check_quote("The Client shall pay each valid invoice within seven (7) days of receipt.", 2, pages)
    assert c.status == "not_found" and not c.ok


def test_table_row_quote_verifies(pages):
    assert check_quote("M2 Passenger survey dataset 14,400.00 29 January 2027", 3, pages).ok


def test_too_short_quote_is_empty(pages):
    assert check_quote("pay", 2, pages).status == "empty"


def test_numbered_text_marks_pages(pages):
    t = numbered_text(pages)
    assert "=== PAGE 1 ===" in t and "=== PAGE 3 ===" in t


# ---- schedule normalisation ----

@pytest.mark.parametrize("raw,want", [
    ("USD 7,200.00", "7200.00"), ("7.200,50 EUR", "7200.50"), ("14400", "14400.00"),
    ("1,234", "1234.00"), ("12,5", "12.50"), ("", None), ("n/a", None),
])
def test_parse_amount(raw, want):
    assert parse_amount(raw) == want


def test_parse_iso_date():
    assert parse_iso_date("2026-12-15") == "2026-12-15"
    assert parse_iso_date("15 December 2026") is None


def _raw(**over):
    base = dict(
        contract_title="Services Agreement", contract_reference="HCC-2026-117",
        supplier_name="Northwind Analytics Ltd", client_name="Harbour City Council", currency="usd",
        total_value="USD 48,000.00", net_days=30,
        payment_terms=ClauseOut(summary="30 days", quote="The Client shall pay each valid invoice within thirty (30) days of receipt.", page=2),
        late_payment=ClauseOut(summary="1.5% a month", quote="interest shall accrue on the overdue amount at the rate of 1.5% per month from the due date until the date of payment", page=2),
        invoice_condition=None,
        milestones=[
            MilestoneOut(code="M1", name="Inception report", amount="7,200.00", currency="USD", due_as_written="15 December 2026",
                         due_date_iso="2026-12-15", payment_quote="M1 Inception report 7,200.00 15 December 2026", payment_page=3,
                         acceptance_criteria=["survey method", "sample of at least 1,200"],
                         acceptance_quote="the report shall set out the survey method, a sample size of at least 1,200 passengers", acceptance_page=3),
            MilestoneOut(code="M2", name="Dataset", amount="14,400.00", currency="USD", due_as_written="29 January 2027",
                         due_date_iso="2027-01-29", payment_quote="M2 costs 99,999.00 according to nobody", payment_page=3,
                         acceptance_criteria=["1,200 responses"]),
        ])
    base.update(over)
    return ScheduleOut(**base)


def test_check_schedule_verifies_and_flags(pages):
    s = check_schedule(_raw(), pages)
    assert s.currency == "USD"
    m1, m2 = s.milestones
    assert m1.amount == "7200.00" and m1.payment_cite.ok and m1.acceptance_cite.ok and not m1.issues
    assert not m2.payment_cite.ok and any("not found" in i for i in m2.issues)
    # the two milestones do not add up to the 48,000 total
    assert any("add up to 21600.00" in i for i in s.issues)
    st = s.stats()
    assert st["quotes"] == 5 and st["verified"] == 4


# ---- invoice and reminder payloads ----

def test_invoice_payload_cites_clause_and_page(pages):
    s = check_schedule(_raw(), pages)
    p = agent.invoice_payload(s, s.milestones[0], invoice_number="0007", recipient_email="buyer@example.com",
                              invoice_date=date(2026, 12, 16))
    item = p["items"][0]
    assert item["unit_amount"] == {"currency_code": "USD", "value": "7200.00"}
    assert item["quantity"] == "1" and item["name"] == "M1: Inception report"
    assert item["description"].startswith("Contract, page 3:") and "7,200.00" in item["description"]
    assert p["detail"]["payment_term"] == {"term_type": "NET_30"}
    assert p["detail"]["invoice_date"] == "2026-12-16" and p["detail"]["reference"] == "HCC-2026-117"
    assert p["primary_recipients"][0]["billing_info"]["email_address"] == "buyer@example.com"
    assert "page 2" in p["detail"]["term"]
    assert len(item["description"]) <= 1000 and len(item["name"]) <= 200


def test_invoice_payload_odd_net_days_uses_due_date(pages):
    s = check_schedule(_raw(net_days=21), pages)
    p = agent.invoice_payload(s, s.milestones[0], invoice_number="1", recipient_email="b@example.com", invoice_date=date(2026, 12, 1))
    assert p["detail"]["payment_term"] == {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": "2026-12-22"}


def test_invoice_payload_refuses_unreadable_amount(pages):
    s = check_schedule(_raw(), pages)
    m = s.milestones[0].model_copy(update={"amount": None})
    with pytest.raises(ValueError):
        agent.invoice_payload(s, m, invoice_number="1", recipient_email="b@example.com", invoice_date=date(2026, 12, 1))


def test_reminder_quotes_late_clause_and_computes_interest(pages):
    s = check_schedule(_raw(), pages)
    rate = agent.monthly_rate_from(s)
    assert rate == Decimal("1.5")
    subject, note = agent.reminder_text(s, s.milestones[0], invoice_number="0007", due=date(2027, 1, 15),
                                        today=date(2027, 2, 14), monthly_rate_pct=rate)
    assert "30 days overdue" in subject
    assert "Late payment, page 2" in note and "1.5% per month" in note
    assert "USD 108.00" in note  # 7,200 x 1.5% x 30/30


def test_send_and_remind_payloads():
    assert send_payload(subject="S", note=None) == {"send_to_invoicer": True, "send_to_recipient": True, "subject": "S"}
    r = remind_payload(subject="Reminder", note="x" * 5000)
    assert r["send_to_recipient"] is True and len(r["note"]) == 4000


# ---- PayPal client against a mock server ----

def _mock():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        path = req.url.path
        if path == "/v1/oauth2/token":
            assert req.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "tok-123", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer tok-123"
        if path == "/v2/invoicing/generate-next-invoice-number":
            return httpx.Response(200, json={"invoice_number": "0042"})
        if path == "/v2/invoicing/invoices" and req.method == "POST":
            assert req.headers.get("prefer") == "return=representation"
            body = json.loads(req.content)
            return httpx.Response(201, json={"id": "INV2-AAAA", "status": "DRAFT", "detail": body["detail"]})
        if path == "/v2/invoicing/invoices/INV2-AAAA/send":
            return httpx.Response(200, json={"href": "https://www.sandbox.paypal.com/invoice/p/#INV2-AAAA"})
        if path == "/v2/invoicing/invoices/INV2-AAAA/remind":
            return httpx.Response(204)
        if path == "/v2/invoicing/invoices/INV2-AAAA":
            return httpx.Response(200, json={"id": "INV2-AAAA", "status": "SENT"})
        if path == "/v2/invoicing/invoices/BAD/send":
            return httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY", "message": "The requested action could not be performed.",
                                             "details": [{"field": "/detail", "issue": "INVALID_STATUS"}], "debug_id": "d1"})
        return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND", "message": "nope"})

    return httpx.MockTransport(handler), calls


def test_paypal_client_invoice_flow():
    async def run():
        transport, calls = _mock()
        c = PayPalClient("id", "secret", transport=transport)
        assert await c.next_invoice_number() == "0042"
        inv = await c.create_draft({"detail": {"invoice_number": "0042", "currency_code": "USD"}, "items": []})
        assert inv["id"] == "INV2-AAAA" and inv["status"] == "DRAFT"
        await c.send("INV2-AAAA", subject="Invoice")
        assert await c.remind("INV2-AAAA", subject="R", note="N") is None
        assert (await c.get("INV2-AAAA"))["status"] == "SENT"
        await c.aclose()
        token_calls = [r for r in calls if r.url.path == "/v1/oauth2/token"]
        assert len(token_calls) == 1  # token reused
        send = [r for r in calls if r.url.path.endswith("/send")][0]
        assert json.loads(send.content) == {"send_to_invoicer": True, "send_to_recipient": True, "subject": "Invoice"}
    asyncio.run(run())


def test_paypal_client_raises_readable_error():
    async def run():
        transport, _ = _mock()
        c = PayPalClient("id", "secret", transport=transport)
        with pytest.raises(PayPalError) as e:
            await c.send("BAD")
        assert e.value.status == 422 and "INVALID_STATUS" in e.value.message and e.value.debug_id == "d1"
        await c.aclose()
    asyncio.run(run())


def test_paypal_from_env_requires_keys(monkeypatch):
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        PayPalClient.from_env()


# ---- delivery check gate (model mocked) ----

def test_delivery_check_forces_hold_unless_every_criterion_is_yes(pages, monkeypatch):
    s = check_schedule(_raw(), pages)

    async def fake(prompt, schema, **kw):
        assert "1,200" in prompt and "page 3" in prompt
        return schema(recommendation="send", summary="Looks fine.", criteria=[
            {"criterion": "survey method", "met": "yes", "evidence": "stated"},
            {"criterion": "sample of at least 1,200", "met": "unclear", "evidence": "no number given"}])

    monkeypatch.setattr(agent, "generate_json", fake)
    out = asyncio.run(agent.check_delivery(s, s.milestones[0], "Report sent."))
    assert out.recommendation == "hold"


def test_recipient_link_comes_from_invoice_metadata():
    from app.server import recipient_link
    inv = {"detail": {"metadata": {"recipient_view_url": "https://www.sandbox.paypal.com/invoice/p/#ABC"}},
           "links": [{"rel": "self", "href": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-X"}]}
    assert recipient_link(inv) == "https://www.sandbox.paypal.com/invoice/p/#ABC"
    assert recipient_link({"links": [{"rel": "self", "href": "x"}]}) is None
