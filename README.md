# Clause & Pay

Invoices that read the contract first.

**Live demo (PayPal sandbox, fictional sample contract):** https://clause-and-pay-384599766402.europe-west1.run.app

Small suppliers and freelancers often work under a written contract that says exactly when they may invoice, how much, and what happens when the client pays late. In practice the contract sits in a folder, invoices are typed by hand, and a disputed invoice turns into an argument about what the contract says.

Clause & Pay is an AI billing agent built on PayPal Invoicing, PayPal Webhooks and the PayPal Agent Toolkit:

1. **Read the contract.** Upload a contract or purchase order (PDF). The agent finds every payment milestone: amount, currency, due date, the acceptance criteria the deliverable must meet, the payment terms, the late-payment clause and any condition that must be met before invoicing.
2. **Show its work.** Every item carries the contract's own sentence and its page number, and every quote is checked against the page text. A quote that is not found on any page is marked "Not found: check by hand" and is never used silently.
3. **Check the delivery before billing.** When you mark a milestone delivered, the agent compares your delivery note with that milestone's acceptance criteria and recommends **send** or **hold**, criterion by criterion. It recommends send only when every criterion is clearly met.
4. **Create and send the PayPal invoice.** After you approve, it creates the invoice through the PayPal Invoicing API v2 (sandbox): the line item names the milestone, the description quotes the contract clause with its page, and the payment term follows the contract (for example NET_30).
5. **Hear back from PayPal.** A PayPal webhook, with its signature verified by PayPal's verify-webhook-signature API, marks the milestone paid. The page updates by itself and the activity log names the verified event.
6. **Ask the billing agent.** Gemini answers questions about the contract and its invoices by calling tools from the PayPal Agent Toolkit. Reads (`list_invoices`, `get_invoice`, `search_invoicing`) run straight away. `send_invoice_reminder` is never run by the model: the call becomes a proposal you approve on the page, and only then does the toolkit send it.
7. **Chase with the contract's words.** Before the due date a reminder is courteous: the payment terms with their page, and the due date. For an overdue invoice it quotes the late-payment clause and computes the interest the clause allows.

A person approves every action that touches money.

## Run it

Requirements: Python 3.12 or newer, a Gemini API key (Google AI Studio), and a PayPal developer account with a **Sandbox** REST app.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pip install --no-deps paypal-agent-toolkit==1.11.0   # the toolkit's core, without its agent-framework extras
cp .env.example .env   # fill in GEMINI_API_KEY, PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET (and PAYPAL_WEBHOOK_ID)
set -a; . ./.env; set +a
uvicorn app.server:app --port 8000
```

Open http://127.0.0.1:8000 and press **Use the sample contract**.

PayPal sandbox set-up:

1. Sign in at https://developer.paypal.com and open **Apps & Credentials**, **Sandbox** tab.
2. **Create App** (type Merchant) on a sandbox *Business* account, and tick **Invoicing** under the app's features (without it every invoicing call returns 403). Copy the Client ID and Secret into `.env`.
3. To see payments arrive by themselves, the app must be reachable over HTTPS (for example on Cloud Run). Add a webhook for `https://<your host>/api/paypal/webhook` with the events `INVOICING.INVOICE.PAID`, `INVOICING.INVOICE.UPDATED` and `INVOICING.INVOICE.CANCELLED`, and put its ID in `PAYPAL_WEBHOOK_ID`. Without it, **Check payment status** asks PayPal directly.
4. Pay an invoice from its PayPal page with a sandbox *Personal* account or a sandbox test card, or press **Sandbox: record the client's payment**, which logs a bank transfer with the Agent Toolkit's `record_payment_for_invoice`.

Without PayPal keys the app still reads contracts and checks deliveries; the invoice buttons explain that PayPal is not configured.

## Demo walkthrough

1. **Use the sample contract** (a fictional services agreement between Northwind Analytics Ltd and Harbour City Council, 3 pages, 4 milestones worth USD 48,000), or upload it with **Upload a contract PDF** from `samples/`.
2. Select **M1 Inception report** and try *"Report sent to the client."*: recommendation **hold**. Then paste a delivery note such as: *"Inception report emailed as PDF on 5 Oct 2026. It sets out the survey method, a sample of 1,250 passengers and a dated work plan for M2, M3 and M4. Ada Brennan accepted it in writing on 9 Oct 2026."* and press **Mark delivered and check**: three criteria met, recommendation **send**.
3. Try **M2** with *"Dataset delivered with 980 survey responses. Data dictionary to follow next week."*: recommendation **hold**, because the contract asks for at least 1,200 responses and a data dictionary.
4. For M1, enter the client's email and **Approve and send invoice**. **Open the invoice in PayPal** to see what the client gets.
5. In **Ask the billing agent**, ask which invoices are still unpaid, then ask for a payment reminder for M1, and approve it.
6. Pay the invoice (see step 4 of the set-up). With the webhook configured, M1 turns to paid on its own within a few seconds of PayPal's event.
7. For an unpaid invoice, set "pretend today is" to a date after the due date and **Draft a reminder**: it quotes clause 5.2 and clause 5.4 and states the interest accrued.

## How it is built

| Part | What it does |
| --- | --- |
| `app/contract_reader.py` | Extracts page text with PyMuPDF and asks Gemini for the payment schedule as JSON (schema in `app/schedule.py`), with page markers so every quote has a page. |
| `app/verify.py` | Normalises quotes and page text (quotes, dashes, hyphenated line breaks, whitespace) and checks each quote is really on its page; corrects the page if it sits on another one. |
| `app/schedule.py` | The checked schedule: amounts as exact decimals, ISO dates, verification status per quote, and a warning when the milestones do not add up to the contract total. |
| `app/agent.py` | Builds the PayPal invoice body, runs the delivery check (Gemini, JSON schema; hold unless every criterion is a clear yes), and drafts reminders with the late-payment interest. |
| `app/paypal_client.py` | A small async client for PayPal Invoicing v2: OAuth client credentials, next invoice number, create draft, send, remind, get, list, record payment. The token stays in memory and is never logged. |
| `app/billing_agent.py` | The billing agent: Gemini function calling over the PayPal Agent Toolkit's own tool definitions; reads run, a reminder becomes a proposal, and the agent only sees this contract's invoices. |
| `app/server.py` | FastAPI API, the verified PayPal webhook, per-visitor limits for the public demo, and the single page in `app/static/`. |

**AI tools used.** Google Gemini (`gemini-3.8-flash` by default, set `CLAUSEPAY_MODEL` to change it) reads the contract and judges deliveries, always with JSON schema output. The model never decides on its own: quotes are verified by code, amounts and dates are parsed by code, and a person approves every invoice and reminder. Claude (Anthropic) was used as a coding assistant.

**PayPal Agent Toolkit.** The billing agent uses the framework-free core of `paypal-agent-toolkit` (tool names, descriptions, argument schemas and handlers), installed without its agent-framework extras (`pip install --no-deps paypal-agent-toolkit==1.11.0`, as in the Dockerfile). The fixed steps a person approves (create and send an invoice, check its status) go through the small async client in `app/paypal_client.py`, which is easier to test against a mock server; the request bodies are the same Invoicing v2 shapes.

## Tests

```bash
pytest -q
```

The tests run offline: quote verification on the sample PDF, amount and date parsing, the invoice and reminder bodies, the delivery-check gate, the PayPal client against a mock server, the webhook (signature check, duplicate events) and the billing agent's approval gate.

`python scripts/live_read.py` reads the sample with the live model and prints counts only.

## Licence

Apache 2.0. The sample contract and every name in it are fictional.
