from accounts.models import Role

from .audit import log as audit

# Pages whose own view already writes a more specific audit entry, and background polls.
SELF_LOGGED = {"case_detail", "evidence_detail", "case_verify", "case_pdf", "evidence_download"}
BACKGROUND = {"chain_feed", "assistant_status", "notification_open"}
SKIP_PREFIXES = ("/static/", "/admin/", "/api/", "/favicon")


class ActivityMiddleware:
    """Records what each officer opens, and every refused request, for the admin office."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or user.role == Role.ADMIN:
            return response
        path = request.path
        if path.startswith(SKIP_PREFIXES):
            return response
        match = request.resolver_match
        name = match.url_name if match else ""

        if response.status_code == 403:
            audit(request, "ACCESS_DENIED", target=path, detail=f"Refused: {request.method} {path}")
        elif response.status_code == 404 and match and ("case_id" in match.kwargs or "uid" in match.kwargs):
            self._hidden_record(request, match)
        elif request.method == "GET" and response.status_code == 200 and name not in SELF_LOGGED | BACKGROUND:
            if response.get("Content-Type", "").startswith("text/html"):
                query = f"?{request.META['QUERY_STRING']}" if request.META.get("QUERY_STRING") else ""
                audit(request, "PAGE_VISITED", target=name or path, detail=(path + query)[:300])
        return response

    @staticmethod
    def _hidden_record(request, match):
        """A 404 on a case or evidence URL is only notable if the record really exists,
        meaning the officer asked for something they are not allowed to see."""
        from cases.models import Case, Evidence

        case = None
        if "case_id" in match.kwargs:
            case = Case.objects.filter(case_id=match.kwargs["case_id"]).first()
        elif "uid" in match.kwargs:
            evidence = Evidence.objects.filter(uid=match.kwargs["uid"]).select_related("case").first()
            case = evidence.case if evidence else None
        if case is not None:
            audit(
                request, "ACCESS_DENIED", case=case, target=case.fir_number,
                detail=f"Requested {request.path}, a case outside their access",
            )
