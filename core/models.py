from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Who did what, when, from where; linked to the on-chain transaction when there is one."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="audit_entries")
    actor_name = models.CharField(max_length=150, blank=True)
    actor_role = models.CharField(max_length=14, blank=True)
    action = models.CharField(max_length=40, db_index=True)
    case = models.ForeignKey("cases.Case", null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_entries")
    case_ref = models.CharField(max_length=40, blank=True, db_index=True)
    target = models.CharField(max_length=120, blank=True)
    detail = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    tx_hash = models.CharField(max_length=66, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.action} by {self.actor_name}"


class SecurityFlag(models.Model):
    """Something an officer did that the admin office should look at."""

    HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"
    SEVERITIES = [(HIGH, "High"), (MEDIUM, "Medium"), (LOW, "Low")]
    OPEN, REVIEWED, DISMISSED = "OPEN", "REVIEWED", "DISMISSED"
    STATUSES = [(OPEN, "Open"), (REVIEWED, "Reviewed - action taken"), (DISMISSED, "Dismissed - no concern")]
    CATEGORIES = [
        ("MANIPULATION", "Attempt to manipulate records"),
        ("QUESTION", "Suspicious question to the assistant"),
        ("ACCESS", "Access outside permission"),
        ("MODIFICATION", "Sensitive modification"),
        ("INTEGRITY", "Record integrity failure"),
        ("SIGN_IN", "Sign-in anomaly"),
        ("VOLUME", "Unusual volume of activity"),
    ]

    officer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="flags")
    severity = models.CharField(max_length=6, choices=SEVERITIES)
    category = models.CharField(max_length=14, choices=CATEGORIES)
    title = models.CharField(max_length=160)
    detail = models.TextField(blank=True)
    case = models.ForeignKey("cases.Case", null=True, blank=True, on_delete=models.SET_NULL, related_name="flags")
    entry = models.ForeignKey(AuditLog, null=True, blank=True, on_delete=models.SET_NULL, related_name="flags")
    status = models.CharField(max_length=10, choices=STATUSES, default=OPEN, db_index=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=250, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.title


class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=140)
    message = models.TextField(blank=True)
    url = models.CharField(max_length=200, blank=True)
    # ALERT notifications (tamper alerts) stay pinned at the top of every page until acknowledged.
    level = models.CharField(max_length=5, choices=[("INFO", "Information"), ("ALERT", "Tamper alert")], default="INFO")
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.title
