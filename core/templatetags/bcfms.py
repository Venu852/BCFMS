import json
import math
from datetime import datetime, timezone as dt_timezone

from django import template
from django.utils import timezone
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def short(value, size=8):
    """0x1234abcd…ef90 style abbreviation for hashes and addresses."""
    value, size = str(value or ""), int(size or 8)
    return value if len(value) <= size * 2 + 5 else f"{value[: size + 2]}…{value[-size:]}"


@register.filter
def label(address, book):
    """Human name for an address, falling back to the shortened address."""
    return (book or {}).get(address) or short(address, 6)


@register.filter
def from_ms(value):
    return timezone.localtime(datetime.fromtimestamp(int(value) / 1000, tz=dt_timezone.utc))


@register.filter
def pretty_json(value):
    return json.dumps(value, indent=2, ensure_ascii=False)


@register.filter
def filesize(value):
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


@register.filter
def action_name(value):
    """CASE_FILED -> Case filed"""
    return str(value).replace("_", " ").capitalize()


@register.filter
def slug_status(value):
    return str(value).lower().replace("_", "-").replace(" ", "-")


ICONS = {
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "plus": '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>',
    "spark": '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    "cube": '<path d="M12 2.5l8.5 4.75v9.5L12 21.5l-8.5-4.75v-9.5z"/><path d="M3.5 7.25L12 12l8.5-4.75M12 12v9.5"/>',
    "shield": '<path d="M12 3l8 3v6c0 4.6-3.2 8.1-8 9-4.8-.9-8-4.4-8-9V6z"/><path d="M9 12l2.2 2.2L15.5 10"/>',
    "chart": '<path d="M4 20V4M4 20h16"/><rect x="7.5" y="11" width="3" height="6" rx=".8"/><rect x="12.5" y="7" width="3" height="10" rx=".8"/><rect x="17.5" y="13" width="3" height="4" rx=".8"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r="1.2"/><circle cx="3.5" cy="12" r="1.2"/><circle cx="3.5" cy="18" r="1.2"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.6-3.6 3.2-5.5 6.5-5.5s5.9 1.9 6.5 5.5"/><path d="M16 4.8a3.5 3.5 0 0 1 0 6.4M18 14.8c2 .7 3.2 2.5 3.6 5.2"/>',
    "building": '<path d="M4 21V5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v16M15 10h4a1 1 0 0 1 1 1v10M2.5 21h19"/><path d="M8 8h3M8 12h3M8 16h3"/>',
    "bell": '<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2.5h-15z"/><path d="M10 20.5a2.2 2.2 0 0 0 4 0"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "lock": '<rect x="5" y="11" width="14" height="9.5" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    "download": '<path d="M12 4v11M7.5 11l4.5 4.5 4.5-4.5M5 20h14"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.2 5.2l1.8 1.8M17 17l1.8 1.8M5.2 18.8L7 17M17 7l1.8-1.8"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "file": '<path d="M7 3h7l5 5v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v5h5"/>',
    "send": '<path d="M4 12l16-8-6 16-3-6.5z"/>',
    "alert": '<path d="M12 3.5l9.5 16.5h-19z"/><path d="M12 10v4.5M12 17.2v.3"/>',
    "play": '<path d="M7 4.5v15l12-7.5z"/>',
}


@register.simple_tag(takes_context=True)
def nav_active(context, href):
    """'active' when the current page belongs to the navigation entry at `href`."""
    path = context["request"].path
    if path == href:
        return "active"
    # Entries whose URL is a prefix of a sibling entry must match exactly.
    nested = {"/cases/": ("/cases/new/",)}
    if any(path.startswith(other) for other in nested.get(href, ())):
        return ""
    if href == "/accounts/officers/" and path.startswith("/monitoring/officers/"):
        return "active"
    return "active" if path.startswith(href) else ""


@register.simple_tag
def mark(size=38):
    """The BCFMS emblem: a 24-spoke wheel set in a hexagonal block.

    It is the project's own mark. It deliberately is not the State Emblem of India,
    whose use is reserved by law for authorised government bodies."""
    spokes = "".join(
        f'<line x1="24" y1="24" x2="{24 + 10.6 * math.cos(i * math.pi / 12):.2f}" y2="{24 + 10.6 * math.sin(i * math.pi / 12):.2f}"/>'
        for i in range(24)
    )
    return mark_safe(
        f'<svg width="{size}" height="{size}" viewBox="0 0 48 48" role="img" aria-label="BCFMS emblem">'
        '<path d="M24 2.5 42.6 13.2v21.6L24 45.5 5.4 34.8V13.2z" fill="#fff" stroke="#0b2a55" stroke-width="2.2" stroke-linejoin="round"/>'
        '<circle cx="24" cy="24" r="11.4" fill="none" stroke="#0b2a55" stroke-width="1.9"/>'
        f'<g stroke="#0b2a55" stroke-width="0.85">{spokes}</g>'
        '<circle cx="24" cy="24" r="2.5" fill="#0b2a55"/></svg>'
    )


@register.simple_tag
def icon(name, size=18):
    body = ICONS.get(name, "")
    return mark_safe(
        f'<svg class="icon" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{body}</svg>'
    )
