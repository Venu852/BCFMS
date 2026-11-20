from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Max, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounts.models import Department, Role
from cases.models import Case, Evidence, Hearing, Task
from ledger import chain
from ledger.models import Block, Transaction

from . import stats
from .audit import log as audit
from .models import AuditLog, Notification, SecurityFlag
from .pdf import statistics_pdf

User = get_user_model()


def visible_cases(user):
    """Plain (join-free) queryset of the cases a user may see; safe to aggregate."""
    return Case.objects.filter(pk__in=Case.objects.visible_to(user).values("pk"))


def index(request):
    """Public landing page. Shows only aggregate ledger figures, never case data."""
    chain.ensure_genesis()
    blocks = list(Block.objects.order_by("-number")[:5])[::-1]
    figures = Transaction.objects.aggregate(total=Count("id"), avg_ms=Avg("confirm_ms"))
    return render(
        request,
        "core/index.html",
        {
            "height": blocks[-1].number,
            "transactions": figures["total"],
            "avg_ms": round(figures["avg_ms"] or 0),
            "blocks": [{"number": b.number, "hash": b.hash[2:8], "tail": b.hash[-4:]} for b in blocks],
        },
    )


# Routine entries that would drown out meaningful activity in summaries.
ROUTINE = ["PAGE_VISITED", "CASE_VIEWED", "EVIDENCE_VIEWED"]


def admin_office(request):
    """The admin's dashboard: who is doing what, what needs review, and the ledger."""
    chain.ensure_genesis()
    today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    officers = User.objects.exclude(role=Role.ADMIN)
    todays = AuditLog.objects.filter(created_at__gte=today).exclude(actor__role=Role.ADMIN).exclude(actor=None)
    open_flags = SecurityFlag.objects.filter(status=SecurityFlag.OPEN)
    head = Block.objects.order_by("-number").first()
    roster = (
        officers.select_related("department")
        .annotate(
            actions_today=Count("audit_entries", filter=Q(audit_entries__created_at__gte=today), distinct=True),
            open_flags=Count("flags", filter=Q(flags__status=SecurityFlag.OPEN), distinct=True),
            last_seen=Max("audit_entries__created_at"),
        )
        .order_by("-open_flags", "-actions_today", "username")
    )
    return render(
        request,
        "core/admin_office.html",
        {
            "officer_count": officers.filter(is_active=True, is_approved=True).count(),
            "active_today": todays.values("actor").distinct().count(),
            "actions_today": todays.exclude(action="PAGE_VISITED").count(),
            "open_count": open_flags.count(),
            "high_count": open_flags.filter(severity=SecurityFlag.HIGH).count(),
            "flags": open_flags.select_related("officer", "case")[:6],
            "roster": roster[:12],
            "activity": AuditLog.objects.exclude(action__in=ROUTINE).exclude(actor__role=Role.ADMIN).select_related("case", "actor")[:10],
            "stats": stats.build(Case.objects.all()),
            "chain": {"head": head, "transactions": Transaction.objects.count()},
        },
    )


@login_required
def dashboard(request):
    user = request.user
    if user.role == Role.ADMIN:
        return admin_office(request)
    cases = visible_cases(user)
    now = timezone.now()
    hearings = Hearing.objects.filter(
        case__in=cases, cancelled=False, scheduled_for__gte=now, scheduled_for__lte=now + timedelta(days=45)
    ).select_related("case")
    return render(
        request,
        "core/dashboard.html",
        {
            "stats": stats.build(cases),
            "recent_cases": cases.select_related("department", "investigating_officer")[:6],
            "my_cases": cases.filter(investigating_officer=user).exclude(status__in=["CLOSED", "CONVICTED"]).count(),
            "tasks": Task.objects.filter(assigned_to=user, done=False).select_related("case")[:6],
            "task_count": Task.objects.filter(assigned_to=user, done=False).count(),
            "hearings": hearings[:6],
            "custody": Evidence.objects.filter(custodian=user).select_related("case")[:6],
            "custody_count": Evidence.objects.filter(custodian=user).count(),
            "alerts": user.notifications.filter(is_read=False)[:5],
        },
    )


@role_required(Role.ADMIN)
def flags(request):
    """Review queue of automatically raised flags."""
    items = SecurityFlag.objects.select_related("officer", "case", "reviewed_by")
    status = request.GET.get("status", SecurityFlag.OPEN)
    severity = request.GET.get("severity", "")
    officer = request.GET.get("officer", "")
    if status != "ALL":
        items = items.filter(status=status)
    if severity:
        items = items.filter(severity=severity)
    if officer.isdigit():
        items = items.filter(officer_id=int(officer))
    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "core/flags.html",
        {
            "page": Paginator(items, 20).get_page(request.GET.get("page")),
            "total": items.count(),
            "status": status,
            "severity": severity,
            "officer": officer,
            "officers": User.objects.exclude(role=Role.ADMIN).order_by("first_name", "username"),
            "statuses": SecurityFlag.STATUSES,
            "severities": SecurityFlag.SEVERITIES,
            "querystring": params.urlencode(),
        },
    )


@role_required(Role.ADMIN)
@require_POST
def flag_review(request, pk):
    flag = get_object_or_404(SecurityFlag, pk=pk)
    outcome = request.POST.get("outcome")
    if outcome in (SecurityFlag.REVIEWED, SecurityFlag.DISMISSED):
        flag.status = outcome
        flag.reviewed_by, flag.reviewed_at = request.user, timezone.now()
        flag.review_note = request.POST.get("note", "")[:250]
        flag.save()
        audit(request, "FLAG_REVIEWED", case=flag.case, target=flag.title, detail=f"{flag.get_status_display()}. {flag.review_note}")
        messages.success(request, "Flag updated.")
    return redirect(request.POST.get("next") or "flags")


@role_required(Role.ADMIN)
def officer_monitor(request, pk):
    """Everything one officer has done on the platform."""
    officer = get_object_or_404(User.objects.select_related("department"), pk=pk)
    entries = AuditLog.objects.filter(actor=officer).select_related("case")
    kind = request.GET.get("show", "actions")
    if kind == "actions":
        entries = entries.exclude(action__in=ROUTINE)
    elif kind == "changes":
        entries = entries.exclude(tx_hash="")
    week = timezone.now() - timedelta(days=7)
    return render(
        request,
        "core/officer_monitor.html",
        {
            "officer": officer,
            "page": Paginator(entries, 30).get_page(request.GET.get("page")),
            "kind": kind,
            "querystring": f"show={kind}",
            "flags": officer.flags.select_related("case")[:20],
            "open_flags": officer.flags.filter(status=SecurityFlag.OPEN).count(),
            "questions": officer.assistant_queries.select_related("scope_case")[:15],
            "summary": {
                "last_login": AuditLog.objects.filter(actor=officer, action="LOGIN").first(),
                "week_actions": AuditLog.objects.filter(actor=officer, created_at__gte=week).exclude(action__in=ROUTINE).count(),
                "changes": AuditLog.objects.filter(actor=officer).exclude(tx_hash="").count(),
                "questions": officer.assistant_queries.count(),
                "denied": AuditLog.objects.filter(actor=officer, action__in=["ACCESS_DENIED", "CHAIN_REVERT"]).count(),
            },
        },
    )


@login_required
def notifications(request):
    if request.method == "POST":
        request.user.notifications.filter(is_read=False).update(is_read=True)
        return redirect("notifications")
    return render(request, "core/notifications.html", {"page": Paginator(request.user.notifications.all(), 25).get_page(request.GET.get("page"))})


@login_required
def notification_open(request, pk):
    note = get_object_or_404(Notification, pk=pk, user=request.user)
    note.is_read = True
    note.save(update_fields=["is_read"])
    return redirect(note.url or "notifications")


@role_required(Role.ADMIN)
def audit_log(request):
    entries = AuditLog.objects.select_related("case", "actor")
    query = request.GET.get("q", "").strip()
    action = request.GET.get("action", "")
    officer = request.GET.get("officer", "")
    if officer.isdigit():
        entries = entries.filter(actor_id=int(officer))
    if not action and request.GET.get("visits") != "1":
        entries = entries.exclude(action="PAGE_VISITED")  # page-by-page browsing is opt-in: it is very long
    if query:
        entries = entries.filter(
            Q(actor_name__icontains=query) | Q(case_ref__icontains=query) | Q(case__fir_number__icontains=query)
            | Q(detail__icontains=query) | Q(target__icontains=query) | Q(tx_hash__istartswith=query)
        )
    if action:
        entries = entries.filter(action=action)
    if request.GET.get("date_from"):
        entries = entries.filter(created_at__date__gte=request.GET["date_from"])
    if request.GET.get("date_to"):
        entries = entries.filter(created_at__date__lte=request.GET["date_to"])
    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "core/audit.html",
        {
            "page": Paginator(entries, 40).get_page(request.GET.get("page")),
            "actions": AuditLog.objects.order_by("action").values_list("action", flat=True).distinct(),
            "query": query,
            "action": action,
            "officer": officer,
            "officers": User.objects.order_by("first_name", "username"),
            "visits": request.GET.get("visits") == "1",
            "querystring": params.urlencode(),
            "total": entries.count(),
        },
    )


@login_required
def reports(request):
    cases = visible_cases(request.user)
    filters = []
    date_from, date_to, department = (request.GET.get(k, "") for k in ("date_from", "date_to", "department"))
    if date_from:
        cases = cases.filter(filed_at__date__gte=date_from)
        filters.append(f"from {date_from}")
    if date_to:
        cases = cases.filter(filed_at__date__lte=date_to)
        filters.append(f"to {date_to}")
    if department.isdigit():
        cases = cases.filter(department_id=int(department))
        filters.append(Department.objects.filter(pk=department).values_list("name", flat=True).first() or "")
    data = stats.build(cases)
    if request.GET.get("format") == "pdf":
        audit(request, "REPORT_EXPORTED", detail="Crime statistics PDF " + " ".join(filters))
        response = HttpResponse(statistics_pdf(data, "Filters: " + ", ".join(filters) if filters else "", request.user), content_type="application/pdf")
        response["Content-Disposition"] = 'attachment; filename="bcfms-crime-statistics.pdf"'
        return response
    params = request.GET.copy()
    params["format"] = "pdf"
    return render(
        request,
        "core/reports.html",
        {
            "stats": data,
            "departments": Department.objects.filter(kind=Department.POLICE),
            "date_from": date_from,
            "date_to": date_to,
            "department": department,
            "pdf_query": params.urlencode(),
        },
    )


def public_lookup(request):
    """Read-only FIR status check for members of the public. Reveals no personal data."""
    fir = request.GET.get("fir", "").strip().upper()
    case = None
    if fir:
        case = Case.objects.filter(fir_number__iexact=fir).select_related("department").first()
        audit(request, "PUBLIC_LOOKUP", case=case, target=fir, detail="Found" if case else "Not found")
    anchor = Transaction.objects.filter(tx_hash=case.tx_hash).select_related("block").first() if case else None
    return render(request, "core/public_lookup.html", {"fir": fir, "case": case, "anchor": anchor, "searched": bool(fir)})


def permission_denied(request, exception=None):
    return render(request, "403.html", {"reason": str(exception) if exception else ""}, status=403)
