from django.urls import path

from . import views

urlpatterns = [
    path("", views.assistant, name="assistant"),
    path("ask/", views.ask, name="assistant_ask"),
    path("status/", views.status, name="assistant_status"),
]
