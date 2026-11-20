import base64
import io

import pyotp
import qrcode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.views import LoginView
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from rest_framework.authtoken.models import Token

from core.audit import log as audit
from core.models import SecurityFlag
from core.notify import notify
from ledger.crypto import decrypt_secret, encrypt_secret
from ledger.services import onchain_role, sync_role

from .credentials import generate_password, generate_username
from .decorators import role_required
from .forms import DepartmentForm, LoginForm, OfficerCreateForm, TOTPForm, UserAdminForm
from .models import Department, Role

User = get_user_model()
BACKEND = "django.contrib.auth.backends.ModelBackend"


class SignInView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True
    DEMO_USERS = ("admin", "officer.ravi", "officer.meena", "io.arjun", "io.fatima", "forensic.nisha", "judge.rao")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if settings.DEBUG:  # never advertise accounts outside development
            context["demo_users"] = list(
                User.objects.filter(username__in=self.DEMO_USERS).order_by("role").values_list("username", flat=True)
            )
        return context

    def form_valid(self, form):
        user = form.get_user()
        if user.mfa_enabled:
            # Password is correct; hold the session until the second factor is verified.
            self.request.session["mfa_user"] = user.pk
            self.request.session["mfa_next"] = self.get_success_url()
            return redirect("mfa_verify")
        response = super().form_valid(form)
        sync_role(user)
        audit(self.request, "LOGIN", detail="Signed in")
        return response


def mfa_verify(request):
    user = User.objects.filter(pk=request.session.get("mfa_user")).first()
    if user is None:
        return redirect("login")
    form = TOTPForm(request.POST or None)
    if form.is_valid():
        totp = pyotp.TOTP(decrypt_secret(user.encrypted_totp_secret))
        if totp.verify(form.cleaned_data["code"], valid_window=1):
            next_url = request.session.pop("mfa_next", None)
            request.session.pop("mfa_user", None)
            login(request, user, backend=BACKEND)
            sync_role(user)
            audit(request, "LOGIN", detail="Signed in with second factor")
            return redirect(next_url or "dashboard")
        audit(request, "LOGIN_FAILED", detail="Wrong authenticator code", actor=user)
        form.add_error("code", "That code is not valid. Check your authenticator app and try again.")
    return render(request, "accounts/mfa_verify.html", {"form": form})


@login_required
def mfa_setup(request):
    user = request.user
    if user.mfa_enabled:
        return redirect("profile")
    secret = request.session.setdefault("mfa_pending_secret", pyotp.random_base32())
    form = TOTPForm(request.POST or None)
    if form.is_valid():
        if pyotp.TOTP(secret).verify(form.cleaned_data["code"], valid_window=1):
            user.encrypted_totp_secret = encrypt_secret(secret)
            user.mfa_enabled = True
            user.save(update_fields=["encrypted_totp_secret", "mfa_enabled"])
            del request.session["mfa_pending_secret"]
            audit(request, "MFA_ENABLED", detail="Authenticator app enrolled")
            messages.success(request, "Two-factor authentication is now on for your account.")
            return redirect("profile")
        form.add_error("code", "That code is not valid. Make sure your device clock is correct.")
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name="BCFMS")
    buffer = io.BytesIO()
    qrcode.make(uri, box_size=6, border=2).save(buffer, format="PNG")
    return render(
        request,
        "accounts/mfa_setup.html",
        {"form": form, "secret": secret, "qr": base64.b64encode(buffer.getvalue()).decode()},
    )


@login_required
@require_POST
def mfa_disable(request):
    user = request.user
    user.mfa_enabled, user.encrypted_totp_secret = False, ""
    user.save(update_fields=["mfa_enabled", "encrypted_totp_secret"])
    audit(request, "MFA_DISABLED", detail="Authenticator app removed")
    messages.info(request, "Two-factor authentication has been turned off.")
    return redirect("profile")


@login_required
def password_change(request):
    """Officers replace the password issued by the admin office with their own."""
    user = request.user
    form = PasswordChangeForm(user, request.POST or None)
    form.fields["old_password"].label = "Password issued to you" if user.must_change_password else "Current password"
    if form.is_valid():
        form.save()
        user.must_change_password = False
        user.save(update_fields=["must_change_password"])
        update_session_auth_hash(request, user)
        audit(request, "PASSWORD_CHANGED", detail="Officer set their own password")
        messages.success(request, "Your password has been changed.")
        return redirect("dashboard")
    return render(request, "accounts/password_change.html", {"form": form, "first_time": user.must_change_password})


@login_required
def profile(request):
    user = request.user
    if request.method == "POST" and request.POST.get("action") == "token" and user.role == Role.ADMIN:
        Token.objects.filter(user=user).delete()
        Token.objects.create(user=user)
        audit(request, "API_TOKEN_ISSUED", detail="Generated a new API token")
        messages.success(request, "A new API token was generated. The previous one no longer works.")
        return redirect("profile")
    return render(
        request,
        "accounts/profile.html",
        {"token": Token.objects.filter(user=user).first(), "onchain_role": onchain_role(user.wallet_address)},
    )


# --- admin office: officer accounts -----------------------------------------
def _issue(request, officer, password, reason):
    """Hand the generated credentials to the admin exactly once, via the session."""
    request.session["issued"] = {"pk": officer.pk, "username": officer.username, "password": password, "reason": reason}
    return redirect("officer_credentials")


@role_required(Role.ADMIN)
def user_list(request):
    query = request.GET.get("q", "").strip()
    users = (
        User.objects.select_related("department")
        .annotate(
            open_flags=Count("flags", filter=Q(flags__status=SecurityFlag.OPEN), distinct=True),
            last_seen=Max("audit_entries__created_at"),
        )
        .order_by("role", "username")
    )
    if query:
        users = users.filter(
            Q(username__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(badge_number__icontains=query)
        )
    if request.GET.get("role"):
        users = users.filter(role=request.GET["role"])
    return render(request, "accounts/users.html", {"users": users, "roles": Role.choices, "query": query})


@role_required(Role.ADMIN)
def officer_create(request):
    form = OfficerCreateForm(request.POST or None)
    if form.is_valid():
        officer = form.save(commit=False)
        password = generate_password()
        officer.username = generate_username(officer.first_name, officer.role)
        officer.set_password(password)
        officer.is_approved = True
        officer.must_change_password = True
        officer.created_by = request.user
        officer.save()
        tx = sync_role(officer, by=request.user)
        audit(
            request, "OFFICER_CREATED", target=officer.username,
            detail=f"{officer.display_name}, {officer.get_role_display()}, {officer.department}", tx=tx,
        )
        return _issue(request, officer, password, "created")
    return render(request, "accounts/officer_create.html", {"form": form})


@role_required(Role.ADMIN)
def officer_credentials(request):
    issued = request.session.pop("issued", None)
    if issued is None:
        messages.info(request, "Credentials are shown only once. Issue a new password if they were not noted down.")
        return redirect("users")
    return render(
        request,
        "accounts/officer_credentials.html",
        {"issued": issued, "officer": get_object_or_404(User, pk=issued["pk"])},
    )


@role_required(Role.ADMIN)
@require_POST
def officer_reset_password(request, pk):
    officer = get_object_or_404(User, pk=pk)
    if officer.pk == request.user.pk:
        messages.error(request, "Change your own password from your profile.")
        return redirect("user_edit", pk=pk)
    password = generate_password()
    officer.set_password(password)
    officer.must_change_password = True
    officer.save()
    audit(request, "PASSWORD_RESET", target=officer.username, detail="New password issued by the admin office")
    notify([officer], "Your password was reset", "The admin office issued you a new password.", "profile")
    return _issue(request, officer, password, "reset")


@role_required(Role.ADMIN)
def user_edit(request, pk):
    target = get_object_or_404(User, pk=pk)
    form = UserAdminForm(request.POST or None, instance=target)
    if form.is_valid():
        if target.pk == request.user.pk and (
            form.cleaned_data["role"] != Role.ADMIN or not form.cleaned_data["is_active"] or not form.cleaned_data["is_approved"]
        ):
            form.add_error(None, "You cannot remove your own administrator access.")
        else:
            target = form.save()
            tx = sync_role(target, by=request.user)
            audit(
                request,
                "OFFICER_UPDATED",
                target=target.username,
                detail=f"Role {target.get_role_display()}, enabled={target.is_approved}, can sign in={target.is_active}",
                tx=tx,
            )
            messages.success(request, f"{target.display_name}'s account was updated.")
            return redirect("users")
    return render(
        request,
        "accounts/user_edit.html",
        {"form": form, "target": target, "onchain_role": onchain_role(target.wallet_address)},
    )


@role_required(Role.ADMIN)
def departments(request):
    form = DepartmentForm(request.POST or None)
    if form.is_valid():
        department = form.save()
        audit(request, "DEPARTMENT_CREATED", detail=f"{department.name} ({department.code})")
        messages.success(request, f"{department.name} was added.")
        return redirect("departments")
    return render(request, "accounts/departments.html", {"form": form, "departments": Department.objects.all()})
