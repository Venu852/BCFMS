from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model

from accounts.models import Department, Role

from .models import Accused, Case, CaseShare, Chargesheet, Evidence, ForensicReport, Hearing, InvestigationNote, Task, Verdict

User = get_user_model()
DATETIME = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class UserChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, user):
        department = f" · {user.department.name}" if user.department else ""
        return f"{user.display_name} ({user.get_role_display()}){department}"


def active_users(*roles):
    users = User.objects.filter(is_active=True, is_approved=True).select_related("department")
    return users.filter(role__in=roles) if roles else users


class CaseForm(forms.ModelForm):
    class Meta:
        model = Case
        fields = (
            "crime_type",
            "priority",
            "incident_date",
            "incident_location",
            "district",
            "sections_applied",
            "description",
            "complainant_name",
            "complainant_contact",
            "complainant_address",
        )
        widgets = {"incident_date": DATE, "description": forms.Textarea(attrs={"rows": 6})}


class AccusedForm(forms.ModelForm):
    class Meta:
        model = Accused
        fields = ("name", "age", "gender", "address", "status")


class NoteForm(forms.ModelForm):
    class Meta:
        model = InvestigationNote
        fields = ("title", "text")
        widgets = {"text": forms.Textarea(attrs={"rows": 8})}


class ChargesheetForm(forms.ModelForm):
    class Meta:
        model = Chargesheet
        fields = ("court", "sections", "summary", "witnesses")
        widgets = {"summary": forms.Textarea(attrs={"rows": 8}), "witnesses": forms.Textarea(attrs={"rows": 4})}


class VerdictForm(forms.ModelForm):
    class Meta:
        model = Verdict
        fields = ("outcome", "judgement", "sentence")
        widgets = {"judgement": forms.Textarea(attrs={"rows": 8})}


class HearingForm(forms.ModelForm):
    scheduled_for = forms.DateTimeField(widget=DATETIME, input_formats=["%Y-%m-%dT%H:%M"])

    class Meta:
        model = Hearing
        fields = ("scheduled_for", "court", "purpose")


class AssignForm(forms.Form):
    officer = UserChoiceField(queryset=User.objects.none(), label="Investigating Officer")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["officer"].queryset = active_users(Role.INVESTIGATOR)


class ShareForm(forms.ModelForm):
    class Meta:
        model = CaseShare
        fields = ("department", "reason")

    def __init__(self, *args, case=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].queryset = Department.objects.exclude(pk=case.department_id)


class TaskForm(forms.ModelForm):
    assigned_to = UserChoiceField(queryset=User.objects.none())

    class Meta:
        model = Task
        fields = ("title", "assigned_to", "due_date")
        widgets = {"due_date": DATE}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = active_users()


class EvidenceForm(forms.ModelForm):
    file = forms.FileField(help_text="Image, document, video or audio. Stored encrypted; only its hash goes on-chain.")
    collected_at = forms.DateTimeField(widget=DATETIME, input_formats=["%Y-%m-%dT%H:%M"])

    class Meta:
        model = Evidence
        fields = ("title", "kind", "file", "description", "collected_at", "collected_location")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > settings.BCFMS_MAX_EVIDENCE_MB * 1024 * 1024:
            raise forms.ValidationError(f"Files larger than {settings.BCFMS_MAX_EVIDENCE_MB} MB are not accepted.")
        return upload


class TransferForm(forms.Form):
    to_user = UserChoiceField(queryset=User.objects.none(), label="Transfer custody to")
    notes = forms.CharField(max_length=250, label="Reason / handover notes")

    def __init__(self, *args, evidence=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["to_user"].queryset = active_users().exclude(pk=evidence.custodian_id)


class ForensicReportForm(forms.ModelForm):
    class Meta:
        model = ForensicReport
        fields = ("title", "methodology", "findings", "conclusion")
        widgets = {
            "methodology": forms.Textarea(attrs={"rows": 3}),
            "findings": forms.Textarea(attrs={"rows": 7}),
            "conclusion": forms.Textarea(attrs={"rows": 3}),
        }


class CaseSearchForm(forms.Form):
    q = forms.CharField(required=False, label="FIR number, name or keyword")
    status = forms.ChoiceField(required=False, choices=[("", "Any status")] + Case.Status.choices)
    crime_type = forms.ChoiceField(required=False, choices=[("", "Any crime type")] + Case.CRIME_TYPES)
    location = forms.CharField(required=False)
    date_from = forms.DateField(required=False, widget=DATE)
    date_to = forms.DateField(required=False, widget=DATE)
