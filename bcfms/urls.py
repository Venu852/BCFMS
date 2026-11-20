from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("cases/", include("cases.urls")),
    path("ledger/", include("ledger.urls")),
    path("assistant/", include("assistant.urls")),
    path("api/v1/", include("api.urls")),
    path("", include("core.urls")),
]

handler403 = "core.views.permission_denied"
