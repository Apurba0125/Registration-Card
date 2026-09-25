from django.urls import path

from . import views

app_name = "certificates"

urlpatterns = [
    path("", views.editor, name="editor"),
    path("bulk/", views.bulk, name="bulk"),
    path("bulk/sample-excel/", views.sample_excel, name="sample_excel"),
    path("bulk/<int:pk>/", views.batch_detail, name="batch_detail"),
    path("bulk/<int:pk>/download/", views.batch_download, name="batch_download"),
    path("history/", views.history, name="history"),
    path(
        "certificate/<int:pk>/download/",
        views.certificate_download,
        name="certificate_download",
    ),
    path(
        "certificate/<int:pk>/download/<str:fmt>/",
        views.certificate_download,
        name="certificate_download_as",
    ),
    path(
        "certificate/<int:pk>/delete/",
        views.certificate_delete,
        name="certificate_delete",
    ),
]
