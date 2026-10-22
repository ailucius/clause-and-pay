# Clause & Pay

Invoices that read the contract first.

**Live demo (PayPal sandbox, fictional sample contract):** https://clause-and-pay-384599766402.europe-west1.run.app

Small suppliers and freelancers often work under a written contract that says exactly when they may invoice, how much, and what happens when the client pays late. In practice the contract sits in a folder, invoices are typed by hand, and a disputed invoice turns into an argument about what the contract says.

Clause & Pay is an AI billing agent built on PayPal Invoicing, PayPal Webhooks and the PayPal Agent Toolkit:

1. **Read the contract.** Upload a contract or purchase order (PDF). The agent finds every payment milestone: amount, currency, due date, the acceptance criteria the deliverable must meet, the payment terms, the late-payment clause and any condition that must be met before invoicing.
2. **Show its work.** Every item carries the contract's own sentence and its page number, and every quote is checked against the page text. A quote that is not found on any page is marked "Not found: check by hand" and is never used silently.
3. **Check the delivery proof before billing.** Upload the evidence as a file (the client's acceptance email as a PDF, a signed certificate as an image, a handover note as a Word file) or type a delivery note. Gemini checks it against the milestone's quoted acceptance criteria and the contract's condition for invoicing, and answers per item: **met**, **not met** or **not shown**, with the evidence's own words. For a PDF or Word file every quote is checked against the file's text by code, and a "met" whose words are not in the file does not count. Nothing read from an image or a scan can be checked that way, so those items are marked **confirm by eye** and never count as met by themselves. The invoice is offered only when every item is met; you can still send it over the hold, with a stated reason that goes in the ledger. The check is advice, not a decision: the model can be wrong, and a file can be written to mislead it (a quote found in the file shows the words are there, not that they are true). You read the quotes and decide.
4. **Create and send the PayPal invoice.** After you approve, it creates the invoice through the PayPal Invoicing API v2 (sandbox): the line item names the milestone, the description quotes the contract clause with its page, and the payment term follows the contract (for example NET_30).
5. **Hear back from PayPal.** A PayPal webhook, with its signature verified by PayPal's verify-webhook-signature API, marks the milestone paid. The page updates by itself and the activity log names the verified event.
6. **Ask the billing agent.** Gemini answers questions about the contract and its invoices by calling tools from the PayPal Agent Toolkit. Reads (`list_invoices`, `get_invoice`, `search_invoicing`) run straight away. `send_invoice_reminder` is never run by the model: the call becomes a proposal you approve on the page, and only then does the toolkit send it.
7. **Chase with the contract's words.** Before the due date a reminder is courteous: the payment terms with their page, and the due date. For an overdue invoice it quotes the late-payment clause and computes the interest the clause allows.
8. **Keep the books in AG Grid.** The payment schedule, the invoices and a ledger of every invoice event (drafted, sent, reminded, payment recorded, paid, cancelled, and any hold you overrode, with your reason), each with its amount and time, from the app's own PayPal calls and from PayPal's verified webhook. The ledger can be searched and downloaded as CSV.

A person approves every action that touches money.

## Run it

Requirements: Python 3.12 or newer, Gemini (a Google AI Studio key, or Vertex AI with your gcloud credentials), and a PayPal developer account with a **Sandbox** REST app.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pip install --no-deps paypal-agent-toolkit==1.11.0   # the toolkit's core, without its agent-framework extras
cp .env.example .env   # fill in GEMINI_API_KEY, PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET (and PAYPAL_WEBHOOK_ID)
set -a; . ./.env; set +a
uvicorn app.server:app --port 8000
```

Gemini on Vertex AI instead of a key (as the hosted demo runs it): `gcloud auth application-default login`, then set `GOOGLE_GENAI_USE_VERTEXAI=true`, `GOOGLE_CLOUD_PROJECT=<your project>` and `GOOGLE_CLOUD_LOCATION=global` and leave `GEMINI_API_KEY` empty.

Open http://127.0.0.1:8000 and press **Use the sample contract**.

PayPal sandbox set-up:

1. Sign in at https://developer.paypal.com and open **Apps & Credentials**, **Sandbox** tab.
2. **Create App** (type Merchant) on a sandbox *Business* account, and tick **Invoicing** under the app's features (without it every invoicing call returns 403). Copy the Client ID and Secret into `.env`.
3. To see payments arrive by themselves, the app must be reachable over HTTPS (for example on Cloud Run). Add a webhook for `https://<your host>/api/paypal/webhook` with the events `INVOICING.INVOICE.PAID`, `INVOICING.INVOICE.UPDATED` and `INVOICING.INVOICE.CANCELLED`, and put its ID in `PAYPAL_WEBHOOK_ID`. Without it, **Check payment status** asks PayPal directly.
4. Pay an invoice from its PayPal page with a sandbox *Personal* account or a sandbox test card, or press **Sandbox: record the client's payment**, which logs a bank transfer with the Agent Toolkit's `record_payment_for_invoice`.

Without PayPal keys the app still reads contracts and checks deliveries; the invoice buttons explain that PayPal is not configured.

## Demo walkthrough

1. **Use the sample contract** (a fictional services agreement between Northwind Analytics Ltd and Harbour City Council, 3 pages, 4 milestones worth USD 48,000), or upload it with **Upload a contract PDF** from `samples/`.
2. Select **M1 Inception report** in the payment schedule and upload `samples/evidence/m1-acceptance-email.pdf` (or press the sample button **M1: client's acceptance email (PDF)**): four of four items met (the three acceptance criteria and the client's written acceptance), each with the email's own sentence, found on page 1 of the file. Recommendation **send**. `m1-acceptance-certificate.png` is the same acceptance as an image: the model reads it, but every item is marked **confirm by eye**, so it holds until you have looked yourself and send with the override and your reason.
3. Select **M2** and upload `samples/evidence/m2-dataset-handover.docx`: recommendation **hold**. The note says 980 responses where the contract asks for 1,200, the data dictionary "will follow next week", and there is no written acceptance. To send anyway, tick the override and say why; the reason is recorded in the ledger.
4. For M1, enter the client's email and **Approve and send invoice**. **Open the invoice in PayPal** to see what the client gets. The invoice appears in **Invoices and ledger**, and the ledger shows it drafted and sent.
5. In **Ask the billing agent**, ask which invoices are still unpaid, then ask for a payment reminder for M1, and approve it. An answer takes 15 to 60 seconds (several model rounds and PayPal look-ups); the page says what the agent is doing and how long it has been working. The approved reminder goes in the ledger.
6. Pay the invoice (see step 4 of the set-up). With the webhook configured, M1 turns to paid on its own within a few seconds of PayPal's event, and the ledger gains a paid event that names the verified webhook. Without a webhook, **Check payment status** asks PayPal and records the same paid event.
7. For an unpaid invoice, set "pretend today is" to a date after the due date and **Draft a reminder**: it quotes clause 5.2 and clause 5.4 and states the interest accrued.

## How it is built

| Part | What it does |
| --- | --- |
| `app/contract_reader.py` | Extracts page text with PyMuPDF and asks Gemini for the payment schedule as JSON (schema in `app/schedule.py`), with page markers so every quote has a page. |
| `app/verify.py` | Normalises quotes and page text (quotes, dashes, hyphenated line breaks, whitespace) and checks each quote is really on its page; corrects the page if it sits on another one. |
| `app/schedule.py` | The checked schedule: amounts as exact decimals, ISO dates, verification status per quote, and a warning when the milestones do not add up to the contract total. |
| `app/agent.py` | Builds the PayPal invoice body, runs the typed-note delivery check (Gemini, JSON schema; hold unless every criterion is a clear yes), and drafts reminders with the late-payment interest. |
| `app/evidence.py` | Delivery proof as a file: recognises PDF, PNG, JPEG and .docx by their bytes, bounds size, pages and image dimensions, reads the text (PyMuPDF; the .docx XML scanned for its text runs), asks Gemini per item (met, not met, not shown, with a quote), checks every quote against the file's text and computes the recommendation. |
| `app/paypal_client.py` | A small async client for PayPal Invoicing v2: OAuth client credentials, next invoice number, create draft, send, remind, get, list, record payment. The token stays in memory and is never logged. |
| `app/billing_agent.py` | The billing agent: Gemini function calling over the PayPal Agent Toolkit's own tool definitions; reads run, a reminder becomes a proposal, and the agent only sees this contract's invoices. |
| `app/server.py` | FastAPI API, the ledger of invoice events, the verified PayPal webhook, per-visitor limits for the public demo, and the single page in `app/static/`. |
| `app/static/app.js` | The page: plain JS, no build step. AG Grid Community for the payment schedule, the invoices and the ledger; every value is inserted as text. |
| `samples/make_sample_evidence.py` | Builds the three fictional evidence files in `samples/evidence/`. |

**AI tools used.** Google Gemini (`gemini-3.8-flash` by default, set `CLAUSEPAY_MODEL` to change it) reads the contract and judges deliveries, always with JSON schema output. The model never decides on its own: quotes are verified by code, amounts and dates are parsed by code, and a person approves every invoice and reminder. Claude (Anthropic) was used as a coding assistant.

**AG Grid.** The schedule, invoice and ledger tables are AG Grid Community 36.2.0 (MIT), loaded from jsDelivr at a pinned version with a subresource-integrity hash. The grid theme is AG Grid's Theming API (`themeQuartz.withParams`) bound to the page's own colour tokens, so it follows light and dark mode. Cell renderers draw the status pills, overdue days and PayPal links as DOM nodes; selecting a row in the schedule opens that milestone; on a narrow screen the schedule moves the due date and then the amount under the name instead of scrolling; the ledger has a search box and CSV export (text that a spreadsheet would run as a formula is quoted). If the CDN is blocked, the page falls back to a plain list and plain tables.

**Evidence files.** Accepted: PDF (up to 30 pages), PNG or JPEG (up to 12,000 px a side), and Word .docx without macros, all up to 8 MB, recognised by their first bytes, never by name. HTML, SVG and anything else is refused before any AI call. Each upload counts against a per-visitor limit before it is parsed; the file is opened in a worker thread, at most 200,000 characters of its text are looked at, and the clean-up is a single linear pass, so a small file that unzips to megabytes of blanks cannot stall the server. The model sees, and quotes are checked against, at most 60,000 characters. The file is read for the check and dropped: it is never stored, served back or rendered, and the workspace keeps only its name, size and a hash. The prompt tells the model that the evidence is data, not instructions; that lowers the risk of a file written to steer the check, but does not remove it, which is why the check is advice and a person decides.

**PayPal Agent Toolkit.** The billing agent uses the framework-free core of `paypal-agent-toolkit` (tool names, descriptions, argument schemas and handlers), installed without its agent-framework extras (`pip install --no-deps paypal-agent-toolkit==1.11.0`, as in the Dockerfile). The fixed steps a person approves (create and send an invoice, check its status) go through the small async client in `app/paypal_client.py`, which is easier to test against a mock server; the request bodies are the same Invoicing v2 shapes.

## Tests

```bash
pytest -q
```

The tests run offline, with Gemini and PayPal mocked: quote verification on the sample PDF, amount and date parsing, the invoice and reminder bodies, the delivery-check gate, the PayPal client against a mock server, the webhook (signature check, duplicate events), the billing agent's approval gate, evidence files (types by their bytes, size, page and image limits, .docx text, quotes checked against the file, the met-only gate, nothing stored), the ledger (every event with its amount, the override reason, one paid event however the news arrives) and the page (AG Grid pinned with its hash, no HTML inserted).

`python scripts/live_read.py` reads the sample with the live model and prints counts only. `python scripts/live_evidence.py` checks the three sample evidence files against the sample contract with the live model and prints the verdicts.

Optional settings: `AGENT_TIMEOUT_SECONDS` (120) and `CHECK_TIMEOUT_SECONDS` (90) bound a slow model call; `CLAUSEPAY_AGENT_THINKING` (low, medium or high) sets the billing agent's thinking level. Demo limits: `AI_CALLS_PER_HOUR_PER_IP` (20), `PAYPAL_SENDS_PER_HOUR_PER_IP` (15), `EVIDENCE_UPLOADS_PER_HOUR_PER_IP` (30), `AI_CALLS_PER_DAY` (300) and `PAYPAL_SENDS_PER_DAY` (200). The visitor's address is the last entry of `X-Forwarded-For` (the one Google's front end appends on Cloud Run; earlier entries come from the client); set `TRUSTED_PROXY_HOPS` if more proxies sit in front (`LOG_CLIENT_ADDRESS=1` logs which address each limit uses, to compare with the platform's request log).

## Licence

Apache 2.0. The sample contract, the sample evidence and every name in them are fictional. AG Grid Community is MIT-licensed and loaded from its CDN, not bundled.
