def log(source, action, case=None, target="", detail="", tx=None, actor=None):
    """Write an audit entry and run the flag rules on it.

    `source` is the request (preferred, captures the IP address) or a user.
    `actor` names the user explicitly when the request is not yet authenticated,
    for example on a failed sign-in."""
    from . import flags
    from .models import AuditLog

    ip = None
    if hasattr(source, "META"):
        user = source.user if source.user.is_authenticated else None
        forwarded = source.META.get("HTTP_X_FORWARDED_FOR", "")
        ip = forwarded.split(",")[0].strip() or source.META.get("REMOTE_ADDR")
    else:
        user = source
    user = actor or user
    entry = AuditLog.objects.create(
        actor=user,
        actor_name=user.display_name if user else "Public",
        actor_role=user.role if user else "",
        action=action,
        case=case,
        case_ref=case.case_id if case else "",
        target=target,
        detail=detail,
        ip_address=ip or None,
        tx_hash=tx.tx_hash if tx else "",
    )
    flags.safe_evaluate(entry)
    return entry
