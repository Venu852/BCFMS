from django.contrib import admin

from ledger.admin import ReadOnlyAdmin

from .models import Case, CustodyLog, Evidence


# Case records are anchored on-chain: edits must go through the application so
# they are hashed and recorded. The admin site is therefore read-only here.
@admin.register(Case)
class CaseAdmin(ReadOnlyAdmin):
    list_display = ("fir_number", "crime_type", "status", "department", "filed_at")
    list_filter = ("status", "crime_type", "department")
    search_fields = ("fir_number", "case_id", "complainant_name")


@admin.register(Evidence)
class EvidenceAdmin(ReadOnlyAdmin):
    list_display = ("uid", "title", "case", "custodian", "created_at")
    exclude = ("extracted_text",)


@admin.register(CustodyLog)
class CustodyLogAdmin(ReadOnlyAdmin):
    list_display = ("evidence", "action", "actor", "to_user", "created_at")
