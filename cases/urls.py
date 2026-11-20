from django.urls import path

from . import views

urlpatterns = [
    path("", views.case_list, name="case_list"),
    path("new/", views.case_create, name="case_create"),
    path("tasks/<int:pk>/done/", views.task_done, name="task_done"),
    path("evidence/<str:uid>/", views.evidence_detail, name="evidence_detail"),
    path("evidence/<str:uid>/download/", views.evidence_download, name="evidence_download"),
    path("evidence/<str:uid>/transfer/", views.evidence_transfer, name="evidence_transfer"),
    path("evidence/<str:uid>/report/", views.forensic_add, name="forensic_add"),
    path("<str:case_id>/", views.case_detail, name="case_detail"),
    path("<str:case_id>/edit/", views.case_edit, name="case_edit"),
    path("<str:case_id>/status/", views.case_status, name="case_status"),
    path("<str:case_id>/assign/", views.case_assign, name="case_assign"),
    path("<str:case_id>/accused/add/", views.accused_add, name="accused_add"),
    path("<str:case_id>/accused/<int:pk>/remove/", views.accused_remove, name="accused_remove"),
    path("<str:case_id>/notes/add/", views.note_add, name="note_add"),
    path("<str:case_id>/notes/<str:uid>/remove/", views.note_remove, name="note_remove"),
    path("<str:case_id>/chargesheet/", views.chargesheet_file, name="chargesheet_file"),
    path("<str:case_id>/hearings/add/", views.hearing_add, name="hearing_add"),
    path("<str:case_id>/hearings/<str:uid>/cancel/", views.hearing_cancel, name="hearing_cancel"),
    path("<str:case_id>/verdict/", views.verdict_add, name="verdict_add"),
    path("<str:case_id>/share/", views.share_add, name="share_add"),
    path("<str:case_id>/share/<int:pk>/revoke/", views.share_revoke, name="share_revoke"),
    path("<str:case_id>/tasks/add/", views.task_add, name="task_add"),
    path("<str:case_id>/evidence/add/", views.evidence_add, name="evidence_add"),
    path("<str:case_id>/verify/", views.case_verify, name="case_verify"),
    path("<str:case_id>/export/", views.case_pdf, name="case_pdf"),
]
