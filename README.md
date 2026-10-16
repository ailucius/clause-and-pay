# Clause & Pay

Invoices that read the contract first.

Small suppliers and freelancers often work under a written contract that says exactly when they may invoice, how much, and what happens when the client pays late. In practice the contract sits in a folder, invoices are typed by hand, and a disputed invoice turns into an argument about what the contract says.

Clause & Pay is an AI billing agent built on PayPal Invoicing:

1. **Read the contract.** Upload a contract or purchase order (PDF). The agent finds every payment milestone: amount, currency, due date, the acceptance criteria the deliverable must meet, the payment terms, the late-payment clause and any condition that must be met before invoicing.
2. **Show its work.** Every item carries the contract's own sentence and its page number, and every quote is checked against the page text. A quote that is not found on any page is marked "Not found: check by hand" and is never used silently.
3. **Check the delivery before billing.** When you mark a milestone delivered, the agent compares your delivery note with that milestone's acceptance criteria and recommends **send** or **hold**, criterion by criterion. It recommends send only when every criterion is clearly met.
4. **Create and send the PayPal invoice.** After you approve, it creates the invoice through the PayPal Invoicing API v2 (sandbox): the line item names the milestone, the description quotes the contract clause with its page, and the payment term follows the contract (for example NET_30).
5. **Chase late payment with the contract's words.** For an overdue invoice it drafts a reminder that quotes the payment terms and the late-payment clause, computes the interest the clause allows, and sends it through the PayPal remind endpoint after you approve.

A person approves every action that touches money.

## Run it

Requirements: Python 3.12 or newer, a Gemini API key (Google AI Studio), and a PayPal developer account with a **Sandbox** REST app.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # fill in GEMINI_API_KEY, PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET
set -a; . ./.env; set +a
uvicorn app.server:app --port 8000
```

Open http://127.0.0.1:8000 and press **Use the sample contract**.

PayPal sandbox set-up:

1. Sign in at https://developer.paypal.com and open **Apps & Credentials**, **Sandbox** tab.
2. **Create App** (type Merchant). Copy the Client ID and Secret into `.env`.
3. Under **Testing Tools > Sandbox Accounts** use the default *Personal* account's email as the invoice recipient, and log in to https://www.sandbox.paypal.com with it to pay the invoice.

Without PayPal keys the app still reads contracts and checks deliveries; the invoice buttons explain that PayPal is not configured.

## Demo walkthrough

1. **Use the sample contract** (a fictional services agreement between Northwind Analytics Ltd and Harbour City Council, 3 pages, 4 milestones worth USD 48,000).
2. Select **M1 Inception report**, paste a delivery note such as: *"Inception report emailed as PDF on 10 Dec 2026. It sets out the survey method, a sample of 1,250 passengers and a dated work plan for M2, M3 and M4. Ada Brennan accepted it in writing on 12 Dec 2026."* and press **Mark delivered and check**: three criteria met, recommendation **send**.
3. Try **M2** with *"Dataset delivered with 980 survey responses. Data dictionary to follow next week."*: recommendation **hold**, because the contract asks for at least 1,200 responses and a data dictionary.
4. For M1, enter the sandbox buyer's email and **Approve and send invoice**. Open the invoice in PayPal and pay it with the sandbox buyer, then **Check payment status**.
5. For an unpaid invoice, set "pretend today is" to a date after the due date and **Draft a reminder**: it quotes clause 5.2 and clause 5.4 and states the interest accrued.

## How it is built

| Part | What it does |
| --- | --- |
| `app/contract_reader.py` | Extracts page text with PyMuPDF and asks Gemini for the payment schedule as JSON (schema in `app/schedule.py`), with page markers so every quote has a page. |
| `app/verify.py` | Normalises quotes and page text (quotes, dashes, hyphenated line breaks, whitespace) and checks each quote is really on its page; corrects the page if it sits on another one. |
| `app/schedule.py` | The checked schedule: amounts as exact decimals, ISO dates, verification status per quote, and a warning when the milestones do not add up to the contract total. |
| `app/agent.py` | Builds the PayPal invoice body, runs the delivery check (Gemini, JSON schema; hold unless every criterion is a clear yes), and drafts reminders with the late-payment interest. |
| `app/paypal_client.py` | A small async client for PayPal Invoicing v2: OAuth client credentials, next invoice number, create draft, send, remind, get, list, record payment. The token stays in memory and is never logged. |
| `app/server.py` | FastAPI API and the single page in `app/static/`. |

**AI tools used.** Google Gemini (`gemini-3.8-flash` by default, set `CLAUSEPAY_MODEL` to change it) reads the contract and judges deliveries, always with JSON schema output. The model never decides on its own: quotes are verified by code, amounts and dates are parsed by code, and a person approves every invoice and reminder. Claude (Anthropic) was used as a coding assistant.

**PayPal Agent Toolkit.** PayPal's `paypal-agent-toolkit` package exposes invoicing tools (create, send, remind, get, list) for OpenAI Agents, LangChain, CrewAI and Bedrock. Clause & Pay keeps its own thin REST client instead, because the agent's actions are fixed steps that a person approves (not free tool calls), and an async client is easier to test against a mock server. The request bodies are the same Invoicing v2 shapes, so the toolkit's handlers could be swapped in.

## Tests

```bash
pytest -q
```

The tests run offline: quote verification on the sample PDF, amount and date parsing, the invoice and reminder bodies, the delivery-check gate, and the PayPal client against a mock server.

`python scripts/live_read.py` reads the sample with the live model and prints counts only.

## Licence

Apache 2.0. The sample contract and every name in it are fictional.
