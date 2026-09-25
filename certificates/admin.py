from django.contrib import admin
from django.utils.html import format_html

from .models import CertificateBatch, CertificateTemplate, GeneratedCertificate


def _thumb(field_file, width=220):
    if not field_file:
        return "-"
    return format_html(
        '<img src="{}" style="width:{}px;border:1px solid #d8dcd6;border-radius:4px" />',
        field_file.url,
        width,
    )


@admin.register(CertificateTemplate)
class CertificateTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "is_default", "created_at")
    list_filter = ("is_default",)
    search_fields = ("name",)
    readonly_fields = ("created_at", "preview")
    fields = ("name", "image", "preview", "is_default", "layout_overrides", "created_at")

    @admin.display(description="Preview")
    def preview(self, obj):
        return _thumb(obj.image, width=520)


class GeneratedCertificateInline(admin.TabularInline):
    model = GeneratedCertificate
    extra = 0
    can_delete = False
    fields = ("student_name", "registration_number", "year", "created_at")
    readonly_fields = fields
    show_change_link = True


@admin.register(CertificateBatch)
class CertificateBatchAdmin(admin.ModelAdmin):
    list_display = ("name", "total_rows", "generated", "failed", "created_at")
    search_fields = ("name",)
    date_hierarchy = "created_at"
    readonly_fields = ("total_rows", "generated", "failed", "log", "created_at")
    fields = ("name", "template", "source_excel", "hod_signature", "output_archive",
              "total_rows", "generated", "failed", "log", "created_at")
    inlines = [GeneratedCertificateInline]


@admin.register(GeneratedCertificate)
class GeneratedCertificateAdmin(admin.ModelAdmin):
    list_display = (
        "student_name",
        "registration_number",
        "sl_no",
        "year",
        "batch",
        "created_at",
    )
    list_filter = ("year", "batch", "template")
    search_fields = ("student_name", "guardian_name", "registration_number", "sl_no")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "preview")
    fields = (
        "template",
        "batch",
        "sl_no",
        "student_name",
        "guardian_name",
        "registration_number",
        "year",
        "photo",
        "hod_signature",
        "image",
        "preview",
        "created_at",
    )

    @admin.display(description="Certificate")
    def preview(self, obj):
        return _thumb(obj.image, width=620)


admin.site.site_header = "SVU Registration Certificates"
admin.site.site_title = "SVU Certificates"
admin.site.index_title = "Certificate administration"
