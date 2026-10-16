"""Ask the billing agent.

Gemini answers questions about the contract and its PayPal invoices by calling
tools from PayPal's own Agent Toolkit (paypal-agent-toolkit, the framework-free
shared core: each tool's name, description, parameter schema and handler).

Reading PayPal runs freely (list, get, search invoices). Anything that sends
something to the client, here a payment reminder, is never executed by the
model: the call is turned into a proposal that the user approves on the page,
and only then does the toolkit run it.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import date
from typing import Any, Optional

from google.genai import types
from paypal_agent_toolkit.shared.api import PayPalAPI
from paypal_agent_toolkit.shared.configuration import Context
from paypal_agent_toolkit.shared.tools import tools as PAYPAL_TOOLS

from .llm import _get_client, model_id
from .schedule import Schedule

READ_TOOLS = ("list_invoices", "get_invoice", "search_invoicing")
APPROVAL_TOOLS = ("send_invoice_reminder",)
MAX_ROUNDS = 6

SYSTEM = """You are the billing agent for a small supplier. You answer questions about one contract and the PayPal invoices raised under it.

Rules:
- Ground every statement about the contract in the CONTRACT section below and cite the page, like (p. 2). Never invent a clause, amount or date.
- For the live state of an invoice (sent, paid, overdue, amounts due) call the PayPal tools; do not guess from memory.
- Keep answers short: two to six sentences, plain words, amounts with their currency. Plain text only, no markdown.
- Only this contract's invoices exist for you; the tools show nothing else.
- If the user wants a reminder sent, call send_invoice_reminder with the invoice id, a subject and a note that quotes the contract's payment and late-payment wording with pages. That call does NOT send anything: it is queued for the user to approve. Say it is waiting for their approval; never say it was sent.
- You cannot create, cancel, refund or delete invoices here. Say so if asked.
"""


def _decls() -> list[types.FunctionDeclaration]:
    out = []
    for t in PAYPAL_TOOLS:
        m = t.get("method")
        if m not in READ_TOOLS + APPROVAL_TOOLS:
            continue
        desc = t.get("description", "")
        if m in APPROVAL_TOOLS:
            desc += "\n\nIn this app the call is queued for the user's approval; it is sent only after they approve it."
        out.append(types.FunctionDeclaration(name=m, description=desc,
                                             parameters_json_schema=t["args_schema"].model_json_schema()))
    return out


def paypal_api() -> PayPalAPI:
    return PayPalAPI(client_id=os.environ["PAYPAL_CLIENT_ID"], secret=os.environ["PAYPAL_CLIENT_SECRET"],
                     context=Context(sandbox=True, source="CLAUSE-AND-PAY"))


def contract_context(schedule: Schedule, invoices: dict[str, dict], status: dict[str, str], today: date) -> str:
    def clause(name: str, c) -> str:
        return f"- {name}: {c.summary} Quote (p. {c.cite.page}): \"{c.cite.quote}\"" if c else f"- {name}: not stated"

    lines = [f"Today is {today.isoformat()}.", "", "CONTRACT",
             f"- {schedule.contract_title} ({schedule.contract_reference or 'no reference'})",
             f"- Supplier (you): {schedule.supplier_name}. Client: {schedule.client_name}.",
             f"- Value: {schedule.currency} {schedule.total_value or 'not stated'}. Payment within {schedule.net_days or '?'} days.",
             clause("Payment terms", schedule.payment_terms),
             clause("Late payment", schedule.late_payment),
             clause("Before invoicing", schedule.invoice_condition),
             "", "MILESTONES"]
    for m in schedule.milestones:
        inv = invoices.get(m.code)
        inv_txt = f"PayPal invoice {inv['number']} (id {inv['id']}, status {inv.get('status')}, due {inv.get('due_date')})" if inv else "no invoice yet"
        lines.append(f"- {m.code} {m.name}: {m.currency} {m.amount}, due {m.due_as_written} "
                     f"(p. {m.payment_cite.page}); app status {status.get(m.code, 'planned')}; {inv_txt}")
    return "\n".join(lines)


def _trim(raw: Any, allowed: Optional[set[str]] = None) -> Any:
    """Keep the parts of a PayPal answer the model needs, so a long list stays small.
    With `allowed`, only this contract's invoices are passed on: the sandbox
    merchant account is shared by every visitor of the demo."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return str(raw)[:2000]

    def slim(inv: dict) -> dict:
        d = inv.get("detail", {}) or {}
        amt = inv.get("amount", {}) or {}
        due = inv.get("due_amount", {}) or {}
        return {"id": inv.get("id"), "number": d.get("invoice_number"), "status": inv.get("status"),
                "invoice_date": d.get("invoice_date"), "due_date": (d.get("payment_term") or {}).get("due_date"),
                "amount": amt.get("value"), "currency": amt.get("currency_code"),
                "due_amount": due.get("value"), "recipient": [
                    (r.get("billing_info") or {}).get("email_address") for r in inv.get("primary_recipients", []) or []]}

    if isinstance(data, dict) and isinstance(data.get("items"), list):
        items = [i for i in data["items"] if allowed is None or i.get("id") in allowed]
        return {"total_items": len(items), "items": [slim(i) for i in items[:25]]}
    if isinstance(data, dict) and str(data.get("id", "")).startswith("INV"):
        if allowed is not None and data.get("id") not in allowed:
            return {"error": "That invoice does not belong to this contract."}
        return slim(data)
    return data if len(json.dumps(data, default=str)) < 4000 else json.dumps(data, default=str)[:4000]


async def ask(question: str, context_text: str, api: Optional[PayPalAPI] = None,
              allowed: Optional[set[str]] = None) -> dict:
    """One question, answered with up to MAX_ROUNDS rounds of tool calls.
    `allowed`: the PayPal invoice ids of this contract; nothing else is shown or acted on."""
    api = api or paypal_api()
    client = _get_client()
    cfg = types.GenerateContentConfig(system_instruction=SYSTEM + "\n" + context_text,
                                      tools=[types.Tool(function_declarations=_decls())], temperature=0.2)
    contents: list[types.Content] = [types.Content(role="user", parts=[types.Part(text=question)])]
    trace: list[dict] = []
    proposals: list[dict] = []
    for _ in range(MAX_ROUNDS):
        resp = await client.aio.models.generate_content(model=model_id(), contents=contents, config=cfg)
        cand = resp.candidates[0]
        contents.append(cand.content)
        calls = [p.function_call for p in (cand.content.parts or []) if p.function_call]
        if not calls:
            text = "".join(p.text or "" for p in (cand.content.parts or []) if p.text and not getattr(p, "thought", False))
            return {"answer": text.strip(), "trace": trace, "proposals": proposals}
        replies = []
        for fc in calls:
            args = dict(fc.args or {})
            if fc.name in APPROVAL_TOOLS and allowed is not None and args.get("invoice_id") not in allowed:
                result = {"error": "That invoice does not belong to this contract."}
            elif fc.name in APPROVAL_TOOLS:
                pid = uuid.uuid4().hex[:10]
                proposals.append({"id": pid, "method": fc.name, "args": args})
                result: Any = {"status": "queued_for_user_approval", "proposal_id": pid}
            elif fc.name in READ_TOOLS:
                try:
                    if fc.name == "get_invoice" and allowed is not None and args.get("invoice_id") not in allowed:
                        raise ValueError("That invoice does not belong to this contract.")
                    result = _trim(await asyncio.to_thread(api.run, fc.name, args), allowed)
                except Exception as e:  # a PayPal or validation error goes back to the model as data
                    result = {"error": str(e)[:300]}
            else:
                result = {"error": "This tool is not available here."}
            trace.append({"tool": fc.name, "args": {k: v for k, v in args.items() if k in ("invoice_id", "page", "page_size", "status")},
                          "ok": not (isinstance(result, dict) and "error" in result)})
            replies.append(types.Part.from_function_response(name=fc.name, response={"result": result}))
        contents.append(types.Content(role="user", parts=replies))
    return {"answer": "I stopped after several PayPal lookups without a final answer. Try a narrower question.",
            "trace": trace, "proposals": proposals}


async def approve(proposal: dict, api: Optional[PayPalAPI] = None) -> Any:
    """Run an approved proposal through the toolkit."""
    if proposal.get("method") not in APPROVAL_TOOLS:
        raise ValueError("Only reminders can be approved here.")
    api = api or paypal_api()
    return await asyncio.to_thread(api.run, proposal["method"], proposal["args"])
