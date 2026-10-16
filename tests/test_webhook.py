"""The PayPal webhook marks a milestone paid only for a verified event."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import server
from app.paypal_client import PayPalClient


class FakePayPal:
    def __init__(self, ok: bool):
        self.ok, self.calls = ok, []

    async def verify_webhook(self, *, headers, raw_event, webhook_id):
        self.calls.append((webhook_id, raw_event))
        return self.ok


def _workspace(inv_id="INV2-AAAA-1111"):
    w = server.Workspace.model_construct(id="w1", filename="c.pdf", schedule=None, status={"M1": "invoiced"},
                                         checks={}, invoices={"M1": {"id": inv_id, "number": "0007", "status": "SENT"}},
                                         log=[])
    server.STORE.clear()
    server.STORE["w1"] = w
    return w


def _event(eid="WH-1", etype="INVOICING.INVOICE.PAID", inv_id="INV2-AAAA-1111", status="PAID"):
    return {"id": eid, "event_type": etype, "resource": {"invoice": {"id": inv_id, "status": status}}}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH-ID-TEST")
    server._seen_events.clear()
    return TestClient(server.app)


def test_verified_paid_event_marks_the_milestone_paid(client, monkeypatch):
    w = _workspace()
    fake = FakePayPal(True)
    monkeypatch.setattr(server, "paypal", lambda: fake)
    r = client.post("/api/paypal/webhook", content=json.dumps(_event()), headers={"Content-Type": "application/json"})
    assert r.status_code == 200 and r.json()["matched"] == "M1"
    assert w.status["M1"] == "paid" and w.invoices["M1"]["status"] == "PAID"
    assert "Paid through PayPal (webhook)" in w.log[-1]["text"]
    assert fake.calls[0][0] == "WH-ID-TEST"


def test_unverified_event_is_rejected(client, monkeypatch):
    w = _workspace()
    monkeypatch.setattr(server, "paypal", lambda: FakePayPal(False))
    r = client.post("/api/paypal/webhook", content=json.dumps(_event()))
    assert r.status_code == 400
    assert w.status["M1"] == "invoiced"


def test_unknown_invoice_is_ignored(client, monkeypatch):
    w = _workspace()
    monkeypatch.setattr(server, "paypal", lambda: FakePayPal(True))
    r = client.post("/api/paypal/webhook", content=json.dumps(_event(inv_id="INV2-OTHER")))
    assert r.status_code == 200 and r.json()["matched"] is None
    assert w.status["M1"] == "invoiced"


def test_duplicate_event_is_applied_once(client, monkeypatch):
    w = _workspace()
    monkeypatch.setattr(server, "paypal", lambda: FakePayPal(True))
    for _ in range(2):
        r = client.post("/api/paypal/webhook", content=json.dumps(_event(eid="WH-DUP")))
        assert r.status_code == 200
    assert r.json().get("duplicate") is True
    assert sum("Paid through PayPal" in n["text"] for n in w.log) == 1


def test_updated_event_with_paid_status_counts_and_resource_may_be_the_invoice(client, monkeypatch):
    w = _workspace()
    monkeypatch.setattr(server, "paypal", lambda: FakePayPal(True))
    ev = {"id": "WH-2", "event_type": "INVOICING.INVOICE.UPDATED", "resource": {"id": "INV2-AAAA-1111", "status": "MARKED_AS_PAID"}}
    r = client.post("/api/paypal/webhook", content=json.dumps(ev))
    assert r.json()["matched"] == "M1" and w.status["M1"] == "paid"


def test_no_webhook_id_means_not_configured(client, monkeypatch):
    monkeypatch.delenv("PAYPAL_WEBHOOK_ID")
    r = client.post("/api/paypal/webhook", content=json.dumps(_event()))
    assert r.status_code == 503


def test_verify_sends_the_raw_event_and_reads_success():
    seen = {}

    def handler(req: httpx.Request):
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 300})
        seen["body"] = req.content.decode()
        return httpx.Response(200, json={"verification_status": "SUCCESS"})

    import asyncio
    pp = PayPalClient("id", "sec", transport=httpx.MockTransport(handler))
    raw = b'{"id":"WH-9","event_type":"INVOICING.INVOICE.PAID","resource":{"invoice":{"id":"X","status":"PAID"}}}'
    headers = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/cert",
               "PAYPAL-TRANSMISSION-ID": "t1", "PAYPAL-TRANSMISSION-SIG": "sig", "PAYPAL-TRANSMISSION-TIME": "2026-10-16T12:00:00Z"}
    assert asyncio.run(pp.verify_webhook(headers=headers, raw_event=raw, webhook_id="WH-ID")) is True
    body = json.loads(seen["body"])
    assert body["webhook_id"] == "WH-ID" and body["webhook_event"]["id"] == "WH-9"
    assert seen["body"].endswith(raw.decode() + "}")
    assert asyncio.run(pp.verify_webhook(headers={}, raw_event=raw, webhook_id="WH-ID")) is False
