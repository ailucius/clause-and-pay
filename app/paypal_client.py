"""A thin client for PayPal Invoicing v2 (sandbox by default).

Credentials come from PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET. The access token is
kept in memory only and is never logged or returned to the browser.
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import httpx

SANDBOX = "https://api-m.sandbox.paypal.com"
LIVE = "https://api-m.paypal.com"


class PayPalError(RuntimeError):
    def __init__(self, status: int, name: str, message: str, debug_id: str | None = None):
        super().__init__(f"PayPal {status} {name}: {message}")
        self.status, self.name, self.message, self.debug_id = status, name, message, debug_id


def paypal_configured() -> bool:
    return bool(os.environ.get("PAYPAL_CLIENT_ID") and os.environ.get("PAYPAL_CLIENT_SECRET"))


class PayPalClient:
    def __init__(self, client_id: str, secret: str, *, base_url: str = SANDBOX,
                 transport: httpx.AsyncBaseTransport | None = None, timeout: float = 30.0):
        self._id, self._secret = client_id, secret
        self.base_url = base_url
        self._http = httpx.AsyncClient(base_url=base_url, transport=transport, timeout=timeout)
        self._token: Optional[str] = None
        self._token_exp = 0.0

    @classmethod
    def from_env(cls, **kw) -> "PayPalClient":
        cid, sec = os.environ.get("PAYPAL_CLIENT_ID"), os.environ.get("PAYPAL_CLIENT_SECRET")
        if not (cid and sec):
            raise RuntimeError("PayPal is not configured: set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET.")
        base = LIVE if os.environ.get("PAYPAL_ENV", "sandbox").lower() == "live" else SANDBOX
        return cls(cid, sec, base_url=base, **kw)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- auth ----
    async def _bearer(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        r = await self._http.post("/v1/oauth2/token", data={"grant_type": "client_credentials"},
                                  auth=(self._id, self._secret),
                                  headers={"Accept": "application/json"})
        if r.status_code != 200:
            raise PayPalError(r.status_code, "AUTHENTICATION_FAILURE", "Could not get a PayPal access token.")
        body = r.json()
        self._token = body["access_token"]
        self._token_exp = time.time() + float(body.get("expires_in", 300))
        return self._token

    async def _call(self, method: str, path: str, *, json: Any = None, params: dict | None = None,
                    prefer_full: bool = False) -> Any:
        headers = {"Authorization": f"Bearer {await self._bearer()}", "Content-Type": "application/json"}
        if prefer_full:
            headers["Prefer"] = "return=representation"
        r = await self._http.request(method, path, json=json, params=params, headers=headers)
        if r.status_code >= 400:
            try:
                b = r.json()
            except ValueError:
                b = {}
            details = "; ".join(f"{d.get('field', '')} {d.get('issue', '')} {d.get('description', '')}".strip()
                                for d in b.get("details", []) or [])
            raise PayPalError(r.status_code, b.get("name", "ERROR"),
                              (b.get("message", r.reason_phrase) + (f" ({details})" if details else "")),
                              b.get("debug_id"))
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    # ---- Invoicing v2 ----
    async def next_invoice_number(self) -> str:
        return (await self._call("POST", "/v2/invoicing/generate-next-invoice-number"))["invoice_number"]

    async def create_draft(self, payload: dict) -> dict:
        """Create a draft invoice; returns the full invoice (id, status DRAFT, links)."""
        res = await self._call("POST", "/v2/invoicing/invoices", json=payload, prefer_full=True)
        if res and "id" not in res and res.get("href"):  # minimal representation: follow the link
            res = await self._call("GET", res["href"].replace(self.base_url, ""))
        return res

    async def send(self, invoice_id: str, *, subject: str | None = None, note: str | None = None,
                   send_to_invoicer: bool = True) -> Any:
        body = send_payload(subject=subject, note=note, send_to_invoicer=send_to_invoicer)
        return await self._call("POST", f"/v2/invoicing/invoices/{invoice_id}/send", json=body)

    async def remind(self, invoice_id: str, *, subject: str, note: str) -> None:
        await self._call("POST", f"/v2/invoicing/invoices/{invoice_id}/remind",
                         json=remind_payload(subject=subject, note=note))

    async def get(self, invoice_id: str) -> dict:
        return await self._call("GET", f"/v2/invoicing/invoices/{invoice_id}")

    async def list(self, *, page: int = 1, page_size: int = 20) -> dict:
        return await self._call("GET", "/v2/invoicing/invoices",
                                params={"page": page, "page_size": page_size, "total_required": "true"})

    async def record_payment(self, invoice_id: str, *, amount: str, currency: str, method: str = "BANK_TRANSFER",
                             payment_date: str | None = None, note: str | None = None) -> Any:
        body = {"method": method, "amount": {"currency_code": currency, "value": amount}}
        if payment_date:
            body["payment_date"] = payment_date
        if note:
            body["note"] = note[:2000]
        return await self._call("POST", f"/v2/invoicing/invoices/{invoice_id}/payments", json=body)


def send_payload(*, subject: str | None, note: str | None, send_to_invoicer: bool = True) -> dict:
    body: dict = {"send_to_invoicer": send_to_invoicer, "send_to_recipient": True}
    if subject:
        body["subject"] = subject[:4000]
    if note:
        body["note"] = note[:4000]
    return body


def remind_payload(*, subject: str, note: str) -> dict:
    return {"subject": subject[:4000], "note": note[:4000], "send_to_invoicer": True, "send_to_recipient": True}
