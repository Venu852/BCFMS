import uuid
from datetime import timezone as dt_timezone

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from accounts.models import Department, Role
from ledger import contracts
from ledger.crypto import hash_obj, sha256_hex


def stamp(value):
    """Timezone-independent text form of a datetime, used inside hashed payloads."""
    return value.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if value else ""


def now():
    return timezone.now().replace(microsecond=0)


def _uid(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def note_uid():
    return _uid("NOTE")


def chargesheet_uid():
    return _uid("CS")


def verdict_uid():
    return _uid("VER")


def hearing_uid():
    return _uid("HRG")


def evidence_uid():
    return _uid("EV")


def report_uid():
    return _uid("FR")


class CaseQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Cases a user may open, according to role, department and sharing."""
        if not user.is_authenticated:
            return self.none()
        if user.role == Role.ADMIN:
            return self
        rule = Q(shares__department=user.department_id, shares__revoked=False) if user.department_id else Q(pk__in=[])
        if user.role in (Role.POLICE, Role.INVESTIGATOR):
            rule |= Q(filed_by=user) | Q(investigating_officer=user)
            if user.department_id:
                rule |= Q(department=user.department_id)
        elif user.role == Role.FORENSIC:
            rule |= Q(evidence__custodian=user)
        elif user.role == Role.JUDICIARY:
            rule |= Q(status__in=[contracts.CHARGESHEET_FILED, contracts.CONVICTED]) | Q(chargesheet__isnull=False)
        return self.filter(rule).distinct()


class Case(models.Model):
    class Status(models.TextChoices):
        FILED = contracts.FILED, "Filed"
        UNDER_INVESTIGATION = contracts.UNDER_INVESTIGATION, "Under Investigation"
        CHARGESHEET_FILED = contracts.CHARGESHEET_FILED, "Chargesheet Filed"
        CLOSED = contracts.CLOSED, "Closed"
        CONVICTED = contracts.CONVICTED, "Convicted"

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    CRIME_TYPES = [
        (c, c)
        for c in (
            "Theft",
            "Burglary",
            "Robbery",
            "Assault",
            "Homicide",
            "Fraud",
            "Cybercrime",
            "Narcotics",
            "Kidnapping",
            "Sexual Offence",
            "Domestic Violence",
            "Missing Person",
            "Other",
        )
    ]

    case_id = models.CharField(max_length=40, unique=True, editable=False)
    fir_number = models.CharField(max_length=40, unique=True, editable=False)
    filed_at = models.DateTimeField(default=now)
    incident_date = models.DateField()
    incident_location = models.CharField(max_length=200)
    district = models.CharField(max_length=80)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="cases")
    crime_type = models.CharField(max_length=30, choices=CRIME_TYPES)
    sections_applied = models.CharField(max_length=250, help_text="e.g. BNS 303(2), 331(4)")
    description = models.TextField(help_text="Facts of the complaint as narrated")
    complainant_name = models.CharField(max_length=120)
    complainant_contact = models.CharField(max_length=60, blank=True)
    complainant_address = models.CharField(max_length=250, blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.FILED, db_index=True)
    filed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cases_filed")
    investigating_officer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="cases_investigating"
    )
    record_hash = models.CharField(max_length=66, blank=True)
    tx_hash = models.CharField(max_length=66, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CaseQuerySet.as_manager()

    class Meta:
        ordering = ["-filed_at", "-id"]

    def __str__(self):
        return self.fir_number

    def get_absolute_url(self):
        return reverse("case_detail", args=[self.case_id])

    def assign_identifiers(self):
        """FIR number is human-readable and sequential per station and year; the
        case ID is a hash, so it is unguessable and safe to anchor on-chain."""
        year = self.filed_at.year
        sequence = Case.objects.filter(department=self.department, filed_at__year=year).count() + 1
        while True:
            fir = f"FIR/{self.department.code}/{year}/{sequence:04d}"
            if not Case.objects.filter(fir_number=fir).exists():
                break
            sequence += 1
        self.fir_number = fir
        self.case_id = "CASE-" + sha256_hex(f"{fir}|{uuid.uuid4().hex}")[2:18].upper()

    def accused_payload(self):
        if self.pk is None:
            return []
        return [a.payload() for a in self.accused.all()]

    def payload(self, accused=None):
        """The canonical FIR content whose SHA-256 hash is anchored on-chain."""
        accused = self.accused_payload() if accused is None else accused
        return {
            "case_id": self.case_id,
            "fir_number": self.fir_number,
            "filed_at": stamp(self.filed_at),
            "incident_date": self.incident_date.isoformat(),
            "incident_location": self.incident_location,
            "district": self.district,
            "department": self.department.code,
            "crime_type": self.crime_type,
            "sections_applied": self.sections_applied,
            "description": self.description,
            "complainant_name": self.complainant_name,
            "complainant_contact": self.complainant_contact,
            "complainant_address": self.complainant_address,
            "priority": self.priority,
            "accused": sorted(accused, key=lambda a: (a["name"], a["address"], a["age"] or 0)),
        }

    def compute_hash(self, accused=None):
        return hash_obj(self.payload(accused))

    @property
    def is_final(self):
        return self.status in contracts.FINAL_STATUSES

    def stakeholders(self):
        return [u for u in (self.filed_by, self.investigating_officer) if u]

    @property
    def progress(self):
        order = [self.Status.FILED, self.Status.UNDER_INVESTIGATION, self.Status.CHARGESHEET_FILED]
        return 3 if self.is_final else order.index(self.status)


class Accused(models.Model):
    STATUSES = [(s, s.title()) for s in ("UNKNOWN", "ABSCONDING", "ARRESTED", "ON BAIL", "IN CUSTODY")]
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="accused")
    name = models.CharField(max_length=120)
    age = models.PositiveIntegerField(null=True, blank=True)
    gender = models.CharField(max_length=10, blank=True, choices=[("Male", "Male"), ("Female", "Female"), ("Other", "Other")])
    address = models.CharField(max_length=250, blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default="UNKNOWN")

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "accused"

    def __str__(self):
        return self.name

    def payload(self):
        return {"name": self.name, "age": self.age, "gender": self.gender, "address": self.address, "status": self.status}


class ChainRecord(models.Model):
    """Base for case documents whose hash is anchored on-chain."""

    record_hash = models.CharField(max_length=66, blank=True)
    tx_hash = models.CharField(max_length=66, blank=True)

    class Meta:
        abstract = True

    def compute_hash(self):
        return hash_obj(self.payload())


class InvestigationNote(ChainRecord):
    uid = models.CharField(max_length=20, unique=True, default=note_uid, editable=False)
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    title = models.CharField(max_length=140)
    text = models.TextField()
    created_at = models.DateTimeField(default=now)
    removed = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at", "-id"]

    def payload(self):
        return {
            "uid": self.uid,
            "case_id": self.case.case_id,
            "author": self.author.wallet_address,
            "title": self.title,
            "text": self.text,
            "created_at": stamp(self.created_at),
        }


class Chargesheet(ChainRecord):
    uid = models.CharField(max_length=20, unique=True, default=chargesheet_uid, editable=False)
    case = models.OneToOneField(Case, on_delete=models.CASCADE, related_name="chargesheet")
    filed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    court = models.CharField(max_length=160)
    sections = models.CharField("Sections charged", max_length=250)
    summary = models.TextField("Summary of investigation and charges")
    witnesses = models.TextField(blank=True, help_text="One witness per line")
    filed_at = models.DateTimeField(default=now)

    def payload(self):
        return {
            "uid": self.uid,
            "case_id": self.case.case_id,
            "filed_by": self.filed_by.wallet_address,
            "court": self.court,
            "sections": self.sections,
            "summary": self.summary,
            "witnesses": self.witnesses,
            "filed_at": stamp(self.filed_at),
        }


class Verdict(ChainRecord):
    OUTCOMES = [(o, o.title()) for o in contracts.VERDICT_OUTCOMES]
    uid = models.CharField(max_length=20, unique=True, default=verdict_uid, editable=False)
    case = models.OneToOneField(Case, on_delete=models.CASCADE, related_name="verdict")
    judge = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    outcome = models.CharField(max_length=12, choices=OUTCOMES)
    judgement = models.TextField()
    sentence = models.CharField(max_length=250, blank=True)
    delivered_at = models.DateTimeField(default=now)

    def payload(self):
        return {
            "uid": self.uid,
            "case_id": self.case.case_id,
            "judge": self.judge.wallet_address,
            "outcome": self.outcome,
            "judgement": self.judgement,
            "sentence": self.sentence,
            "delivered_at": stamp(self.delivered_at),
        }


class Hearing(ChainRecord):
    uid = models.CharField(max_length=20, unique=True, default=hearing_uid, editable=False)
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="hearings")
    scheduled_for = models.DateTimeField()
    court = models.CharField(max_length=160)
    purpose = models.CharField(max_length=200)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    cancelled = models.BooleanField(default=False)

    class Meta:
        ordering = ["scheduled_for"]

    def payload(self):
        return {
            "uid": self.uid,
            "case_id": self.case.case_id,
            "scheduled_for": stamp(self.scheduled_for),
            "court": self.court,
            "purpose": self.purpose,
            "created_by": self.created_by.wallet_address,
        }


class CaseShare(models.Model):
    """Grants another department access to a case file (inter-departmental sharing)."""

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="shares")
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="shared_cases")
    shared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reason = models.CharField(max_length=250)
    created_at = models.DateTimeField(default=now)
    revoked = models.BooleanField(default=False)
    tx_hash = models.CharField(max_length=66, blank=True)

    class Meta:
        unique_together = [("case", "department")]
        ordering = ["-created_at"]


class Task(models.Model):
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=200)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tasks")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasks_assigned")
    due_date = models.DateField(null=True, blank=True)
    done = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["done", "due_date", "-id"]


class Evidence(models.Model):
    KINDS = [(k, k.title()) for k in ("IMAGE", "DOCUMENT", "VIDEO", "AUDIO", "DIGITAL", "PHYSICAL")]

    uid = models.CharField(max_length=20, unique=True, default=evidence_uid, editable=False)
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="evidence")
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=10, choices=KINDS, default="DOCUMENT")
    original_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=66)
    storage_path = models.CharField(max_length=200)
    extracted_text = models.TextField(blank=True)
    collected_at = models.DateTimeField(default=now)
    collected_location = models.CharField(max_length=200, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="evidence_uploaded")
    custodian = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="evidence_held")
    meta_hash = models.CharField(max_length=66, blank=True)
    tx_hash = models.CharField(max_length=66, blank=True)
    created_at = models.DateTimeField(default=now)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "evidence"

    def __str__(self):
        return f"{self.uid} {self.title}"

    def get_absolute_url(self):
        return reverse("evidence_detail", args=[self.uid])

    def payload(self):
        return {
            "uid": self.uid,
            "case_id": self.case.case_id,
            "title": self.title,
            "description": self.description,
            "kind": self.kind,
            "original_name": self.original_name,
            "size": self.size,
            "sha256": self.sha256,
            "collected_at": stamp(self.collected_at),
            "collected_location": self.collected_location,
            "uploaded_by": self.uploaded_by.wallet_address,
        }

    def compute_meta_hash(self):
        return hash_obj(self.payload())


class CustodyLog(models.Model):
    ACTIONS = [(a, a.title()) for a in ("REGISTERED", "VIEWED", "DOWNLOADED", "TRANSFERRED", "ANALYSED", "VERIFIED")]
    evidence = models.ForeignKey(Evidence, on_delete=models.CASCADE, related_name="custody_log")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    action = models.CharField(max_length=12, choices=ACTIONS)
    to_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    notes = models.CharField(max_length=250, blank=True)
    tx_hash = models.CharField(max_length=66, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class ForensicReport(ChainRecord):
    uid = models.CharField(max_length=20, unique=True, default=report_uid, editable=False)
    evidence = models.ForeignKey(Evidence, on_delete=models.CASCADE, related_name="reports")
    expert = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    title = models.CharField(max_length=160)
    methodology = models.TextField(blank=True)
    findings = models.TextField()
    conclusion = models.TextField(blank=True)
    created_at = models.DateTimeField(default=now)

    class Meta:
        ordering = ["-created_at", "-id"]

    def payload(self):
        return {
            "uid": self.uid,
            "evidence": self.evidence.uid,
            "expert": self.expert.wallet_address,
            "title": self.title,
            "methodology": self.methodology,
            "findings": self.findings,
            "conclusion": self.conclusion,
            "created_at": stamp(self.created_at),
        }
