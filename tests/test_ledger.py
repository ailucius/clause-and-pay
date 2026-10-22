"""The ledger: every invoice event (drafted, sent, reminded, payment recorded, paid,
cancelled, a hold overridden with its reason), with amounts and times, from the
app's own PayPal calls and from PayPal's verified webhook. PayPal is faked."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import billing_agent, server

BASE = "/api/contracts/abcdef123456"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH-ID-TEST")
    return TestClient(server.app)


def _invoice(client, code="M1", **extra):
    body = {"recipient_email": "client@example.com", "approve": True, **extra}
    return client.post(f"{BASE}/milestones/{code}/invoice", json=body)


def _events(w, code=None):
    return [e["event"] for e in w.ledger if code is None or e["code"] == code]


def test_an_invoice_is_drafted_then_sent_with_its_amount(workspace, fake_paypal, client):
    workspace.status["M1"] = "delivered"
    r = _invoice(client)
    assert r.status_code == 200, r.text
    assert fake_paypal.calls == ["number", "draft", "send", "get"]
    assert _events(workspace) == ["drafted", "sent"]
    drafted, sent = workspace.ledger
    assert drafted["amount"] == sent["amount"] == "7200.00" and sent["currency"] == "USD"
    assert sent["invoice"] == "0041" and sent["invoice_id"] == "INV2-TEST-41" and sent["at"]
    assert "PayPal Invoicing API" in sent["source"] and "21 November 2026" in sent["detail"]
    rec = workspace.invoices["M1"]
    assert rec["amount"] == "7200.00" and rec["sent_at"] and rec["reminders"] == 0 and rec["due_date"] == "2026-11-21"
    assert rec["link"].startswith("https://www.sandbox.paypal.com/") and workspace.status["M1"] == "invoiced"
    assert r.json()["ledger"] == workspace.ledger  # the page gets the ledger with the workspace


def test_no_invoice_before_the_delivery_is_checked(workspace, fake_paypal, client):
    r = _invoice(client)
    assert r.status_code == 409 and "Check the delivery" in r.json()["detail"]
    assert fake_paypal.calls == [] and workspace.ledger == []


def test_a_hold_needs_the_override_and_a_stated_reason(workspace, fake_paypal, client):
    workspace.status["M2"] = "held"
    assert _invoice(client, "M2").status_code == 409
    assert _invoice(client, "M2", override_hold=True).status_code == 422
    assert _invoice(client, "M2", override_hold=True, override_reason="  because ").status_code == 422
    assert fake_paypal.calls == [] and workspace.ledger == []
    reason = "Client confirmed acceptance by phone; written email follows today."
    r = _invoice(client, "M2", override_hold=True, override_reason=reason)
    assert r.status_code == 200
    assert _events(workspace) == ["override", "drafted", "sent"]
    override = workspace.ledger[0]
    assert reason in override["detail"] and override["source"] == "You" and override["amount"] == "14400.00"
    assert override["invoice"] == "0041"
    assert workspace.invoices["M2"]["override_reason"] == reason
    assert reason in workspace.log[-1]["text"]


def test_a_reminder_you_approve_is_in_the_ledger(workspace, fake_paypal, client):
    workspace.status["M1"] = "delivered"
    _invoice(client)
    preview = client.post(f"{BASE}/milestones/M1/reminder", json={"approve": False})
    assert preview.status_code == 200 and "remind" not in fake_paypal.calls and _events(workspace) == ["drafted", "sent"]
    r = client.post(f"{BASE}/milestones/M1/reminder", json={"approve": True, "today": "2026-12-01"})
    assert r.status_code == 200 and "remind" in fake_paypal.calls
    assert _events(workspace) == ["drafted", "sent", "reminded"]
    assert workspace.ledger[-1]["detail"].startswith("Reminder: invoice 0041") and workspace.invoices["M1"]["reminders"] == 1


def test_the_agents_reminder_is_in_the_ledger_once_approved(workspace, fake_paypal, client, monkeypatch):
    workspace.status["M1"] = "delivered"
    _invoice(client)
    sent = []

    async def approve(prop, api=None):
        sent.append(prop)

    monkeypatch.setattr(billing_agent, "approve", approve)
    workspace.proposals["p1"] = {"id": "p1", "method": "send_invoice_reminder", "summary": "Send a PayPal reminder",
                                 "args": {"invoice_id": "INV2-TEST-41", "subject": "Friendly reminder", "note": "n"}}
    r = client.post(f"{BASE}/proposals/p1/approve")
    assert r.status_code == 200 and sent and "p1" not in workspace.proposals
    last = workspace.ledger[-1]
    assert last["event"] == "reminded" and "Agent Toolkit" in last["source"] and last["detail"] == "Friendly reminder"
    assert workspace.invoices["M1"]["reminders"] == 1


def test_the_sandbox_payment_then_the_webhook(workspace, fake_paypal, client, monkeypatch):
    workspace.status["M1"] = "delivered"
    _invoice(client)
    runs = []

    class Toolkit:
        def run(self, method, params):
            runs.append((method, params))
            return "{}"

    monkeypatch.setattr(billing_agent, "paypal_api", lambda: Toolkit())
    assert client.post(f"{BASE}/milestones/M1/sandbox-payment").status_code == 200
    assert runs[0][0] == "record_payment_for_invoice" and runs[0][1]["amount"] == {"currency_code": "USD", "value": "7200.00"}
    assert _events(workspace) == ["drafted", "sent", "payment_recorded"]
    event = {"id": "WH-PAID-1", "event_type": "INVOICING.INVOICE.PAID",
             "resource": {"invoice": {"id": "INV2-TEST-41", "status": "PAID",
                                      "payments": {"paid_amount": {"value": "7200.00", "currency_code": "USD"}}}}}
    for _ in range(2):  # PayPal may deliver an event twice
        assert client.post("/api/paypal/webhook", content=json.dumps(event)).status_code == 200
    paid = [e for e in workspace.ledger if e["event"] == "paid"]
    assert len(paid) == 1 and "webhook INVOICING.INVOICE.PAID (signature verified)" in paid[0]["source"]
    assert paid[0]["amount"] == "7200.00" and paid[0]["detail"] == "Event WH-PAID-1"
    assert workspace.status["M1"] == "paid" and workspace.invoices["M1"]["paid_at"]
    # asking PayPal afterwards does not add a second paid event
    fake_paypal.status["INV2-TEST-41"] = "PAID"
    assert client.post(f"{BASE}/milestones/M1/refresh").status_code == 200
    assert _events(workspace).count("paid") == 1


def test_paid_found_by_checking_the_status(workspace, fake_paypal, client):
    workspace.status["M1"] = "delivered"
    _invoice(client)
    fake_paypal.status["INV2-TEST-41"] = "MARKED_AS_PAID"
    client.post(f"{BASE}/milestones/M1/refresh")
    paid = workspace.ledger[-1]
    assert paid["event"] == "paid" and "Check payment status" in paid["source"] and paid["amount"] == "7200.00"
    event = {"id": "WH-LATE", "event_type": "INVOICING.INVOICE.PAID", "resource": {"invoice": {"id": "INV2-TEST-41", "status": "PAID"}}}
    client.post("/api/paypal/webhook", content=json.dumps(event))
    assert _events(workspace).count("paid") == 1


def test_a_cancelled_invoice_is_in_the_ledger(workspace, fake_paypal, client):
    workspace.status["M1"] = "delivered"
    _invoice(client)
    event = {"id": "WH-C", "event_type": "INVOICING.INVOICE.CANCELLED", "resource": {"invoice": {"id": "INV2-TEST-41", "status": "CANCELLED"}}}
    client.post("/api/paypal/webhook", content=json.dumps(event))
    assert _events(workspace)[-1] == "cancelled" and workspace.invoices["M1"]["status"] == "CANCELLED"


def test_a_slow_billing_agent_answers_504(workspace, fake_paypal, client, monkeypatch):
    monkeypatch.setattr(server, "ai_configured", lambda: True)

    async def slow(*a, **k):
        await asyncio.sleep(1)

    monkeypatch.setattr(billing_agent, "ask", slow)
    monkeypatch.setattr(server, "AGENT_TIMEOUT", 0.05)
    r = client.post(f"{BASE}/ask", json={"question": "Which invoices are unpaid?"})
    assert r.status_code == 504 and "took too long" in r.json()["detail"]
    assert workspace.log[-1]["kind"] == "error"


def test_the_agents_thinking_level_is_the_models_own_unless_set(monkeypatch):
    monkeypatch.delenv("CLAUSEPAY_AGENT_THINKING", raising=False)
    assert billing_agent._thinking() is None
    monkeypatch.setenv("CLAUSEPAY_AGENT_THINKING", "low")
    assert billing_agent._thinking().thinking_level.value == "LOW"
    monkeypatch.setenv("CLAUSEPAY_AGENT_THINKING", "minimal")
    assert billing_agent._thinking() is None


def test_paypal_out_of_reach_answers_502(workspace, fake_paypal, client, monkeypatch):
    import httpx

    async def down(payload):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(fake_paypal, "create_draft", down)
    workspace.status["M1"] = "delivered"
    r = _invoice(client)
    assert r.status_code == 502 and "could not be reached" in r.json()["detail"]
    assert "M1" not in workspace.invoices and workspace.status["M1"] == "delivered"
