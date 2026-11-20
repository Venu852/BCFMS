from django.urls import path

from . import views

urlpatterns = [
    path("", views.explorer, name="explorer"),
    path("feed/", views.feed, name="chain_feed"),
    path("verify/", views.verify, name="chain_verify"),
    path("block/<int:number>/", views.block_detail, name="block_detail"),
    path("tx/<str:tx_hash>/", views.tx_detail, name="tx_detail"),
    path("attack-lab/", views.attack_lab, name="attack_lab"),
    path("attack-lab/run/", views.attack_run, name="attack_run"),
]
