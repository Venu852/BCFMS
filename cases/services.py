"""Case workflow operations.

Every operation follows the same order: compute the record's hash, send the
contract call, and only if the contract accepts it write to the database. A
reverted call raises ChainRejected and leaves the database untouched (the failed
transaction itself stays on-chain as evidence of the attempt).

`source` is the HttpRequest when called from a view (so the audit trail captures
the IP address) or a User when called from a script.
"""
import logging
import mimetypes

from core.audit import log as audit
from core.notify import notify
from ledger.services import user_send

from . import vault
from .models import Case, CaseShare, CustodyLog

logger = logging.getLogger(__name__)


class ChainRejected(Exception):
    def __init__(self, tx):
        self.tx = tx
        super().__init__(tx.error)


def _user(source):
    return source.user if hasattr(source, "META") else source


def _send(source, case, contract, method, case_ref=None, **args):
    ref = case_ref or case.case_id
    tx = user_send(_user(source), contract, method, case_ref=ref, **args)
    if not tx.status:
        saved = case if case is not None and case.pk else None
        audit(source, "CHAIN_REVERT", case=saved, detail=f"{contract}.{method} reverted: {tx.error}", tx=tx)
        raise ChainRejected(tx)
    return tx


def reindex(case):
    """Refresh the assistant's search index for a case; never blocks the workflow."""
    try:
        from assistant.indexer import index_case

        index_case(case)
    except Exception:
        logger.exception("Could not index %s for the assistant", case.case_id)


# --- FIR ------------------------------------------------------------------
def file_case(source, case):
    user = _user(source)
    case.filed_by = user
    case.department = user.department
    case.assign_identifiers()
    case.record_hash = case.compute_hash()
    tx = _send(
        source, case, "CaseRegistry", "createCase",
        case_id=case.case_id, record_hash=case.record_hash, department=case.department.code,
    )
    case.tx_hash = tx.tx_hash
    case.save()
    audit(source, "CASE_FILED", case=case, target=case.fir_number, detail=f"{case.crime_type} at {case.incident_location}", tx=tx)
    reindex(case)
    return tx


def _anchor_case(source, case, accused=None):
    case.record_hash = case.compute_hash(accused)
    tx = _send(source, case, "CaseRegistry", "updateCase", case_id=case.case_id, record_hash=case.record_hash)
    case.tx_hash = tx.tx_hash
    return tx


def update_case(source, case, changed):
    tx = _anchor_case(source, case)
    case.save()
    audit(source, "CASE_UPDATED", case=case, target=case.fir_number, detail="Changed: " + ", ".join(changed), tx=tx)
    notify(case.stakeholders(), f"{case.fir_number} was updated", "FIR details were amended.", case.get_absolute_url(), exclude=_user(source))
    reindex(case)
    return tx


def add_accused(source, case, accused):
    tx = _anchor_case(source, case, case.accused_payload() + [accused.payload()])
    accused.case = case
    accused.save()
    case.save()
    audit(source, "ACCUSED_ADDED", case=case, target=accused.name, detail=f"Status: {accused.status}", tx=tx)
    reindex(case)
    return tx


def remove_accused(source, case, accused):
    remaining = [a.payload() for a in case.accused.exclude(pk=accused.pk)]
    tx = _anchor_case(source, case, remaining)
    name = accused.name
    accused.delete()
    case.save()
    audit(source, "ACCUSED_REMOVED", case=case, target=name, tx=tx)
    reindex(case)
    return tx


def change_status(source, case, status):
    previous = case.get_status_display()
    tx = _send(source, case, "CaseRegistry", "setStatus", case_id=case.case_id, status=status)
    case.status = status
    case.save()
    audit(source, "STATUS_CHANGED", case=case, target=case.fir_number, detail=f"{previous} -> {case.get_status_display()}", tx=tx)
    notify(
        case.stakeholders(), f"{case.fir_number}: status is now {case.get_status_display()}",
        f"Changed from {previous}.", case.get_absolute_url(), exclude=_user(source),
    )
    reindex(case)
    return tx


def assign_investigator(source, case, officer):
    tx = _send(source, case, "CaseRegistry", "assignInvestigator", case_id=case.case_id, officer=officer.wallet_address)
    case.investigating_officer = officer
    case.save()
    audit(source, "INVESTIGATOR_ASSIGNED", case=case, target=officer.display_name, tx=tx)
    notify([officer], f"You were assigned {case.fir_number}", f"{case.crime_type} · {case.incident_location}", case.get_absolute_url())
    reindex(case)
    return tx


# --- case documents ---------------------------------------------------------
def add_note(source, case, note):
    note.case, note.author = case, _user(source)
    note.record_hash = note.compute_hash()
    tx = _send(
        source, case, "CaseRegistry", "addRecord",
        case_id=case.case_id, kind="NOTE", record_id=note.uid, record_hash=note.record_hash,
    )
    note.tx_hash = tx.tx_hash
    note.save()
    audit(source, "NOTE_ADDED", case=case, target=note.title, tx=tx)
    notify(case.stakeholders(), f"{case.fir_number}: new investigation note", note.title, case.get_absolute_url(), exclude=_user(source))
    reindex(case)
    return tx


def remove_note(source, note):
    case = note.case
    tx = _send(source, case, "CaseRegistry", "removeRecord", case_id=case.case_id, kind="NOTE", record_id=note.uid)
    note.removed = True
    note.save()
    audit(source, "NOTE_REMOVED", case=case, target=note.title, detail="Withdrawn; the original hash remains on-chain", tx=tx)
    reindex(case)
    return tx


def file_chargesheet(source, case, chargesheet):
    chargesheet.case, chargesheet.filed_by = case, _user(source)
    chargesheet.record_hash = chargesheet.compute_hash()
    tx = _send(
        source, case, "CaseRegistry", "fileChargesheet",
        case_id=case.case_id, record_id=chargesheet.uid, record_hash=chargesheet.record_hash,
    )
    chargesheet.tx_hash = tx.tx_hash
    chargesheet.save()
    case.status = Case.Status.CHARGESHEET_FILED
    case.save()
    audit(source, "CHARGESHEET_FILED", case=case, target=chargesheet.court, detail=f"Sections: {chargesheet.sections}", tx=tx)
    notify(
        case.stakeholders(), f"{case.fir_number}: chargesheet filed", f"Filed before {chargesheet.court}.",
        case.get_absolute_url(), exclude=_user(source),
    )
    reindex(case)
    return tx


def record_verdict(source, case, verdict):
    verdict.case, verdict.judge = case, _user(source)
    verdict.record_hash = verdict.compute_hash()
    tx = _send(
        source, case, "CaseRegistry", "recordVerdict",
        case_id=case.case_id, record_id=verdict.uid, record_hash=verdict.record_hash, outcome=verdict.outcome,
    )
    verdict.tx_hash = tx.tx_hash
    verdict.save()
    case.status = Case.Status.CONVICTED if verdict.outcome == "CONVICTED" else Case.Status.CLOSED
    case.save()
    audit(source, "VERDICT_RECORDED", case=case, target=verdict.get_outcome_display(), detail=verdict.sentence, tx=tx)
    notify(
        case.stakeholders(), f"{case.fir_number}: verdict — {verdict.get_outcome_display()}",
        verdict.sentence, case.get_absolute_url(), exclude=_user(source),
    )
    reindex(case)
    return tx


def schedule_hearing(source, case, hearing):
    hearing.case, hearing.created_by = case, _user(source)
    hearing.record_hash = hearing.compute_hash()
    tx = _send(
        source, case, "CaseRegistry", "addRecord",
        case_id=case.case_id, kind="HEARING", record_id=hearing.uid, record_hash=hearing.record_hash,
    )
    hearing.tx_hash = tx.tx_hash
    hearing.save()
    audit(source, "HEARING_SCHEDULED", case=case, target=hearing.purpose, detail=f"{hearing.court}", tx=tx)
    notify(
        case.stakeholders(), f"{case.fir_number}: hearing scheduled",
        f"{hearing.purpose} at {hearing.court}.", case.get_absolute_url(), exclude=_user(source),
    )
    reindex(case)
    return tx


def cancel_hearing(source, hearing):
    case = hearing.case
    tx = _send(source, case, "CaseRegistry", "removeRecord", case_id=case.case_id, kind="HEARING", record_id=hearing.uid)
    hearing.cancelled = True
    hearing.save()
    audit(source, "HEARING_CANCELLED", case=case, target=hearing.purpose, tx=tx)
    notify(case.stakeholders(), f"{case.fir_number}: hearing cancelled", hearing.purpose, case.get_absolute_url(), exclude=_user(source))
    reindex(case)
    return tx


# --- sharing and tasks ------------------------------------------------------
def share_case(source, case, department, reason):
    tx = _send(source, case, "CaseRegistry", "shareCase", case_id=case.case_id, department=department.code)
    share, _ = CaseShare.objects.update_or_create(
        case=case, department=department,
        defaults={"shared_by": _user(source), "reason": reason, "revoked": False, "tx_hash": tx.tx_hash},
    )
    audit(source, "CASE_SHARED", case=case, target=department.name, detail=reason, tx=tx)
    notify(
        department.members.filter(is_active=True, is_approved=True), f"{case.fir_number} was shared with {department.name}",
        reason, case.get_absolute_url(), exclude=_user(source),
    )
    return share


def revoke_share(source, share):
    case = share.case
    tx = _send(source, case, "CaseRegistry", "revokeShare", case_id=case.case_id, department=share.department.code)
    share.revoked, share.tx_hash = True, tx.tx_hash
    share.save()
    audit(source, "SHARE_REVOKED", case=case, target=share.department.name, tx=tx)
    return tx


def add_task(source, case, task):
    task.case, task.assigned_by = case, _user(source)
    task.save()
    audit(source, "TASK_ASSIGNED", case=case, target=task.assigned_to.display_name, detail=task.title)
    notify([task.assigned_to], f"New task on {case.fir_number}", task.title, case.get_absolute_url(), exclude=_user(source))
    return task


# --- evidence ---------------------------------------------------------------
def register_evidence(source, case, evidence, filename, data, content_type=""):
    user = _user(source)
    evidence.case, evidence.uploaded_by, evidence.custodian = case, user, user
    evidence.original_name = filename
    evidence.mime_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    evidence.size = len(data)
    evidence.sha256, evidence.storage_path = vault.store(data)
    try:
        from assistant.extract import extract_text

        evidence.extracted_text = extract_text(filename, data)
    except Exception:
        logger.exception("Text extraction failed for %s", filename)
    evidence.meta_hash = evidence.compute_meta_hash()
    tx = _send(
        source, case, "EvidenceRegistry", "registerEvidence",
        evidence_id=evidence.uid, case_id=case.case_id, file_hash=evidence.sha256, meta_hash=evidence.meta_hash,
    )
    evidence.tx_hash = tx.tx_hash
    evidence.save()
    CustodyLog.objects.create(evidence=evidence, actor=user, action="REGISTERED", notes="Evidence collected and registered", tx_hash=tx.tx_hash)
    audit(source, "EVIDENCE_REGISTERED", case=case, target=evidence.uid, detail=f"{evidence.title} ({evidence.original_name})", tx=tx)
    notify(case.stakeholders(), f"{case.fir_number}: new evidence", evidence.title, evidence.get_absolute_url(), exclude=user)
    reindex(case)
    return tx


def log_evidence_access(source, evidence, action, notes=""):
    tx = _send(source, evidence.case, "EvidenceRegistry", "logAccess", evidence_id=evidence.uid, action=action)
    CustodyLog.objects.create(evidence=evidence, actor=_user(source), action=action, notes=notes, tx_hash=tx.tx_hash)
    audit(source, f"EVIDENCE_{action}", case=evidence.case, target=evidence.uid, detail=notes, tx=tx)
    return tx


def transfer_custody(source, evidence, to_user, notes):
    tx = _send(source, evidence.case, "EvidenceRegistry", "transferCustody", evidence_id=evidence.uid, to=to_user.wallet_address)
    previous = evidence.custodian
    evidence.custodian = to_user
    evidence.save()
    CustodyLog.objects.create(evidence=evidence, actor=_user(source), action="TRANSFERRED", to_user=to_user, notes=notes, tx_hash=tx.tx_hash)
    audit(
        source, "CUSTODY_TRANSFERRED", case=evidence.case, target=evidence.uid,
        detail=f"{previous.display_name} -> {to_user.display_name}. {notes}", tx=tx,
    )
    notify([to_user], f"Evidence {evidence.uid} is now in your custody", f"{evidence.title} · {evidence.case.fir_number}", evidence.get_absolute_url())
    return tx


def add_forensic_report(source, evidence, report):
    case = evidence.case
    report.evidence, report.expert = evidence, _user(source)
    report.record_hash = report.compute_hash()
    tx = _send(
        source, case, "EvidenceRegistry", "addForensicReport",
        evidence_id=evidence.uid, record_id=report.uid, record_hash=report.record_hash,
    )
    report.tx_hash = tx.tx_hash
    report.save()
    CustodyLog.objects.create(evidence=evidence, actor=_user(source), action="ANALYSED", notes=report.title, tx_hash=tx.tx_hash)
    audit(source, "FORENSIC_REPORT_ADDED", case=case, target=evidence.uid, detail=report.title, tx=tx)
    notify(case.stakeholders(), f"{case.fir_number}: forensic report ready", report.title, evidence.get_absolute_url(), exclude=_user(source))
    reindex(case)
    return tx
