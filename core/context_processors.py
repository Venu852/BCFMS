from django.conf import settings

from accounts.models import Role


def shell(request):
    """Navigation and badges for signed-in pages."""
    user = request.user
    if not user.is_authenticated:
        return {}
    is_admin = user.role == Role.ADMIN
    nav = [("dashboard", "Admin Office" if is_admin else "Dashboard"), ("case_list", "Cases")]
    if user.role in (Role.POLICE, Role.INVESTIGATOR) and user.department_id:
        nav.append(("case_create", "File FIR"))
    if user.role in settings.BCFMS_ASSISTANT_ROLES:
        nav.append(("assistant", "Case Assistant"))
    nav.append(("reports", "Reports"))
    open_flags = 0
    if is_admin:
        from .models import SecurityFlag

        open_flags = SecurityFlag.objects.filter(status=SecurityFlag.OPEN).count()
        nav += [("users", "Officers"), ("flags", "Flags"), ("audit_log", "Activity Log"), ("explorer", "Blockchain")]
    return {
        "nav": nav,
        "unread_count": user.notifications.filter(is_read=False).count(),
        "open_flags": open_flags,
        "tamper_alerts": user.notifications.filter(is_read=False, level="ALERT")[:3],
        # Ledger internals (hashes, transactions, contract names) are shown to the admin office only.
        "show_chain": is_admin,
    }
