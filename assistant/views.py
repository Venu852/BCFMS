import json

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from cases.models import Case
from core import flags
from core.audit import log as audit

from . import embedder, llm, rag
from .models import DocumentChunk

ROLES = settings.BCFMS_ASSISTANT_ROLES


@role_required(*ROLES)
def assistant(request):
    visible = Case.objects.visible_to(request.user)
    scope = visible.filter(case_id=request.GET.get("case", "")).first()
    return render(
        request,
        "assistant/chat.html",
        {
            "cases": visible.order_by("-filed_at")[:200],
            "scope": scope,
            "history": request.user.assistant_queries.select_related("scope_case")[:8],
            "llm": llm.status(),
            "indexed_cases": DocumentChunk.objects.filter(case__in=visible.values("pk")).values("case").distinct().count(),
            "passages": DocumentChunk.objects.filter(case__in=visible.values("pk")).count(),
        },
    )


@role_required(*ROLES)
@require_POST
def ask(request):
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid request"}, status=400)
    question = (payload.get("question") or "").strip()[:1500]
    if len(question) < 3:
        return JsonResponse({"error": "Please type a question."}, status=400)
    case = Case.objects.visible_to(request.user).filter(case_id=payload.get("case") or "").first()
    if flags.check_question(request.user, question):
        # Requests to alter records or evade monitoring are flagged for the admin office and not answered.
        audit(request, "ASSISTANT_QUERY", case=case, detail=f"Q: {question[:300]} | not answered (flagged)")
        return JsonResponse({"answer": flags.REFUSAL, "sources": [], "mode": "none", "latency_ms": 0, "model_error": ""})
    result = rag.answer(request.user, question, case)
    cited = sorted({s["fir_number"] for s in result["sources"]})
    audit(
        request, "ASSISTANT_QUERY", case=case,
        detail=f"Q: {question[:300]} | sources: {', '.join(cited) or 'none'} | {result['mode']}",
    )
    return JsonResponse(result)


@role_required(*ROLES)
def status(request):
    return JsonResponse({**llm.status(), "embeddings": embedder.available()})
