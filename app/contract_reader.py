"""Read a contract PDF and pull out its payment schedule, every line tied to its page."""
from __future__ import annotations

import pymupdf

from .llm import generate_json
from .schedule import Schedule, ScheduleOut, check_schedule

MAX_PAGES = 60

PROMPT = """You read contracts and purchase orders for a small supplier who needs to get paid.
Below is the full text of a contract. Each page starts with a marker like === PAGE 3 ===.

Extract the payment schedule:
- the parties: the supplier (who is paid and sends invoices) and the client (who pays);
- every milestone or instalment: its label, deliverable, amount, currency, when it is due,
  and the acceptance criteria the deliverable must meet before it can be invoiced;
- the payment terms (how many days the client has to pay), any late-payment interest or
  penalty, and any condition that must be met before an invoice may be issued.

Rules:
- Every quote must be copied word for word from the contract text, including numbers and
  punctuation. Do not paraphrase inside a quote. Keep quotes to one sentence or one table row.
- The page of a quote is the number in the nearest === PAGE n === marker above it.
- If something is not in the contract, use null. Never invent an amount, a date or a clause.

CONTRACT TEXT:
{text}
"""


def pdf_pages(data: bytes) -> list[str]:
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        if doc.page_count > MAX_PAGES:
            raise ValueError(f"The document has {doc.page_count} pages; the limit is {MAX_PAGES}.")
        return [p.get_text() for p in doc]
    finally:
        doc.close()


def numbered_text(pages: list[str]) -> str:
    return "\n".join(f"=== PAGE {i} ===\n{t.strip()}\n" for i, t in enumerate(pages, start=1))


async def read_contract(data: bytes) -> Schedule:
    pages = pdf_pages(data)
    if not any(p.strip() for p in pages):
        raise ValueError("No text layer found: the PDF looks scanned. Upload a text PDF.")
    raw = await generate_json(PROMPT.format(text=numbered_text(pages)), ScheduleOut)
    return check_schedule(raw, pages)
