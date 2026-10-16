"""The billing agent: PayPal's toolkit tools, this contract's invoices only, and
no reminder leaves without the user's approval."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app import billing_agent


class FakeAPI:
    def __init__(self):
        self.calls = []

    def run(self, method, params):
        self.calls.append((method, params))
        if method == "list_invoices":
            return json.dumps({"total_items": 2, "items": [
                {"id": "INV2-MINE", "status": "SENT", "detail": {"invoice_number": "0007"}, "amount": {"value": "7200.00", "currency_code": "USD"}},
                {"id": "INV2-OTHER", "status": "SENT", "detail": {"invoice_number": "0099"}, "amount": {"value": "1.00", "currency_code": "USD"},
                 "primary_recipients": [{"billing_info": {"email_address": "someone@else.example"}}]}]})
        return json.dumps({"ok": True})


def _resp(*parts):
    content = SimpleNamespace(parts=list(parts), role="model")
    return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


def _call(name, args):
    return SimpleNamespace(function_call=SimpleNamespace(name=name, args=args), text=None, thought=False)


def _text(t):
    return SimpleNamespace(function_call=None, text=t, thought=False)


class FakeModels:
    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    async def generate_content(self, model, contents, config):
        self.seen.append(contents[-1])
        return self.script.pop(0)


def _client(script):
    models = FakeModels(script)
    return SimpleNamespace(aio=SimpleNamespace(models=models)), models


def test_other_visitors_invoices_never_reach_the_model(monkeypatch):
    client, models = _client([_resp(_call("list_invoices", {})), _resp(_text("One invoice is open."))])
    monkeypatch.setattr(billing_agent, "_get_client", lambda: client)
    monkeypatch.setattr(billing_agent.types.Part, "from_function_response", staticmethod(lambda name, response: response))
    monkeypatch.setattr(billing_agent.types, "Content", lambda role, parts: SimpleNamespace(role=role, parts=parts))
    api = FakeAPI()
    out = asyncio.run(billing_agent.ask("What is open?", "ctx", api=api, allowed={"INV2-MINE"}))
    sent_back = json.dumps(models.seen[-1].parts, default=str)
    assert "INV2-MINE" in sent_back and "INV2-OTHER" not in sent_back and "someone@else" not in sent_back
    assert out["answer"] == "One invoice is open."


def test_a_reminder_is_only_proposed_never_sent(monkeypatch):
    client, _ = _client([_resp(_call("send_invoice_reminder", {"invoice_id": "INV2-MINE", "subject": "s", "note": "n"})),
                         _resp(_text("Waiting for your approval."))])
    monkeypatch.setattr(billing_agent, "_get_client", lambda: client)
    monkeypatch.setattr(billing_agent.types.Part, "from_function_response", staticmethod(lambda name, response: response))
    monkeypatch.setattr(billing_agent.types, "Content", lambda role, parts: SimpleNamespace(role=role, parts=parts))
    api = FakeAPI()
    out = asyncio.run(billing_agent.ask("Remind them", "ctx", api=api, allowed={"INV2-MINE"}))
    assert api.calls == []  # nothing ran against PayPal
    assert out["proposals"] and out["proposals"][0]["method"] == "send_invoice_reminder"
    asyncio.run(billing_agent.approve(out["proposals"][0], api=api))
    assert api.calls == [("send_invoice_reminder", {"invoice_id": "INV2-MINE", "subject": "s", "note": "n"})]


def test_only_reminders_can_be_approved():
    with pytest.raises(ValueError):
        asyncio.run(billing_agent.approve({"method": "delete_invoice", "args": {}}, api=FakeAPI()))
