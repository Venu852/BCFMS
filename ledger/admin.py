from django.contrib import admin

from .models import Authority, Block, Transaction


class ReadOnlyAdmin(admin.ModelAdmin):
    """The ledger is append-only; the admin site may look but not touch."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Block)
class BlockAdmin(ReadOnlyAdmin):
    list_display = ("number", "hash", "validator", "timestamp")


@admin.register(Transaction)
class TransactionAdmin(ReadOnlyAdmin):
    list_display = ("tx_hash", "contract", "method", "sender", "status")
    list_filter = ("contract", "method", "status")
    search_fields = ("tx_hash", "sender", "case_ref")


@admin.register(Authority)
class AuthorityAdmin(ReadOnlyAdmin):
    list_display = ("name", "kind", "address")
    exclude = ("encrypted_private_key",)
