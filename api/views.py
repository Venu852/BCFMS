"""Read-only REST API for integration with existing police and court systems.

Authenticate with `Authorization: Token <key>` (generate a key on the profile
page or via POST /api/v1/auth/token/). Access rules are the same as in the UI.
"""
from django.contrib.auth import authenticate
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response

from cases import integrity
from cases.access import access_for
from cases.models import Case
from core.audit import log as audit
from core.models import AuditLog
from ledger import chain
from ledger.models import Block, Transaction

PAGE = 50


class IsAdminOffice(BasePermission):
    """The ledger is visible only to the admin office."""

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == "ADMIN"


def _page(request, queryset):
    try:
        offset = max(int(request.GET.get("offset", 0)), 0)
        limit = min(max(int(request.GET.get("limit", PAGE)), 1), 200)
    except ValueError:
        offset, limit = 0, PAGE
    return queryset[offset : offset + limit], {"offset": offset, "limit": limit, "count": queryset.count()}


def _case_summary(case):
    return {
        "case_id": case.case_id,
        "fir_number": case.fir_number,
        "status": case.status,
        "crime_type": case.crime_type,
        "priority": case.priority,
        "filed_at": case.filed_at.isoformat(),
        "incident_date": case.incident_date.isoformat(),
        "incident_location": case.incident_location,
        "district": case.district,
        "department": case.department.code,
        "record_hash": case.record_hash,
        "tx_hash": case.tx_hash,
    }


def _visible(request):
    return Case.objects.visible_to(request.user).select_related("department")


@api_view(["POST"])
@permission_classes([AllowAny])
def obtain_token(request):
    user = authenticate(request, username=request.data.get("username"), password=request.data.get("password"))
    if user is None or not user.is_approved:
        return Response({"detail": "Invalid credentials or account not approved."}, status=400)
    if user.mfa_enabled:
        # A password alone must not bypass the second factor.
        return Response({"detail": "This account uses two-factor authentication. Generate a token from your profile page."}, status=403)
    token, _ = Token.objects.get_or_create(user=user)
    audit(user, "API_TOKEN_ISSUED", detail="Token requested through the API")
    return Response({"token": token.key})


@api_view(["GET"])
def case_list(request):
    cases = _visible(request)
    params = request.GET
    if params.get("q"):
        q = params["q"]
        cases = cases.filter(
            Q(fir_number__icontains=q) | Q(complainant_name__icontains=q) | Q(accused__name__icontains=q)
        ).distinct()
    if params.get("status"):
        cases = cases.filter(status=params["status"].upper())
    if params.get("location"):
        cases = cases.filter(Q(incident_location__icontains=params["location"]) | Q(district__icontains=params["location"]))
    if params.get("date_from"):
        cases = cases.filter(filed_at__date__gte=params["date_from"])
    if params.get("date_to"):
        cases = cases.filter(filed_at__date__lte=params["date_to"])
    rows, meta = _page(request, cases)
    return Response({**meta, "results": [_case_summary(c) for c in rows]})


@api_view(["GET"])
def case_detail(request, case_id):
    case = get_object_or_404(_visible(request), case_id=case_id)
    audit(request, "CASE_VIEWED", case=case, target=case.fir_number, detail="via API")
    data = _case_summary(case)
    access = access_for(request.user, case)
    complainant = {"name": case.complainant_name}
    if access.personal:  # contact details are withheld from shared and custodian access
        complainant.update(contact=case.complainant_contact, address=case.complainant_address)
    data.update(
        access={"level": access.code, "label": access.label},
        sections_applied=case.sections_applied,
        description=case.description,
        complainant=complainant,
        investigating_officer=case.investigating_officer.display_name if case.investigating_officer else None,
        accused=[a.payload() for a in case.accused.all()],
        notes=[
            {"uid": n.uid, "title": n.title, "text": n.text, "author": n.author.display_name, "created_at": n.created_at.isoformat(), "record_hash": n.record_hash}
            for n in case.notes.filter(removed=False).select_related("author")
        ],
        evidence=[
            {"uid": e.uid, "title": e.title, "kind": e.kind, "file_name": e.original_name, "size": e.size, "sha256": e.sha256,
             "custodian": e.custodian.display_name, "tx_hash": e.tx_hash}
            for e in case.evidence.select_related("custodian")
        ],
        hearings=[
            {"uid": h.uid, "scheduled_for": h.scheduled_for.isoformat(), "court": h.court, "purpose": h.purpose}
            for h in case.hearings.filter(cancelled=False)
        ],
        chargesheet={"uid": case.chargesheet.uid, "court": case.chargesheet.court, "sections": case.chargesheet.sections,
                     "filed_at": case.chargesheet.filed_at.isoformat()} if hasattr(case, "chargesheet") else None,
        verdict={"uid": case.verdict.uid, "outcome": case.verdict.outcome, "sentence": case.verdict.sentence,
                 "delivered_at": case.verdict.delivered_at.isoformat()} if hasattr(case, "verdict") else None,
    )
    return Response(data)


@api_view(["GET"])
def case_audit(request, case_id):
    """Full history of a case: application audit entries and on-chain transactions."""
    case = get_object_or_404(_visible(request), case_id=case_id)
    return Response(
        {
            "case_id": case.case_id,
            "audit": [
                {"at": e.created_at.isoformat(), "actor": e.actor_name, "role": e.actor_role, "action": e.action,
                 "target": e.target, "detail": e.detail, "tx_hash": e.tx_hash}
                for e in AuditLog.objects.filter(case=case)
            ],
            "transactions": [
                {"tx_hash": t.tx_hash, "block": t.block.number, "contract": t.contract, "method": t.method,
                 "sender": t.sender, "status": "success" if t.status else "reverted", "timestamp": t.timestamp, "args": t.args}
                for t in Transaction.objects.filter(case_ref=case.case_id).select_related("block")
            ],
        }
    )


@api_view(["GET"])
def case_verify(request, case_id):
    case = get_object_or_404(_visible(request), case_id=case_id)
    report = integrity.verify_case(case)
    audit(request, "CASE_VERIFIED", case=case, target=case.fir_number, detail="via API")
    return Response({"case_id": case.case_id, "authentic": report["ok"], "checks": report["checks"]})


@api_view(["GET"])
@permission_classes([IsAdminOffice])
def block_list(request):
    chain.ensure_genesis()
    rows, meta = _page(request, Block.objects.order_by("-number"))
    return Response({**meta, "results": [b.to_dict(with_transactions=False) for b in rows]})


@api_view(["GET"])
@permission_classes([IsAdminOffice])
def block_detail(request, number):
    return Response(get_object_or_404(Block, number=number).to_dict())


@api_view(["GET"])
@permission_classes([IsAdminOffice])
def transaction_detail(request, tx_hash):
    tx = get_object_or_404(Transaction.objects.select_related("block"), tx_hash=tx_hash)
    return Response({**tx.to_dict(), "block_number": tx.block.number, "block_hash": tx.block.hash, "confirm_ms": tx.confirm_ms})


@api_view(["GET"])
@permission_classes([IsAdminOffice])
def chain_verify(request):
    return Response(chain.verify_chain(deep=request.GET.get("deep") == "1"))


@api_view(["GET"])
@permission_classes([AllowAny])
def public_fir_status(request):
    """Unauthenticated, rate-limited status check. Exposes no personal data."""
    fir = request.GET.get("fir_number", "").strip()
    case = Case.objects.filter(fir_number__iexact=fir).select_related("department").first() if fir else None
    if case is None:
        return Response({"detail": "No FIR found with that number."}, status=404)
    return Response(
        {
            "fir_number": case.fir_number,
            "status": case.get_status_display(),
            "filed_on": case.filed_at.date().isoformat(),
            "police_station": case.department.name,
            "last_updated": case.updated_at.date().isoformat(),
            "blockchain_anchor": case.tx_hash,
        }
    )
