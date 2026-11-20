"""Aggregations behind the dashboard and the reports page."""
from collections import OrderedDict

from django.db.models import Count
from django.db.models.functions import TruncMonth

from cases.models import Case

STATUS_ORDER = [s.value for s in Case.Status]


def _bars(pairs):
    """[(label, count)] -> rows with a width percentage relative to the largest value."""
    top = max((count for _, count in pairs), default=0) or 1
    return [{"label": label, "count": count, "pct": round(count * 100 / top, 1)} for label, count in pairs]


def build(cases):
    """Statistics for a queryset of cases."""
    total = cases.count()
    status_counts = dict(cases.values_list("status").annotate(n=Count("id")).order_by())
    labels = dict(Case.Status.choices)

    # One segment per workflow status, in workflow order, for the stacked bar.
    segments = [
        {"status": status, "label": labels[status], "count": status_counts.get(status, 0),
         "share": round(status_counts.get(status, 0) * 100 / total, 1) if total else 0}
        for status in STATUS_ORDER
    ]

    by_type = cases.values_list("crime_type").annotate(n=Count("id")).order_by("-n")
    by_department = cases.values_list("department__name").annotate(n=Count("id")).order_by("-n")
    by_district = cases.values_list("district").annotate(n=Count("id")).order_by("-n")[:8]

    monthly = OrderedDict()
    for month, n in (
        cases.annotate(m=TruncMonth("filed_at")).values_list("m").annotate(n=Count("id")).order_by("m")
    ):
        monthly[month.strftime("%b %Y")] = n
    monthly = list(monthly.items())[-12:]

    department_table = []
    for name in [d for d, _ in by_department]:
        subset = cases.filter(department__name=name)
        counts = dict(subset.values_list("status").annotate(n=Count("id")).order_by())
        resolved = counts.get("CLOSED", 0) + counts.get("CONVICTED", 0)
        n = sum(counts.values())
        department_table.append(
            {"name": name, "total": n, "counts": [counts.get(s, 0) for s in STATUS_ORDER],
             "resolved_pct": round(resolved * 100 / n) if n else 0}
        )

    resolved = status_counts.get("CLOSED", 0) + status_counts.get("CONVICTED", 0)
    return {
        "total": total,
        "open": total - resolved,
        "resolved": resolved,
        "convicted": status_counts.get("CONVICTED", 0),
        "resolution_rate": round(resolved * 100 / total) if total else 0,
        "segments": segments,
        "by_type": _bars(list(by_type)),
        "by_department": _bars(list(by_department)),
        "by_district": _bars(list(by_district)),
        "monthly": _bars(monthly),
        "department_table": department_table,
        "status_labels": [labels[s] for s in STATUS_ORDER],
    }
