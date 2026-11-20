from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("login/", views.SignInView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("mfa/verify/", views.mfa_verify, name="mfa_verify"),
    path("mfa/setup/", views.mfa_setup, name="mfa_setup"),
    path("mfa/disable/", views.mfa_disable, name="mfa_disable"),
    path("password/", views.password_change, name="password_change"),
    path("profile/", views.profile, name="profile"),
    path("officers/", views.user_list, name="users"),
    path("officers/new/", views.officer_create, name="officer_create"),
    path("officers/issued/", views.officer_credentials, name="officer_credentials"),
    path("officers/<int:pk>/", views.user_edit, name="user_edit"),
    path("officers/<int:pk>/reset-password/", views.officer_reset_password, name="officer_reset_password"),
    path("departments/", views.departments, name="departments"),
]
