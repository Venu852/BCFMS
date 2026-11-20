from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/<int:pk>/", views.notification_open, name="notification_open"),
    path("audit/", views.audit_log, name="audit_log"),
    path("monitoring/flags/", views.flags, name="flags"),
    path("monitoring/flags/<int:pk>/review/", views.flag_review, name="flag_review"),
    path("monitoring/officers/<int:pk>/", views.officer_monitor, name="officer_monitor"),
    path("reports/", views.reports, name="reports"),
    path("fir-status/", views.public_lookup, name="public_lookup"),
]
