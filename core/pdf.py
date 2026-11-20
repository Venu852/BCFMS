"""PDF exports (ReportLab)."""
import io
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#64748b")
LINE = colors.HexColor("#cbd5e1")
BAND = colors.HexColor("#f1f5f9")
GOOD = colors.HexColor("#15803d")
BAD = colors.HexColor("#b91c1c")

_base = getSampleStyleSheet()
TITLE = ParagraphStyle("T", parent=_base["Title"], fontSize=17, textColor=INK, alignment=0, spaceAfter=2)
SUB = ParagraphStyle("S", parent=_base["Normal"], fontSize=9, textColor=MUTED, spaceAfter=10)
H2 = ParagraphStyle("H", parent=_base["Heading2"], fontSize=11.5, textColor=INK, spaceBefore=12, spaceAfter=5)
BODY = ParagraphStyle("B", parent=_base["Normal"], fontSize=9.2, leading=13, textColor=INK)
SMALL = ParagraphStyle("Sm", parent=BODY, fontSize=7.6, leading=10, textColor=MUTED)
MONO = ParagraphStyle("M", parent=BODY, fontName="Courier", fontSize=7, leading=9)


def p(text, style=BODY):
    return Paragraph(escape(str(text or "—")).replace("\n", "<br/>"), style)


def _table(rows, widths, header=True):
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), BAND))
    else:
        style.append(("BACKGROUND", (0, 0), (0, -1), BAND))
    table.setStyle(TableStyle(style))
    return table


def _render(story, title, user):
    buffer = io.BytesIO()
    generated = timezone.localtime().strftime("%d %b %Y %H:%M")

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 10 * mm, f"BCFMS · {title} · generated {generated} by {user.display_name}")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm, title=title
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def case_file_pdf(case, report, user):
    local = lambda dt: timezone.localtime(dt).strftime("%d %b %Y %H:%M") if dt else "—"
    width = A4[0] - 36 * mm
    story = [
        p(f"Case file — {case.fir_number}", TITLE),
        p(f"Case ID {case.case_id} · {case.department.name} · Status: {case.get_status_display()}", SUB),
    ]
    verdict_style = ParagraphStyle("V", parent=BODY, textColor=GOOD if report["ok"] else BAD, fontName="Helvetica-Bold")
    story.append(
        p(
            "Blockchain verification: all records match their on-chain hashes."
            if report["ok"]
            else "Blockchain verification FAILED: one or more records do not match the ledger.",
            verdict_style,
        )
    )

    story.append(p("First Information Report", H2))
    story.append(
        _table(
            [
                [p("Filed"), p(f"{local(case.filed_at)} by {case.filed_by.display_name}")],
                [p("Crime type / priority"), p(f"{case.crime_type} / {case.get_priority_display()}")],
                [p("Incident"), p(f"{case.incident_date:%d %b %Y} at {case.incident_location}, {case.district}")],
                [p("Sections applied"), p(case.sections_applied)],
                [p("Complainant"), p(f"{case.complainant_name}\n{case.complainant_contact}\n{case.complainant_address}".strip())],
                [p("Investigating Officer"), p(case.investigating_officer.display_name if case.investigating_officer else "Not assigned")],
                [p("Description"), p(case.description)],
            ],
            [40 * mm, width - 40 * mm],
            header=False,
        )
    )

    accused = list(case.accused.all())
    if accused:
        story.append(p("Accused", H2))
        rows = [[p("Name"), p("Age"), p("Gender"), p("Status"), p("Address")]]
        rows += [[p(a.name), p(a.age), p(a.gender), p(a.get_status_display()), p(a.address)] for a in accused]
        story.append(_table(rows, [42 * mm, 14 * mm, 18 * mm, 26 * mm, width - 100 * mm]))

    notes = list(case.notes.filter(removed=False).select_related("author"))
    if notes:
        story.append(p("Investigation notes", H2))
        for note in reversed(notes):
            story.append(p(f"{note.title} — {note.author.display_name}, {local(note.created_at)}", ParagraphStyle("nb", parent=BODY, fontName="Helvetica-Bold")))
            story.append(p(note.text))
            story.append(Spacer(1, 5))

    evidence = list(case.evidence.select_related("custodian"))
    if evidence:
        story.append(p("Evidence", H2))
        rows = [[p("ID"), p("Title / file"), p("Custodian"), p("SHA-256")]]
        rows += [
            [p(e.uid), p(f"{e.title}\n{e.original_name}"), p(e.custodian.display_name), p(e.sha256, MONO)] for e in evidence
        ]
        story.append(_table(rows, [26 * mm, 52 * mm, 32 * mm, width - 110 * mm]))
        for e in evidence:
            for r in e.reports.select_related("expert"):
                story.append(Spacer(1, 6))
                story.append(p(f"Forensic report on {e.uid}: {r.title} — {r.expert.display_name}", ParagraphStyle("fb", parent=BODY, fontName="Helvetica-Bold")))
                story.append(p(f"Findings: {r.findings}"))
                if r.conclusion:
                    story.append(p(f"Conclusion: {r.conclusion}"))

    if hasattr(case, "chargesheet"):
        cs = case.chargesheet
        story.append(p("Chargesheet", H2))
        story.append(
            _table(
                [
                    [p("Filed"), p(f"{local(cs.filed_at)} by {cs.filed_by.display_name}")],
                    [p("Court"), p(cs.court)],
                    [p("Sections charged"), p(cs.sections)],
                    [p("Summary"), p(cs.summary)],
                    [p("Witnesses"), p(cs.witnesses)],
                ],
                [40 * mm, width - 40 * mm],
                header=False,
            )
        )

    hearings = list(case.hearings.filter(cancelled=False))
    if hearings:
        story.append(p("Hearings", H2))
        rows = [[p("Date"), p("Court"), p("Purpose")]] + [[p(local(h.scheduled_for)), p(h.court), p(h.purpose)] for h in hearings]
        story.append(_table(rows, [36 * mm, 60 * mm, width - 96 * mm]))

    if hasattr(case, "verdict"):
        v = case.verdict
        story.append(p("Verdict", H2))
        story.append(
            _table(
                [
                    [p("Outcome"), p(f"{v.get_outcome_display()} — {local(v.delivered_at)}, {v.judge.display_name}")],
                    [p("Sentence"), p(v.sentence)],
                    [p("Judgement"), p(v.judgement)],
                ],
                [40 * mm, width - 40 * mm],
                header=False,
            )
        )

    story.append(p("Integrity report", H2))
    rows = [[p("Record"), p("Result"), p("On-chain hash / value")]]
    for check in report["checks"]:
        rows.append(
            [
                p(f"{check['label']}\n{check['kind']}", SMALL),
                p("MATCH" if check["ok"] else "MISMATCH", ParagraphStyle("r", parent=SMALL, textColor=GOOD if check["ok"] else BAD)),
                p(check["onchain"] or check["note"], MONO),
            ]
        )
    story.append(_table(rows, [58 * mm, 20 * mm, width - 78 * mm]))
    return _render(story, f"Case file {case.fir_number}", user)


def statistics_pdf(stats, filters, user):
    width = A4[0] - 36 * mm
    story = [p("Crime statistics report", TITLE), p(filters or "All cases visible to the requesting officer", SUB)]
    story.append(
        _table(
            [
                [p("Total cases"), p("Open"), p("Resolved"), p("Convictions"), p("Resolution rate")],
                [p(stats["total"]), p(stats["open"]), p(stats["resolved"]), p(stats["convicted"]), p(f"{stats['resolution_rate']}%")],
            ],
            [width / 5] * 5,
        )
    )

    def section(title, rows, first):
        if not rows:
            return
        story.append(p(title, H2))
        data = [[p(first), p("Cases")]] + [[p(r["label"]), p(r["count"])] for r in rows]
        story.append(_table(data, [width - 30 * mm, 30 * mm]))

    story.append(p("Case status", H2))
    story.append(
        _table(
            [[p("Status"), p("Cases"), p("Share")]] + [[p(s["label"]), p(s["count"]), p(f"{s['share']}%")] for s in stats["segments"]],
            [width - 60 * mm, 30 * mm, 30 * mm],
        )
    )
    section("Crime type", stats["by_type"], "Crime type")
    section("District", stats["by_district"], "District")
    section("Cases filed per month", stats["monthly"], "Month")

    if stats["department_table"]:
        story.append(p("Department-wise", H2))
        head = [p("Department"), p("Total")] + [p(label, SMALL) for label in stats["status_labels"]] + [p("Resolved")]
        rows = [head] + [
            [p(d["name"]), p(d["total"])] + [p(c) for c in d["counts"]] + [p(f"{d['resolved_pct']}%")]
            for d in stats["department_table"]
        ]
        story.append(_table(rows, [48 * mm, 14 * mm] + [(width - 82 * mm) / 5] * 5 + [20 * mm]))
    return _render(story, "Crime statistics report", user)
