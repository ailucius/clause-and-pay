"""Builds samples/northwind-harbour-services-agreement.pdf.

The contract is fictional: Northwind Analytics Ltd and Harbour City Council are
invented names, and every figure is made up for the demo. Run:

    python samples/make_sample_contract.py
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = Path(__file__).with_name("northwind-harbour-services-agreement.pdf")

styles = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=16, spaceAfter=8)
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4)
P = ParagraphStyle("P", parent=styles["BodyText"], fontSize=10, leading=14, spaceAfter=6)
SMALL = ParagraphStyle("S", parent=P, fontSize=8.5, leading=11)


def clause(num: str, text: str) -> Paragraph:
    return Paragraph(f"<b>{num}</b> {text}", P)


def build() -> Path:
    story = []
    story += [
        Paragraph("SERVICES AGREEMENT", H1),
        Paragraph("Harbour City Transport Data Study (Contract ref. HCC-2026-117)", P),
        Paragraph("This is a fictional sample contract made for a software demonstration. "
                  "The parties, people and figures are invented.", SMALL),
        Spacer(1, 6 * mm),
        Paragraph("1. Parties", H2),
        clause("1.1", "This Agreement is made between Harbour City Council, of 1 Quay Street, Harbour City "
                      "(the \"Client\"), and Northwind Analytics Ltd, of 44 Mill Lane, Northwind "
                      "(the \"Supplier\")."),
        clause("1.2", "The Client Project Manager is Ms. Ada Brennan. The Supplier Project Lead is Mr. Tom Okafor."),
        Paragraph("2. Scope of Services", H2),
        clause("2.1", "The Supplier shall carry out a study of passenger flows on the Harbour City bus and ferry "
                      "network, including a passenger survey, an analysis of ticketing data and recommendations "
                      "for timetable changes."),
        clause("2.2", "The Services shall be delivered in four milestones as set out in Schedule 1 (Payment Schedule)."),
        clause("2.3", "The Services shall start on 1 September 2026 and shall be completed no later than 26 March 2027."),
        Paragraph("3. Contract Price", H2),
        clause("3.1", "The total Contract Price is USD 48,000.00, exclusive of applicable taxes, and covers all fees "
                      "and expenses of the Supplier."),
        clause("3.2", "The Contract Price is fixed and shall be paid in instalments against the milestones in Schedule 1. "
                      "No other payment shall be due."),
        Paragraph("4. Acceptance", H2),
        clause("4.1", "Each deliverable shall be submitted to the Client Project Manager by email in PDF format."),
        clause("4.2", "The Client shall accept or reject a deliverable in writing within ten (10) working days of "
                      "receipt. A rejection shall state the reasons in writing."),
        clause("4.3", "The Supplier may issue an invoice for a milestone only after the Client has accepted the "
                      "related deliverable in writing."),
        PageBreak(),
        Paragraph("5. Invoicing and Payment", H2),
        clause("5.1", "Each invoice shall quote the contract reference HCC-2026-117, the milestone number and the "
                      "amount due for that milestone."),
        clause("5.2", "The Client shall pay each valid invoice within thirty (30) days of receipt."),
        clause("5.3", "Invoices shall be sent electronically and may be paid by PayPal or by bank transfer."),
        clause("5.4", "If the Client fails to pay a valid invoice by its due date, interest shall accrue on the overdue "
                      "amount at the rate of 1.5% per month from the due date until the date of payment."),
        clause("5.5", "The Client may withhold payment of any amount that it disputes in good faith, provided that it "
                      "notifies the Supplier in writing of the reasons within ten (10) days of receiving the invoice."),
        Paragraph("6. Personnel", H2),
        clause("6.1", "The Supplier shall provide a team led by a senior transport analyst with at least ten years of "
                      "relevant experience."),
        clause("6.2", "The Supplier shall not replace the Supplier Project Lead without the prior written consent of "
                      "the Client, which shall not be unreasonably withheld."),
        Paragraph("7. Data Protection", H2),
        clause("7.1", "Survey responses shall be collected anonymously. The Supplier shall not collect names, contact "
                      "details or other personal data from survey respondents."),
        clause("7.2", "All ticketing data supplied by the Client shall be returned or deleted at the end of the Agreement."),
        PageBreak(),
        Paragraph("Schedule 1: Payment Schedule", H2),
        Paragraph("The Contract Price shall be paid in the following four instalments.", P),
    ]
    rows = [
        ["Milestone", "Deliverable", "Amount (USD)", "Due"],
        ["M1", "Inception report", "7,200.00", "9 October 2026"],
        ["M2", "Passenger survey dataset", "14,400.00", "27 November 2026"],
        ["M3", "Draft final report", "16,800.00", "29 January 2027"],
        ["M4", "Final report and presentation", "9,600.00", "26 March 2027"],
    ]
    table = Table(rows, colWidths=[22 * mm, 70 * mm, 32 * mm, 40 * mm])
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 9.5),
        ("GRID", (0, 0), (-1, -1), 0.4, "#8a94a3"),
        ("BACKGROUND", (0, 0), (-1, 0), "#e6ebf2"),
        ("ALIGN", (2, 1), (2, -1), "RIGHT"),
    ]))
    story += [table, Spacer(1, 5 * mm)]
    story += [
        Paragraph("Acceptance criteria for each milestone", H2),
        clause("S1.1", "M1 Inception report: the report shall set out the survey method, a sample size of at least "
                       "1,200 passengers and a work plan with dates for every later milestone."),
        clause("S1.2", "M2 Passenger survey dataset: the dataset shall contain at least 1,200 completed and "
                       "anonymised survey responses, with a data dictionary describing every field."),
        clause("S1.3", "M3 Draft final report: the draft shall analyse the survey and the ticketing data for every "
                       "bus and ferry route and shall include at least three timetable recommendations with their "
                       "expected effect on passenger numbers."),
        clause("S1.4", "M4 Final report and presentation: the final report shall address every written comment the "
                       "Client made on the draft, and the Supplier shall present the findings to the Client's "
                       "transport committee."),
        clause("S1.5", "Payment of each instalment is subject to clause 4.3 and clause 5.2 of this Agreement."),
        Spacer(1, 10 * mm),
        Paragraph("Signed for Harbour City Council: Ada Brennan, Client Project Manager, 20 August 2026", SMALL),
        Paragraph("Signed for Northwind Analytics Ltd: Tom Okafor, Director, 20 August 2026", SMALL),
    ]
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm,
                            title="Services Agreement HCC-2026-117 (fictional sample)")

    def footer(canvas, d):
        canvas.setFont("Helvetica", 8)
        canvas.drawString(20 * mm, 10 * mm, f"HCC-2026-117 fictional sample contract  |  page {d.page}")

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return OUT


if __name__ == "__main__":
    print(build())
