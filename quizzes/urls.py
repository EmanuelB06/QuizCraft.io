from django.urls import path

from . import views

urlpatterns = [
    path("upload/", views.upload, name="upload"),
    path("<uuid:quiz_id>/progress/", views.quiz_progress, name="quiz_progress"),
    path("<uuid:quiz_id>/status/", views.quiz_status, name="quiz_status"),
    path("<uuid:quiz_id>/edit/", views.quiz_edit, name="quiz_edit"),
    path("<uuid:quiz_id>/take/", views.quiz_take, name="quiz_take"),
    path(
        "<uuid:quiz_id>/results/<str:attempt_id>/",
        views.quiz_results,
        name="quiz_results",
    ),
    path("<uuid:quiz_id>/export/", views.quiz_export, name="quiz_export"),
    path("<uuid:quiz_id>/delete/", views.quiz_delete, name="quiz_delete"),
]
