from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Department, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "first_name", "last_name", "role", "department", "is_approved", "mfa_enabled")
    list_filter = ("role", "is_approved", "department")
    readonly_fields = ("wallet_address", "public_key")
    fieldsets = BaseUserAdmin.fieldsets + (
        ("BCFMS", {"fields": ("role", "department", "badge_number", "phone", "is_approved", "wallet_address", "public_key")}),
    )


admin.site.register(Department)
