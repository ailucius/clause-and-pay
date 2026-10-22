"""Check that a quoted sentence really is on the page the model says it is on.

A quote counts as verified when its normalised text is found on the cited page.
If it is found on a different page, the page is corrected and the quote is
still verified (status "moved"). If it is found nowhere, it is flagged
"not_found" and the caller must show it as unverified; it is never silently kept.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_QUOTES = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ", " ": " ",
})


def normalise(text: str) -> str:
    """Lower-case, unify quotes and dashes, join hyphenated line breaks, collapse whitespace."""
    t = unicodedata.normalize("NFKC", text or "").translate(_QUOTES)
    t = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", t)  # word- \n word -> wordword
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()


@dataclass(frozen=True)
class QuoteCheck:
    status: str  # "verified" | "moved" | "not_found" | "empty"
    page: int | None  # 1-based page where the quote was found (or the cited page if not found)

    @property
    def ok(self) -> bool:
        return self.status in ("verified", "moved")


def _strip_ends(q: str) -> str:
    return q.strip().strip('"\'').rstrip(".;:, ").strip()


def check_quote(quote: str, cited_page: int | None, pages: list[str],
                norm_pages: list[str] | None = None) -> QuoteCheck:
    """`pages` holds the text of each page, index 0 = page 1. `norm_pages`, when given,
    is the same pages already normalised (to check many quotes against one document)."""
    q = normalise(_strip_ends(quote))
    if len(q) < 8:
        return QuoteCheck("empty", cited_page)
    if norm_pages is None:
        norm_pages = [normalise(p) for p in pages]
    if cited_page and 1 <= cited_page <= len(pages) and q in norm_pages[cited_page - 1]:
        return QuoteCheck("verified", cited_page)
    for i, p in enumerate(norm_pages, start=1):
        if q in p:
            return QuoteCheck("moved", i)
    return QuoteCheck("not_found", cited_page)
