from django.conf import settings
from django.db import models


class DocumentChunk(models.Model):
    """A passage of a case document, with its embedding, for retrieval."""

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="chunks")
    source_type = models.CharField(max_length=20)
    source_id = models.CharField(max_length=40)
    label = models.CharField(max_length=200, help_text="Document reference shown in citations")
    url = models.CharField(max_length=200)
    position = models.PositiveIntegerField(default=0)
    text = models.TextField()
    embedding = models.BinaryField(null=True)

    class Meta:
        ordering = ["case_id", "source_type", "source_id", "position"]


class AssistantQuery(models.Model):
    """Every question asked of the assistant is kept for accountability."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assistant_queries")
    scope_case = models.ForeignKey("cases.Case", null=True, blank=True, on_delete=models.SET_NULL)
    question = models.TextField()
    answer = models.TextField()
    sources = models.JSONField(default=list)
    mode = models.CharField(max_length=12)
    latency_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "assistant queries"
