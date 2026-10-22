"""Builds the fictional delivery evidence in samples/evidence/ for trying the evidence check:

- m1-acceptance-email.pdf: the client's written acceptance of the M1 inception report
  (a PDF with a text layer, so every quote is checked against its text);
- m1-acceptance-certificate.png: the same acceptance as a signed certificate image
  (read by the model; its quotes are marked as not text-checked);
- m2-dataset-handover.docx: a dataset handover note that falls short of M2's criteria
  (980 responses where the contract asks for 1,200, and no data dictionary yet).

Every person, organisation, address and figure is invented. Run:

    python samples/make_sample_evidence.py
"""
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pymupdf
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = Path(__file__).with_name("evidence")
FICTION = ("Fictional sample made for a software demonstration. The people, organisations "
           "and email addresses are invented.")

styles = getSampleStyleSheet()
P = ParagraphStyle("P", parent=styles["BodyText"], fontSize=10.5, leading=15, spaceAfter=8)
HEAD = ParagraphStyle("Head", parent=P, fontSize=9.5, leading=13, spaceAfter=2, textColor="#333333")
H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=15, spaceAfter=10)
SMALL = ParagraphStyle("S", parent=P, fontSize=8, leading=10, textColor="#666666")
SIGN = ParagraphStyle("Sign", parent=P, fontName="Times-Italic", fontSize=20, leading=24, textColor="#1f3a5f")


def acceptance_email() -> Path:
    path = OUT / "m1-acceptance-email.pdf"
    headers = [
        ("From", "Ada Brennan &lt;ada.brennan@harbourcity.example&gt;"),
        ("To", "Tom Okafor &lt;tom.okafor@northwind.example&gt;"),
        ("Date", "Friday 9 October 2026, 14:12"),
        ("Subject", "HCC-2026-117 M1 Inception report: accepted"),
    ]
    story = [Paragraph(f"<b>{k}:</b> {v}", HEAD) for k, v in headers]
    story += [
        Spacer(1, 6 * mm),
        Paragraph("Dear Tom,", P),
        Paragraph("Thank you for the Inception report for contract HCC-2026-117, which you emailed to me as a PDF "
                  "on 5 October 2026.", P),
        Paragraph("I have reviewed it with the transport team. The report sets out the survey method: an intercept "
                  "survey on board buses and ferries, run on weekdays and at weekends. It plans a sample of 1,250 "
                  "passengers.", P),
        Paragraph("Its work plan gives a date for every later milestone: M2 Passenger survey dataset by 27 November "
                  "2026, M3 Draft final report by 29 January 2027, and M4 Final report and presentation by "
                  "26 March 2027.", P),
        Paragraph("On behalf of Harbour City Council I accept the M1 Inception report in writing under clause 4.2 "
                  "of the Agreement. You may now invoice milestone M1.", P),
        Paragraph("Kind regards,", P),
        Paragraph("Ada Brennan<br/>Client Project Manager, Harbour City Council", P),
        Spacer(1, 14 * mm),
        Paragraph(FICTION, SMALL),
    ]
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=20 * mm,
                      title="HCC-2026-117 M1 Inception report: accepted", author="Clause & Pay sample").build(story)
    return path


def acceptance_certificate() -> Path:
    """A signed certificate, rendered to a PNG: evidence the model reads as an image."""
    pdf = OUT / "_certificate.pdf"
    story = [
        Paragraph("HARBOUR CITY COUNCIL", HEAD),
        Paragraph("Certificate of Acceptance", H1),
        Paragraph("<b>Contract:</b> HCC-2026-117, Harbour City Transport Data Study", P),
        Paragraph("<b>Milestone:</b> M1 Inception report", P),
        Paragraph("<b>Received:</b> 5 October 2026, by email, in PDF format", P),
        Spacer(1, 3 * mm),
        Paragraph("The Client confirms that the Inception report sets out the survey method, a sample size of "
                  "1,250 passengers, and a work plan with dates for milestones M2, M3 and M4.", P),
        Paragraph("The deliverable is accepted in writing under clause 4.2 of the Agreement.", P),
        Spacer(1, 6 * mm),
        Paragraph("A. Brennan", SIGN),
        Paragraph("Ada Brennan, Client Project Manager<br/>9 October 2026", P),
        Spacer(1, 8 * mm),
        Paragraph(FICTION, SMALL),
    ]
    SimpleDocTemplate(str(pdf), pagesize=(150 * mm, 150 * mm), leftMargin=14 * mm, rightMargin=14 * mm,
                      topMargin=12 * mm, bottomMargin=10 * mm).build(story)
    path = OUT / "m1-acceptance-certificate.png"
    doc = pymupdf.open(pdf)
    try:
        doc[0].get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6)).save(path)
    finally:
        doc.close()
        pdf.unlink()
    return path


def _para(text: str, bold: bool = False) -> str:
    run_pr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:p><w:r>{run_pr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def _table(rows: list[list[str]]) -> str:
    cells = "".join("<w:tr>" + "".join(f"<w:tc>{_para(c)}</w:tc>" for c in row) + "</w:tr>" for row in rows)
    return f"<w:tbl>{cells}</w:tbl>"


def dataset_handover() -> Path:
    path = OUT / "m2-dataset-handover.docx"
    body = "".join([
        _para("HCC-2026-117 M2 Passenger survey dataset: handover note", bold=True),
        _para("From: Tom Okafor, Northwind Analytics Ltd. To: Ada Brennan, Harbour City Council. Date: 20 November 2026."),
        _para("The dataset file passenger_survey_m2.csv was uploaded to the Council's shared drive on 20 November 2026."),
        _para("The dataset contains 980 completed survey responses. Respondent names and contact details were not "
              "collected, so every response is anonymised."),
        _para("The data dictionary describing each field will follow next week."),
        _para("Fields in this delivery:"),
        _table([["Field", "Example"], ["route_id", "B12"], ["mode", "ferry"], ["day_type", "weekday"],
                ["trip_purpose", "work"]]),
        _para("We look forward to your review."),
        _para(FICTION),
    ])
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f"<w:body>{body}</w:body></w:document>")
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/'
                     'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="word/document.xml"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in (("[Content_Types].xml", content_types), ("_rels/.rels", rels),
                           ("word/document.xml", document)):
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 20, 9, 0, 0))  # fixed: same bytes on every build
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return path


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for p in (acceptance_email(), acceptance_certificate(), dataset_handover()):
        print(p.relative_to(OUT.parent.parent), p.stat().st_size, "bytes")
