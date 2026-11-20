from django.urls import path

from . import views

urlpatterns = [
    path("auth/token/", views.obtain_token, name="api_token"),
    path("cases/", views.case_list, name="api_cases"),
    path("cases/<str:case_id>/", views.case_detail, name="api_case"),
    path("cases/<str:case_id>/audit/", views.case_audit, name="api_case_audit"),
    path("cases/<str:case_id>/verify/", views.case_verify, name="api_case_verify"),
    path("chain/blocks/", views.block_list, name="api_blocks"),
    path("chain/blocks/<int:number>/", views.block_detail, name="api_block"),
    path("chain/tx/<str:tx_hash>/", views.transaction_detail, name="api_tx"),
    path("chain/verify/", views.chain_verify, name="api_chain_verify"),
    path("public/fir-status/", views.public_fir_status, name="api_public_fir"),
]
