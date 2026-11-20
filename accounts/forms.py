from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .models import Department, User


class LoginForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_approved:
            raise forms.ValidationError("This account has been suspended by the admin office.", code="unapproved")


class OfficerCreateForm(forms.ModelForm):
    """Details the admin office enters; the user ID and password are generated."""

    class Meta:
        model = User
        fields = ("first_name", "last_name", "role", "department", "badge_number", "phone", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("first_name", "last_name", "department"):
            self.fields[name].required = True


class UserAdminForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "role", "department", "badge_number", "phone", "is_approved", "is_active")
        labels = {"is_approved": "Account enabled", "is_active": "Can sign in"}
        help_texts = {"is_approved": "Untick to suspend the officer. Their on-chain role is revoked.", "is_active": ""}


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ("name", "code", "kind", "district")


class TOTPForm(forms.Form):
    code = forms.CharField(
        label="6-digit code",
        max_length=6,
        min_length=6,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "autofocus": True}),
    )
