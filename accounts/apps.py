from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "accounts"

    def ready(self):
        from django.contrib.auth.signals import user_logged_out, user_login_failed

        def failed(sender, credentials, request=None, **kwargs):
            """Log wrong-password attempts against the account that was targeted."""
            from core.audit import log

            from .models import User

            username = credentials.get("username") or ""
            user = User.objects.filter(username=username).first()
            if user is not None and request is not None:
                log(request, "LOGIN_FAILED", detail="Wrong password", actor=user)

        def signed_out(sender, request, user, **kwargs):
            from core.audit import log

            if user is not None and request is not None:
                log(request, "LOGOUT", detail="Signed out", actor=user)

        user_login_failed.connect(failed, weak=False, dispatch_uid="bcfms_login_failed")
        user_logged_out.connect(signed_out, weak=False, dispatch_uid="bcfms_logout")
