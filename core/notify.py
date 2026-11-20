from django.urls import NoReverseMatch, reverse


def notify(users, title, message="", url="", exclude=None, level="INFO"):
    """Create an in-app alert for each user. `url` may be a URL name or a path.

    level="ALERT" marks a tamper alert, which is pinned on every page until acknowledged."""
    from .models import Notification

    if url and not url.startswith("/"):
        try:
            url = reverse(url)
        except NoReverseMatch:
            url = ""
    seen, rows = set(), []
    for user in users:
        if user is None or user.pk in seen or (exclude is not None and user.pk == exclude.pk):
            continue
        seen.add(user.pk)
        rows.append(Notification(user=user, title=title[:140], message=message, url=url, level=level))
    Notification.objects.bulk_create(rows)
