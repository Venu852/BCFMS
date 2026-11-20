from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounts.models import Role
from core.audit import log as audit
from core.models import AuditLog
from core.pdf import case_file_pdf
from ledger import contracts
from ledger.models import Transaction
from ledger.services import address_book

from . import integrity, services, vault
from .access import access_for
from .forms import (
    AccusedForm,
    AssignForm,
    CaseForm,
    CaseSearchForm,
    ChargesheetForm,
    EvidenceForm,
    ForensicReportForm,
    HearingForm,
    NoteForm,
    ShareForm,
    TaskForm,
    TransferForm,
    VerdictForm,
)
from .models import Accused, Case, CaseShare, Evidence, Hearing, InvestigationNote, Task

FIELD_ROLES = (Role.POLICE, Role.INVESTIGATOR)
S = Case.Status


def get_case(request, case_id):
    return get_object_or_404(Case.objects.visible_to(request.user), case_id=case_id)


def get_evidence(request, uid):
    evidence = get_object_or_404(Evidence.objects.select_related("case", "custodian", "uploaded_by"), uid=uid)
    if not Case.objects.visible_to(request.user).filter(pk=evidence.case_id).exists():
        raise PermissionDenied
    return evidence


def permissions(user, case):
    """What this user may do on this case.

    Two things must both allow an action: the user's role (which the smart contracts
    also enforce) and the capacity in which they hold this particular case. A case
    merely shared with a department is read-only for that department."""
    role, status = user.role, case.status
    access = access_for(user, case)
    field = role in FIELD_ROLES and access.can_edit
    # Once an Investigating Officer is assigned, the investigation record is theirs alone.
    investigating = role == Role.INVESTIGATOR and (
        access.code == "LEAD" or (access.code == "OWNER" and case.investigating_officer_id is None)
    )
    admin = role == Role.ADMIN
    return {
        "edit": field and not case.is_final,
        "start": field and status == S.FILED,
        "close": field and status == S.UNDER_INVESTIGATION,
        "assign": ((role == Role.POLICE and access.can_edit) or admin) and not case.is_final,
        "note": investigating and status in (S.UNDER_INVESTIGATION, S.CHARGESHEET_FILED),
        "chargesheet": investigating and status == S.UNDER_INVESTIGATION,
        "hearing": role == Role.JUDICIARY and status == S.CHARGESHEET_FILED,
        "verdict": role == Role.JUDICIARY and status == S.CHARGESHEET_FILED,
        "evidence": field or role == Role.FORENSIC,
        "share": field or admin,
        "task": field or admin,
    }


def require(user, case, action):
    if not permissions(user, case)[action]:
        raise PermissionDenied("Your role cannot perform this action at the case's current stage.")


def run(request, operation, success):
    """Run a workflow operation, turning a contract revert into a user-facing error."""
    try:
        operation()
    except services.ChainRejected as exc:
        messages.error(request, f"The blockchain rejected this action: {exc.tx.error}")
        return False
    messages.success(request, success)
    return True


SEALED_WARNING = (
    "This record is sealed on the blockchain. Changing it raises a tamper alert: the change is recorded "
    "permanently under your user ID, and both you and the admin office are notified."
)


def form_page(request, case, form, title, contract_call, submit="Save", intro="", warning=""):
    return render(
        request,
        "cases/form.html",
        {
            "case": case, "form": form, "title": title, "contract_call": contract_call, "submit": submit,
            "intro": intro, "warning": warning, "access": access_for(request.user, case) if case else None,
        },
    )


# --- list / search ----------------------------------------------------------
@login_required
def case_list(request):
    form = CaseSearchForm(request.GET or None)
    cases = Case.objects.visible_to(request.user).select_related("department", "investigating_officer")
    if form.is_valid():
        data = form.cleaned_data
        if data["q"]:
            q = data["q"]
            cases = cases.filter(
                Q(fir_number__icontains=q)
                | Q(case_id__icontains=q)
                | Q(complainant_name__icontains=q)
                | Q(accused__name__icontains=q)
                | Q(description__icontains=q)
                | Q(sections_applied__icontains=q)
                | Q(crime_type__icontains=q)
                | Q(incident_location__icontains=q)
            )
        if data["status"]:
            cases = cases.filter(status=data["status"])
        if data["crime_type"]:
            cases = cases.filter(crime_type=data["crime_type"])
        if data["location"]:
            cases = cases.filter(Q(incident_location__icontains=data["location"]) | Q(district__icontains=data["location"]))
        if data["date_from"]:
            cases = cases.filter(filed_at__date__gte=data["date_from"])
        if data["date_to"]:
            cases = cases.filter(filed_at__date__lte=data["date_to"])
    cases = cases.distinct()
    params = request.GET.copy()
    params.pop("page", None)
    page = Paginator(cases, 15).get_page(request.GET.get("page"))
    for case in page:
        case.access = access_for(request.user, case)
    return render(
        request,
        "cases/list.html",
        {
            "form": form,
            "page": page,
            "total": cases.count(),
            "querystring": params.urlencode(),
            "can_file": request.user.role in FIELD_ROLES and request.user.department_id,
        },
    )


# --- FIR --------------------------------------------------------------------
@role_required(*FIELD_ROLES)
def case_create(request):
    if not request.user.department_id:
        messages.error(request, "Your account is not attached to a police station. Ask an administrator to set your department.")
        return redirect("case_list")
    form = CaseForm(request.POST or None, initial={"district": request.user.department.district})
    if form.is_valid():
        case = form.save(commit=False)
        if run(request, lambda: services.file_case(request, case), "FIR filed and anchored on the blockchain."):
            return redirect(case)
    return form_page(
        request, None, form, "File a new FIR", "CaseRegistry.createCase", "File FIR",
        "The FIR number and a hashed case ID are generated automatically. A SHA-256 hash of the record is written to the blockchain.",
    )


@login_required
def case_edit(request, case_id):
    case = get_case(request, case_id)
    require(request.user, case, "edit")
    form = CaseForm(request.POST or None, instance=case)
    if form.is_valid():
        if not form.changed_data:
            messages.info(request, "Nothing was changed.")
            return redirect(case)
        case = form.save(commit=False)
        if run(request, lambda: services.update_case(request, case, form.changed_data), "FIR updated; the new record hash is on-chain."):
            return redirect(case)
    return form_page(
        request, case, form, f"Edit {case.fir_number}", "CaseRegistry.updateCase", "Save changes", warning=SEALED_WARNING
    )


@login_required
def case_detail(request, case_id):
    case = get_case(request, case_id)
    audit(request, "CASE_VIEWED", case=case, target=case.fir_number)
    entries = AuditLog.objects.filter(case=case).exclude(action__in=["CASE_VIEWED", "EVIDENCE_VIEWED", "PAGE_VISITED"])
    if request.user.role != Role.ADMIN:
        # Monitoring detail (refused attempts, assistant questions) is for the admin office only.
        entries = entries.exclude(action__in=["CHAIN_REVERT", "ACCESS_DENIED", "ASSISTANT_QUERY", "FLAG_REVIEWED"])
    entries = entries[:200]
    return render(
        request,
        "cases/detail.html",
        {
            "case": case,
            "access": access_for(request.user, case),
            "can": permissions(request.user, case),
            "accused": case.accused.all(),
            "notes": case.notes.select_related("author"),
            "evidence": case.evidence.select_related("custodian", "uploaded_by"),
            "hearings": case.hearings.all(),
            "shares": case.shares.select_related("department", "shared_by"),
            "tasks": case.tasks.select_related("assigned_to"),
            "chargesheet": getattr(case, "chargesheet", None),
            "verdict": getattr(case, "verdict", None),
            "audit_entries": entries,
            "views_count": AuditLog.objects.filter(case=case, action="CASE_VIEWED").count(),
            "transactions": Transaction.objects.filter(case_ref=case.case_id).select_related("block").order_by("-block__number"),
            "book": address_book(),
            "steps": [S.FILED, S.UNDER_INVESTIGATION, S.CHARGESHEET_FILED, S.CONVICTED if case.status == S.CONVICTED else S.CLOSED],
        },
    )


@login_required
@require_POST
def case_status(request, case_id):
    case = get_case(request, case_id)
    status = request.POST.get("status")
    require(request.user, case, "start" if status == S.UNDER_INVESTIGATION else "close")
    if status not in (S.UNDER_INVESTIGATION, S.CLOSED):
        raise PermissionDenied
    run(request, lambda: services.change_status(request, case, status), f"Status changed to {S(status).label}.")
    return redirect(case)


@login_required
def case_assign(request, case_id):
    case = get_case(request, case_id)
    require(request.user, case, "assign")
    form = AssignForm(request.POST or None, initial={"officer": case.investigating_officer})
    if form.is_valid():
        officer = form.cleaned_data["officer"]
        if run(request, lambda: services.assign_investigator(request, case, officer), f"{officer.display_name} is now the Investigating Officer."):
            return redirect(case)
    return form_page(request, case, form, "Assign Investigating Officer", "CaseRegistry.assignInvestigator", "Assign")


@login_required
def accused_add(request, case_id):
    case = get_case(request, case_id)
    require(request.user, case, "edit")
    form = AccusedForm(request.POST or None)
    if form.is_valid():
        accused = form.save(commit=False)
        if run(request, lambda: services.add_accused(request, case, accused), f"{accused.name} was added to the FIR."):
            return redirect(case)
    return form_page(request, case, form, "Add accused", "CaseRegistry.updateCase", "Add accused")


@login_required
@require_POST
def accused_remove(request, case_id, pk):
    case = get_case(request, case_id)
    require(request.user, case, "edit")
    accused = get_object_or_404(Accused, pk=pk, case=case)
    run(request, lambda: services.remove_accused(request, case, accused), "Accused entry removed; the change is recorded on-chain.")
    return redirect(case)


# --- investigation ----------------------------------------------------------
@login_required
def note_add(request, case_id):
    case = get_case(request, case_id)
    require(request.user, case, "note")
    form = NoteForm(request.POST or None)
    if form.is_valid():
        note = form.save(commit=False)
        if run(request, lambda: services.add_note(request, case, note), "Investigation note added."):
            return redirect(case)
    return form_page(request, case, form, "Add investigation note", "CaseRegistry.addRecord(NOTE)", "Add note")


@login_required
@require_POST
def note_remove(request, case_id, uid):
    case = get_case(request, case_id)
    require(request.user, case, "note")
    note = get_object_or_404(InvestigationNote, uid=uid, case=case, removed=False)
    run(request, lambda: services.remove_note(request, note), "Note withdrawn. Its hash and the withdrawal remain on the audit trail.")
    return redirect(case)


@login_required
def chargesheet_file(request, case_id):
    case = get_case(request, case_id)
    require(request.user, case, "chargesheet")
    form = ChargesheetForm(request.POST or None, initial={"sections": case.sections_applied})
    if form.is_valid():
        chargesheet = form.save(commit=False)
        if run(request, lambda: services.file_chargesheet(request, case, chargesheet), "Chargesheet filed. The case is now before the court."):
            return redirect(case)
    return form_page(
        request, case, form, "File chargesheet", "CaseRegistry.fileChargesheet", "File chargesheet",
        "Filing moves the case to Chargesheet Filed and makes it visible to the judiciary.",
    )


@login_required
def hearing_add(request, case_id):
    case = get_case(request, case_id)
    require(request.user, case, "hearing")
    court = case.chargesheet.court if hasattr(case, "chargesheet") else ""
    form = HearingForm(request.POST or None, initial={"court": court})
    if form.is_valid():
        hearing = form.save(commit=False)
        if run(request, lambda: services.schedule_hearing(request, case, hearing), "Hearing scheduled; officers on the case were notified."):
            return redirect(case)
    return form_page(request, case, form, "Schedule hearing", "CaseRegistry.addRecord(HEARING)", "Schedule")


@login_required
@require_POST
def hearing_cancel(request, case_id, uid):
    case = get_case(request, case_id)
    require(request.user, case, "hearing")
    hearing = get_object_or_404(Hearing, uid=uid, case=case, cancelled=False)
    run(request, lambda: services.cancel_hearing(request, hearing), "Hearing cancelled.")
    return redirect(case)


@login_required
def verdict_add(request, case_id):
    case = get_case(request, case_id)
    require(request.user, case, "verdict")
    form = VerdictForm(request.POST or None)
    if form.is_valid():
        verdict = form.save(commit=False)
        if run(request, lambda: services.record_verdict(request, case, verdict), "Verdict recorded on the blockchain."):
            return redirect(case)
    return form_page(
        request, case, form, "Record verdict", "CaseRegistry.recordVerdict", "Record verdict",
        "A verdict is final: it closes the case and cannot be edited afterwards.",
    )


# --- sharing and tasks ------------------------------------------------------
@login_required
def share_add(request, case_id):
    case = get_case(request, case_id)
    require(request.user, case, "share")
    form = ShareForm(request.POST or None, case=case)
    if form.is_valid():
        data = form.cleaned_data
        if run(
            request,
            lambda: services.share_case(request, case, data["department"], data["reason"]),
            f"Case shared with {data['department'].name}.",
        ):
            return redirect(case)
    return form_page(
        request, case, form, "Share case with another department", "CaseRegistry.shareCase", "Share",
        "Members of the chosen department get read access to this case file. The grant is recorded on-chain and can be revoked.",
    )


@login_required
@require_POST
def share_revoke(request, case_id, pk):
    case = get_case(request, case_id)
    require(request.user, case, "share")
    share = get_object_or_404(CaseShare, pk=pk, case=case, revoked=False)
    run(request, lambda: services.revoke_share(request, share), f"Access for {share.department.name} was revoked.")
    return redirect(case)


@login_required
def task_add(request, case_id):
    case = get_case(request, case_id)
    require(request.user, case, "task")
    form = TaskForm(request.POST or None)
    if form.is_valid():
        services.add_task(request, case, form.save(commit=False))
        messages.success(request, "Task assigned.")
        return redirect(case)
    return form_page(request, case, form, "Assign a task", "", "Assign task")


@login_required
@require_POST
def task_done(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if task.assigned_to_id != request.user.pk and task.assigned_by_id != request.user.pk:
        raise PermissionDenied
    task.done = True
    task.save()
    audit(request, "TASK_COMPLETED", case=task.case, detail=task.title)
    return redirect(request.POST.get("next") or task.case.get_absolute_url())


# --- evidence ---------------------------------------------------------------
@login_required
def evidence_add(request, case_id):
    case = get_case(request, case_id)
    require(request.user, case, "evidence")
    form = EvidenceForm(request.POST or None, request.FILES or None)
    if form.is_valid():
        evidence = form.save(commit=False)
        upload = form.cleaned_data["file"]
        if run(
            request,
            lambda: services.register_evidence(request, case, evidence, upload.name, upload.read(), upload.content_type),
            "Evidence stored encrypted; its hash is registered on-chain.",
        ):
            return redirect(evidence)
    return form_page(request, case, form, "Upload evidence", "EvidenceRegistry.registerEvidence", "Upload and register")


@login_required
def evidence_detail(request, uid):
    evidence = get_evidence(request, uid)
    audit(request, "EVIDENCE_VIEWED", case=evidence.case, target=evidence.uid)
    user = request.user
    return render(
        request,
        "cases/evidence.html",
        {
            "evidence": evidence,
            "case": evidence.case,
            "access": access_for(user, evidence.case),
            "custody": evidence.custody_log.select_related("actor", "to_user"),
            "reports": evidence.reports.select_related("expert"),
            "can_transfer": evidence.custodian_id == user.pk or user.role == Role.ADMIN,
            "can_report": user.role == Role.FORENSIC,
            "is_image": evidence.mime_type.startswith("image/"),
        },
    )


@login_required
def evidence_download(request, uid):
    evidence = get_evidence(request, uid)
    try:
        services.log_evidence_access(request, evidence, "DOWNLOADED", "File decrypted and downloaded")
    except services.ChainRejected as exc:
        messages.error(request, f"The blockchain rejected this access: {exc.tx.error}")
        return redirect(evidence)
    try:
        data = vault.load(evidence)
    except Exception:
        messages.error(request, "The stored file failed its integrity check and cannot be opened. Report this to an administrator.")
        return redirect(evidence)
    response = HttpResponse(data, content_type=evidence.mime_type or "application/octet-stream")
    disposition = "inline" if request.GET.get("inline") and evidence.mime_type.startswith(("image/", "application/pdf")) else "attachment"
    response["Content-Disposition"] = f'{disposition}; filename="{evidence.original_name}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
def evidence_transfer(request, uid):
    evidence = get_evidence(request, uid)
    if evidence.custodian_id != request.user.pk and request.user.role != Role.ADMIN:
        raise PermissionDenied("Only the current custodian can transfer custody.")
    form = TransferForm(request.POST or None, evidence=evidence)
    if form.is_valid():
        data = form.cleaned_data
        if run(
            request,
            lambda: services.transfer_custody(request, evidence, data["to_user"], data["notes"]),
            f"Custody transferred to {data['to_user'].display_name}.",
        ):
            return redirect(evidence)
    return form_page(request, evidence.case, form, f"Transfer custody of {evidence.uid}", "EvidenceRegistry.transferCustody", "Transfer custody")


@role_required(Role.FORENSIC)
def forensic_add(request, uid):
    evidence = get_evidence(request, uid)
    form = ForensicReportForm(request.POST or None)
    if form.is_valid():
        report = form.save(commit=False)
        if run(request, lambda: services.add_forensic_report(request, evidence, report), "Forensic report filed and anchored on-chain."):
            return redirect(evidence)
    return form_page(request, evidence.case, form, f"Forensic report for {evidence.uid}", "EvidenceRegistry.addForensicReport", "File report")


# --- authenticity and export ------------------------------------------------
@login_required
def case_verify(request, case_id):
    case = get_case(request, case_id)
    report = integrity.verify_case(case)
    audit(request, "CASE_VERIFIED", case=case, target=case.fir_number, detail="Authentic" if report["ok"] else "INTEGRITY FAILURE")
    return render(request, "cases/verify.html", {"case": case, "report": report})


@login_required
def case_pdf(request, case_id):
    case = get_case(request, case_id)
    audit(request, "CASE_EXPORTED", case=case, target=case.fir_number, detail="PDF case file")
    response = HttpResponse(case_file_pdf(case, integrity.verify_case(case), request.user), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{case.fir_number.replace("/", "-")}.pdf"'
    return response
