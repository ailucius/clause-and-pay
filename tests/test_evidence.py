"""Delivery proof as a file: only safe file types, bounded size, every quote checked
against the file's own text, and the invoice offered only when every item is met."""
import asyncio
import io
import json
import struct
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import evidence, server
from app.evidence import EvidenceCheckOut, EvidenceError, load_evidence

from conftest import EVIDENCE

EMAIL = (EVIDENCE / "m1-acceptance-email.pdf").read_bytes()
CERT = (EVIDENCE / "m1-acceptance-certificate.png").read_bytes()
HANDOVER = (EVIDENCE / "m2-dataset-handover.docx").read_bytes()


def _docx(body_xml: str, extra: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", '<w:document xmlns:w="w"><w:body>' + body_xml + "</w:body></w:document>")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def _png_header(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00" + b"\x00" * 16


# ---- recognising and opening the file ----

def test_the_three_sample_files_load_as_their_kinds():
    pdf = load_evidence(EMAIL, "m1-acceptance-email.pdf")
    assert pdf.kind == "pdf" and pdf.text_checked and pdf.page_count == 1 and "sample of 1,250" in pdf.pages[0]
    img = load_evidence(CERT, "cert.png")
    assert img.kind == "png" and not img.text_checked and img.pages == [] and img.mime == "image/png"
    doc = load_evidence(HANDOVER, "handover.docx")
    assert doc.kind == "docx" and doc.text_checked and "980 completed survey responses" in doc.pages[0]
    assert "route_id B12" in doc.pages[0]  # a table row reads as one line


@pytest.mark.parametrize("data", [
    b"<html><body><script>alert(1)</script></body></html>",
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    b"MZ\x90\x00 an executable",
    b"GIF89a....",
    b"\xef\xbb\xbf<!doctype html><p>hi</p>",
    b"RIFF\x10\x00\x00\x00WEBPVP8 ",
])
def test_html_scripts_and_other_types_are_refused_by_their_bytes(data):
    with pytest.raises(EvidenceError) as e:
        load_evidence(data, "evidence.pdf")  # the name and extension do not matter
    assert e.value.status == 415


def test_empty_and_oversized_files_are_refused():
    with pytest.raises(EvidenceError) as e:
        load_evidence(b"", "x.pdf")
    assert e.value.status == 400
    with pytest.raises(EvidenceError) as e:
        load_evidence(b"%PDF-" + b"0" * evidence.MAX_EVIDENCE_BYTES, "x.pdf")
    assert e.value.status == 413


def test_a_pdf_over_the_page_limit_is_refused():
    import pymupdf
    doc = pymupdf.open()
    for _ in range(evidence.MAX_EVIDENCE_PAGES + 1):
        doc.new_page().insert_text((72, 72), "Delivered.")
    data = doc.tobytes()
    with pytest.raises(EvidenceError) as e:
        load_evidence(data, "long.pdf")
    assert e.value.status == 422 and "pages" in e.value.message


def test_a_broken_pdf_is_refused():
    with pytest.raises(EvidenceError) as e:
        load_evidence(b"%PDF-1.7\nthis is not really a pdf", "x.pdf")
    assert e.value.status == 422


def test_a_scanned_pdf_goes_to_the_model_as_a_file():
    import pymupdf
    doc = pymupdf.open()
    doc.new_page()  # no text layer
    ev = load_evidence(doc.tobytes(), "scan.pdf")
    assert ev.kind == "pdf" and not ev.text_checked and ev.pages == []


def test_image_sizes_are_read_from_the_header_and_huge_images_refused():
    assert evidence.image_size(CERT, "png") == (681, 681)
    with pytest.raises(EvidenceError) as e:
        load_evidence(_png_header(20000, 20000), "huge.png")
    assert e.value.status == 422
    with pytest.raises(EvidenceError):
        load_evidence(b"\x89PNG\r\n\x1a\n", "truncated.png")
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), "white").save(buf, "JPEG")
    assert evidence.image_size(buf.getvalue(), "jpeg") == (64, 48)
    assert load_evidence(buf.getvalue(), "photo.jpg").kind == "jpeg"


def test_word_files_without_a_document_or_with_macros_are_refused():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "not a word file")
    with pytest.raises(EvidenceError) as e:
        load_evidence(buf.getvalue(), "x.docx")
    assert e.value.status == 415
    with pytest.raises(EvidenceError) as e:
        load_evidence(_docx("<w:p><w:r><w:t>Accepted.</w:t></w:r></w:p>", {"word/vbaProject.bin": b"\x00"}), "x.docm")
    assert e.value.status == 415 and "Macro" in e.value.message
    with pytest.raises(EvidenceError) as e:
        load_evidence(_docx("<w:p></w:p>"), "empty.docx")
    assert e.value.status == 422


def test_docx_text_keeps_paragraphs_tabs_and_entities_and_resolves_nothing():
    xml = ('<w:p><w:r><w:t>Q&amp;A &lt;ok&gt;</w:t><w:tab/><w:t xml:space="preserve">done </w:t></w:r></w:p>'
           '<w:p><w:r><w:t>Second line</w:t><w:br/><w:t>third</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>&ext;</w:t></w:r></w:p>')
    text = evidence.docx_text(_docx(xml))
    assert text.splitlines()[:3] == ["Q&A <ok>\tdone", "Second line", "third"]
    assert "&ext;" in text  # an entity is left as written, never expanded or fetched


def test_file_names_lose_folders_and_control_characters():
    assert evidence.clean_name("../../etc/passwd", "pdf") == "passwd"
    assert evidence.clean_name("C:\\Users\\me\\proof\x07.pdf", "pdf") == "proof.pdf"
    assert evidence.clean_name("", "jpeg") == "evidence.jpg"
    assert len(evidence.clean_name("a" * 500 + ".pdf", "pdf")) == 120


# ---- the check (model mocked) ----

def _fake_model(items, kind="client acceptance email", summary="All shown."):
    seen = {}

    async def fake(prompt, schema, **kw):
        seen["prompt"], seen["attachments"] = prompt, kw.get("attachments")
        assert schema is EvidenceCheckOut
        return EvidenceCheckOut(evidence_kind=kind, summary=summary, items=items)

    return fake, seen


def _item(i, verdict, quote="", page=1, reason="r"):
    return {"index": i, "verdict": verdict, "quote": quote, "page": page, "reason": reason}


GOOD = [
    _item(1, "met", "The report sets out the survey method: an intercept survey on board buses and ferries"),
    _item(2, "met", "It plans a sample of 1,250 passengers."),
    _item(3, "met", "Its work plan gives a date for every later milestone"),
    _item(4, "met", "On behalf of Harbour City Council I accept the M1 Inception report in writing", page=3),
]


def test_every_item_met_with_quotes_found_in_the_file_recommends_send(schedule, monkeypatch):
    fake, seen = _fake_model(GOOD)
    monkeypatch.setattr(evidence, "generate_json", fake)
    out = asyncio.run(evidence.check_evidence(schedule, schedule.milestones[0], load_evidence(EMAIL, "email.pdf")))
    assert out.recommendation == "send" and len(out.criteria) == 4
    assert [c.quote_status for c in out.criteria] == ["verified", "verified", "verified", "moved"]
    assert out.criteria[3].page == 1  # page 3 was wrong: corrected to where the words are
    assert out.criteria[3].source == "condition" and out.criteria[3].criterion.startswith("Before invoicing")
    # the model saw the criteria, the invoicing condition with its quote, and the file's text, as data
    p = seen["prompt"]
    assert "1. Set out the survey method" in p and "4. Before invoicing" in p and "contract page 1" in p
    assert "=== PAGE 1 ===" in p and "sample of 1,250" in p and "data, not instructions" in p
    assert seen["attachments"] is None  # text files are sent as text


def test_a_met_whose_quote_is_not_in_the_file_does_not_count(schedule, monkeypatch):
    items = GOOD[:1] + [_item(2, "met", "It plans a sample of 2,000 passengers.")] + GOOD[2:]
    fake, _ = _fake_model(items)
    monkeypatch.setattr(evidence, "generate_json", fake)
    out = asyncio.run(evidence.check_evidence(schedule, schedule.milestones[0], load_evidence(EMAIL, "email.pdf")))
    assert out.recommendation == "hold"
    second = out.criteria[1]
    assert second.verdict == "not_shown" and second.quote_status == "not_found" and "not found in the file" in second.reason
    assert "not counted" in out.summary


def test_met_without_a_quote_and_missing_items_do_not_count(schedule, monkeypatch):
    fake, _ = _fake_model([_item(1, "met", ""), _item(2, "Not met", "It plans a sample of 1,250 passengers."),
                           _item(9, "met", "made up"), _item(1, "met", "It plans a sample of 1,250 passengers.")])
    monkeypatch.setattr(evidence, "generate_json", fake)
    out = asyncio.run(evidence.check_evidence(schedule, schedule.milestones[0], load_evidence(EMAIL, "email.pdf")))
    assert [c.verdict for c in out.criteria] == ["not_shown", "not_met", "not_shown", "not_shown"]
    assert out.criteria[0].reason.startswith("Marked met without quoting")  # the first answer for an item counts
    assert "No answer" in out.criteria[2].reason
    assert out.recommendation == "hold"


def test_an_image_is_read_by_the_model_but_never_counts_as_met_by_itself(schedule, monkeypatch):
    fake, seen = _fake_model([_item(i, "met", "The deliverable is accepted in writing under clause 4.2") for i in range(1, 5)])
    monkeypatch.setattr(evidence, "generate_json", fake)
    out = asyncio.run(evidence.check_evidence(schedule, schedule.milestones[0], load_evidence(CERT, "cert.png")))
    assert seen["attachments"] == [(CERT, "image/png")] and "attached" in seen["prompt"]
    # nothing read from an image can be checked by code: every "met" is to be confirmed by eye
    assert out.recommendation == "hold"
    assert {c.verdict for c in out.criteria} == {"unchecked"} and {c.quote_status for c in out.criteria} == {"not_text_checked"}
    assert "Confirm by eye" in out.criteria[0].reason and "confirm each item by eye" in out.summary.lower()
    assert out.file.text_checked is False and out.file.label == "PNG image"


def test_a_scan_is_treated_like_an_image(schedule, monkeypatch):
    import pymupdf
    doc = pymupdf.open()
    doc.new_page()
    fake, seen = _fake_model([_item(i, "met", "Accepted in writing.") for i in range(1, 5)])
    monkeypatch.setattr(evidence, "generate_json", fake)
    out = asyncio.run(evidence.check_evidence(schedule, schedule.milestones[0], load_evidence(doc.tobytes(), "scan.pdf")))
    assert seen["attachments"][0][1] == "application/pdf"
    assert out.recommendation == "hold" and {c.verdict for c in out.criteria} == {"unchecked"}


def test_a_word_file_is_checked_against_its_text_without_pages(schedule, monkeypatch):
    fake, seen = _fake_model([_item(1, "not_met", "The dataset contains 980 completed survey responses."),
                              _item(2, "not_met", "The data dictionary describing each field will follow next week."),
                              _item(3, "not_shown")], kind="handover note")
    monkeypatch.setattr(evidence, "generate_json", fake)
    out = asyncio.run(evidence.check_evidence(schedule, schedule.milestones[1], load_evidence(HANDOVER, "h.docx")))
    assert out.recommendation == "hold"
    assert [c.quote_status for c in out.criteria] == ["verified", "verified", "none"]
    assert all(c.page is None for c in out.criteria)
    assert "=== PAGE" not in seen["prompt"] and "980 completed" in seen["prompt"]


@pytest.mark.parametrize("with_condition", [False, True])
def test_no_criteria_means_hold_without_asking_the_model(schedule, monkeypatch, with_condition):
    """Even with an invoicing condition to check, no acceptance criteria means there is
    nothing to check the delivery against: the user decides."""
    async def boom(*a, **k):
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(evidence, "generate_json", boom)
    m = schedule.milestones[0].model_copy(update={"acceptance_criteria": [], "acceptance_cite": None})
    s = schedule if with_condition else schedule.model_copy(update={"invoice_condition": None})
    assert (s.invoice_condition is not None) == with_condition
    out = asyncio.run(evidence.check_evidence(s, m, load_evidence(EMAIL, "e.pdf")))
    assert out.recommendation == "hold" and out.criteria == [] and "nothing to check" in out.summary


# ---- the endpoint ----

def _upload(client, code, data, name="evidence.pdf", mime="application/pdf"):
    return client.post(f"/api/contracts/abcdef123456/milestones/{code}/evidence", files={"file": (name, data, mime)})


@pytest.fixture
def ai_on(monkeypatch):
    monkeypatch.setattr(server, "ai_configured", lambda: True)


def test_upload_checks_the_file_and_stores_the_result_not_the_file(workspace, ai_on, monkeypatch):
    fake, _ = _fake_model(GOOD)
    monkeypatch.setattr(evidence, "generate_json", fake)
    r = _upload(TestClient(server.app), "M1", EMAIL, name="../m1 email.pdf")
    assert r.status_code == 200, r.text
    w = r.json()
    assert w["status"]["M1"] == "delivered"
    check = w["checks"]["M1"]
    assert check["kind"] == "file" and check["recommendation"] == "send" and check["file"]["name"] == "m1 email.pdf"
    assert "4 of 4 items met" in w["log"][-1]["text"]
    dumped = json.dumps(w)
    assert "PDF-1." not in dumped and len(dumped) < 20000  # the file itself is not kept


def test_a_hold_from_the_file_holds_the_milestone(workspace, ai_on, monkeypatch):
    fake, _ = _fake_model([_item(1, "not_met", "The dataset contains 980 completed survey responses.")])
    monkeypatch.setattr(evidence, "generate_json", fake)
    r = _upload(TestClient(server.app), "M2", HANDOVER, name="h.docx", mime="application/octet-stream")
    assert r.status_code == 200 and r.json()["status"]["M2"] == "held"


def test_refused_files_do_not_use_the_ai_allowance(workspace, ai_on, monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(evidence, "generate_json", boom)
    client = TestClient(server.app)
    r = _upload(client, "M1", b"<html><script>alert(1)</script></html>", name="proof.pdf")
    assert r.status_code == 415
    assert not any(k[1] == "ai" for k in server._hits)
    assert workspace.checks == {} and workspace.status["M1"] == "planned"


def test_an_oversized_upload_is_refused_from_its_length(workspace, ai_on):
    client = TestClient(server.app)
    r = client.post("/api/contracts/abcdef123456/milestones/M1/evidence", content=b"x",
                    headers={"content-type": "multipart/form-data; boundary=b", "content-length": str(20 * 1024 * 1024)})
    assert r.status_code == 413


def test_no_new_evidence_once_the_milestone_is_invoiced(workspace, ai_on):
    workspace.invoices["M1"] = {"id": "INV2-X", "number": "0041", "status": "SENT"}
    client = TestClient(server.app)
    assert _upload(client, "M1", EMAIL).status_code == 409
    assert client.post("/api/contracts/abcdef123456/milestones/M1/deliver", json={"evidence": "done"}).status_code == 409


def test_a_slow_check_answers_504(workspace, ai_on, monkeypatch):
    async def slow(*a, **k):
        await asyncio.sleep(1)

    monkeypatch.setattr(evidence, "check_evidence", slow)
    monkeypatch.setattr(server, "CHECK_TIMEOUT", 0.05)
    assert _upload(TestClient(server.app), "M1", EMAIL).status_code == 504


def test_sample_evidence_is_served_by_name_only():
    client = TestClient(server.app)
    r = client.get("/api/sample-evidence/m1-acceptance-email.pdf")
    assert r.status_code == 200 and r.content == EMAIL and r.headers["content-type"] == "application/pdf"
    assert client.get("/api/sample-evidence/m2-dataset-handover.docx").headers["content-type"] == evidence.DOCX_MIME
    for bad in ("make_sample_evidence.py", "..%2Fnorthwind-harbour-services-agreement.pdf", "nope.pdf"):
        assert client.get(f"/api/sample-evidence/{bad}").status_code == 404


# ---- review fixes: bounded work before the AI allowance is spent ----

def _timed_load(data, name="x.docx"):
    import time
    t0 = time.perf_counter()
    try:
        ev = load_evidence(data, name)
    except EvidenceError as e:
        ev = e
    return ev, time.perf_counter() - t0


@pytest.mark.parametrize("body", [
    '<w:p><w:r><w:t xml:space="preserve">a' + " " * 6_000_000 + 'b</w:t></w:r></w:p>',  # MBs of preserved spaces
    '<w:p><w:r><w:t xml:space="preserve">a' + " \t" * 3_000_000 + '</w:t></w:r></w:p>',  # spaces and tabs
    '<w:p><w:r><w:t xml:space="preserve">a-' + "\u00a0" * 2_000_000 + '\n-</w:t></w:r></w:p>',  # other blanks
    "<w:t " * 1_300_000,  # tags that never close
    "<w:br" * 1_300_000,
], ids=["spaces", "spaces-and-tabs", "other-blanks", "open-text-tags", "open-break-tags"])
def test_a_small_docx_with_megabytes_of_blanks_is_read_in_well_under_a_second(body):
    data = _docx(body)
    assert len(data) < 40_000  # a few KB on the wire, megabytes once unzipped
    ev, seconds = _timed_load(data)
    assert seconds < 0.5, seconds
    if not isinstance(ev, EvidenceError):
        assert len(ev.pages[0]) <= evidence.MAX_TEXT_CHARS


def test_long_text_is_capped_before_quotes_are_checked():
    import pymupdf
    doc = pymupdf.open()
    for _ in range(25):
        page = doc.new_page()
        page.insert_textbox(page.rect, ("Delivered and accepted. " * 400), fontsize=4)
    ev = load_evidence(doc.tobytes(), "long.pdf")
    assert ev.text_checked and sum(len(p) for p in ev.pages) <= evidence.MAX_TEXT_CHARS
    big = evidence.tidy_text("word " * 200_000)
    assert len(big) == evidence.MAX_TEXT_CHARS


def test_uploads_count_against_a_per_visitor_limit_before_parsing(workspace, ai_on, monkeypatch):
    monkeypatch.setitem(server.LIMITS, "upload", (3, 3600))
    client = TestClient(server.app)
    codes = [_upload(client, "M1", b"<html>junk</html>").status_code for _ in range(4)]
    assert codes == [415, 415, 415, 429]


def test_the_file_is_opened_off_the_event_loop(workspace, ai_on, monkeypatch):
    import threading
    seen = {}
    real = evidence.load_evidence

    def spy(data, name):
        seen["thread"] = threading.current_thread() is threading.main_thread()
        return real(data, name)

    monkeypatch.setattr(evidence, "load_evidence", spy)
    fake, _ = _fake_model(GOOD)
    monkeypatch.setattr(evidence, "generate_json", fake)
    assert _upload(TestClient(server.app), "M1", EMAIL).status_code == 200
    assert seen["thread"] is False


@pytest.mark.parametrize("path", ["evidence", "deliver"])
def test_an_invoice_sent_while_the_check_ran_is_not_undone(workspace, ai_on, monkeypatch, path):
    """Two tabs: one checks evidence, the other sends the invoice meanwhile."""
    from app import agent

    async def check_while_invoiced(*a, **k):
        workspace.invoices["M1"] = {"id": "INV2-X", "number": "0041", "status": "SENT"}
        workspace.status["M1"] = "invoiced"
        if path == "evidence":
            return evidence.EvidenceCheck(file=evidence.file_info(load_evidence(EMAIL, "e.pdf")),
                                          recommendation="hold", criteria=[], summary="")
        return agent.DeliveryCheck(recommendation="hold", criteria=[], summary="")

    monkeypatch.setattr(evidence, "check_evidence", check_while_invoiced)
    monkeypatch.setattr(agent, "check_delivery", check_while_invoiced)
    client = TestClient(server.app)
    if path == "evidence":
        r = _upload(client, "M1", EMAIL)
    else:
        r = client.post("/api/contracts/abcdef123456/milestones/M1/deliver", json={"evidence": "done"})
    assert r.status_code == 409
    assert workspace.status["M1"] == "invoiced" and "M1" not in workspace.checks


def test_the_page_says_the_check_is_advice():
    from pathlib import Path
    js = (Path(server.ROOT) / "static" / "app.js").read_text()
    assert "advice" in js.lower() and "Confirm by eye" in js
