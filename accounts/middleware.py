from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


class AccountPolicyMiddleware:
    """Holds a signed-in officer at the required step until it is done:

    1. replace the password issued by the admin office;
    2. enrol an authenticator app, for sensitive roles when BCFMS_ENFORCE_MFA is on.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and not request.path.startswith("/static/"):
            logout = reverse("logout")
            if user.must_change_password:
                if request.path not in (reverse("password_change"), logout):
                    return redirect("password_change")
            elif settings.BCFMS_ENFORCE_MFA and user.role in settings.BCFMS_MFA_ROLES and not user.mfa_enabled:
                if request.path not in (reverse("mfa_setup"), logout):
                    return redirect("mfa_setup")
        return self.get_response(request)
