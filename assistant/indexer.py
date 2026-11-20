"""Turns a case file into retrievable passages."""
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from . import embedder
from .models import DocumentChunk

PERSONAL = "PERSONAL"
CHUNK_CHARS = 1100
OVERLAP = 150


def split(text):
    """Split on paragraph boundaries into overlapping passages of about CHUNK_CHARS."""
    text = text.strip()
    if len(text) <= CHUNK_CHARS:
        return [text] if text else []
    chunks, current = [], ""
    for paragraph in [p.strip() for p in text.split("\n") if p.strip()]:
        while len(paragraph) > CHUNK_CHARS:  # a single very long paragraph
            head, paragraph = paragraph[:CHUNK_CHARS], paragraph[CHUNK_CHARS - OVERLAP :]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(head)
        if len(current) + len(paragraph) + 1 > CHUNK_CHARS and current:
            chunks.append(current)
            current = current[-OVERLAP:]
        current = f"{current}\n{paragraph}".strip()
    if current:
        chunks.append(current)
    return chunks


def _when(value):
    return timezone.localtime(value).strftime("%d %b %Y %H:%M") if value else ""


def case_documents(case):
    """Yield (source_type, source_id, label, url, text) for everything in the case file."""
    url = case.get_absolute_url()
    people = list(case.accused.all())
    accused = "; ".join(
        f"{a.name} ({a.age or 'age unknown'}, {a.gender or 'gender unknown'}, {a.get_status_display()})" for a in people
    )
    officer = case.investigating_officer.display_name if case.investigating_officer else "not assigned"
    yield (
        "FIR", case.case_id, "FIR", url,
        f"FIR number: {case.fir_number}\nCase ID: {case.case_id}\nStatus: {case.get_status_display()}\n"
        f"Crime type: {case.crime_type}\nPriority: {case.get_priority_display()}\n"
        f"Police station: {case.department.name}\nDistrict: {case.district}\n"
        f"Filed on: {_when(case.filed_at)} by {case.filed_by.display_name}\n"
        f"Incident date: {case.incident_date:%d %b %Y}\nIncident location: {case.incident_location}\n"
        f"Sections applied: {case.sections_applied}\nInvestigating Officer: {officer}\n"
        f"Complainant: {case.complainant_name}\n"
        f"Accused: {accused or 'none named'}\n"
        f"Complaint: {case.description}",
    )
    # Contact details and addresses live in their own passage so retrieval can withhold
    # them from officers whose access to the case does not include personal data.
    addresses = "; ".join(f"{a.name}: {a.address}" for a in people if a.address)
    yield (
        PERSONAL, case.case_id, "Personal details", url,
        f"Complainant {case.complainant_name}: contact {case.complainant_contact or 'not recorded'}, "
        f"address {case.complainant_address or 'not recorded'}.\nAddresses of accused: {addresses or 'not recorded'}",
    )
    for note in case.notes.filter(removed=False).select_related("author"):
        yield (
            "NOTE", note.uid, f"Investigation note {note.uid}: {note.title}", url + "#notes",
            f"Investigation note by {note.author.display_name} on {_when(note.created_at)}.\n{note.title}\n{note.text}",
        )
    for evidence in case.evidence.select_related("custodian"):
        e_url = reverse("evidence_detail", args=[evidence.uid])
        yield (
            "EVIDENCE", evidence.uid, f"Evidence {evidence.uid}: {evidence.title}", e_url,
            f"Evidence item {evidence.uid}: {evidence.title} ({evidence.get_kind_display()}, file {evidence.original_name}).\n"
            f"Collected on {_when(evidence.collected_at)} at {evidence.collected_location or 'unspecified location'}.\n"
            f"Current custodian: {evidence.custodian.display_name}.\n{evidence.description}",
        )
        if evidence.extracted_text:
            yield (
                "EVIDENCE_TEXT", evidence.uid, f"Evidence {evidence.uid} contents: {evidence.original_name}", e_url,
                evidence.extracted_text,
            )
        for report in evidence.reports.select_related("expert"):
            yield (
                "FORENSIC", report.uid, f"Forensic report {report.uid}: {report.title}", e_url,
                f"Forensic report on evidence {evidence.uid} ({evidence.title}) by {report.expert.display_name}, {_when(report.created_at)}.\n"
                f"{report.title}\nMethodology: {report.methodology}\nFindings: {report.findings}\nConclusion: {report.conclusion}",
            )
    if hasattr(case, "chargesheet"):
        cs = case.chargesheet
        yield (
            "CHARGESHEET", cs.uid, f"Chargesheet {cs.uid}", url + "#court",
            f"Chargesheet filed on {_when(cs.filed_at)} by {cs.filed_by.display_name} before {cs.court}.\n"
            f"Sections charged: {cs.sections}\nSummary: {cs.summary}\nWitnesses: {cs.witnesses}",
        )
    hearings = [h for h in case.hearings.all() if not h.cancelled]
    if hearings:
        yield (
            "HEARING", case.case_id, "Hearing schedule", url + "#court",
            "\n".join(f"Hearing on {_when(h.scheduled_for)} at {h.court}: {h.purpose}" for h in hearings),
        )
    if hasattr(case, "verdict"):
        v = case.verdict
        yield (
            "VERDICT", v.uid, f"Verdict {v.uid}", url + "#court",
            f"Verdict delivered on {_when(v.delivered_at)} by {v.judge.display_name}: {v.get_outcome_display()}.\n"
            f"Sentence: {v.sentence or 'none'}\nJudgement: {v.judgement}",
        )


def index_case(case):
    """Rebuild the passages and embeddings for one case."""
    chunks = []
    for source_type, source_id, label, url, text in case_documents(case):
        for position, passage in enumerate(split(text)):
            chunks.append(
                DocumentChunk(case=case, source_type=source_type, source_id=source_id, label=label[:200], url=url, position=position, text=passage)
            )
    vectors = embedder.embed([f"{case.fir_number} | {c.label}\n{c.text}" for c in chunks])
    if vectors is not None:
        for chunk, vector in zip(chunks, vectors):
            chunk.embedding = vector.tobytes()
    with transaction.atomic():
        DocumentChunk.objects.filter(case=case).delete()
        DocumentChunk.objects.bulk_create(chunks)
    return len(chunks)
