"""Database models: templates, generated certificates and bulk batches."""

from django.conf import settings
from django.core.files import File
from django.db import models

from . import layout as layout_module


class CertificateTemplate(models.Model):
    """A blank certificate that details get drawn onto.

    ``layout_overrides`` lets an administrator nudge coordinates without a code
    change -- see :func:`certificates.layout.merge_layout` for the shape.
    """

    name = models.CharField(max_length=120, unique=True)
    image = models.ImageField(upload_to="templates/")
    is_default = models.BooleanField(
        default=False,
        help_text="The template the editor starts with. Only one may be default.",
    )
    layout_overrides = models.JSONField(
        blank=True,
        default=dict,
        help_text=(
            'Optional coordinate tweaks, e.g. {"fields": {"year": {"x": 1120}}}. '
            "Leave as {} to use the measured defaults."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_default", "name"]

    def __str__(self):
        return f"{self.name}{' (default)' if self.is_default else ''}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            CertificateTemplate.objects.exclude(pk=self.pk).update(is_default=False)

    def get_layout(self):
        return layout_module.merge_layout(self.layout_overrides)


def get_default_template():
    """Return the default template, creating it from the supplied scan if needed.

    This keeps the app usable straight after ``migrate`` -- no fixture loading
    or manual upload required.
    """
    template = (
        CertificateTemplate.objects.filter(is_default=True).first()
        or CertificateTemplate.objects.first()
    )
    if template is not None:
        return template

    blank = settings.BLANK_TEMPLATE
    if not blank.exists():
        from .cleaning import build_blank_template

        build_blank_template(settings.SOURCE_TEMPLATE, blank)

    template = CertificateTemplate(name="SVU Registration Certificate", is_default=True)
    with blank.open("rb") as handle:
        template.image.save(blank.name, File(handle), save=False)
    template.save()
    return template


class CertificateBatch(models.Model):
    """One bulk run: the uploaded workbook and the ZIP of certificates it made."""

    name = models.CharField(max_length=200)
    template = models.ForeignKey(
        CertificateTemplate, on_delete=models.SET_NULL, null=True, blank=True
    )
    source_excel = models.FileField(upload_to="batches/excel/", blank=True)
    # One HOD signs a whole cohort, so the signature is uploaded once per batch
    # rather than per student.
    hod_signature = models.ImageField(upload_to="signatures/", blank=True, null=True)
    output_archive = models.FileField(upload_to="batches/output/", blank=True)
    # What the bundle holds: a ZIP of JPGs, a ZIP of PDFs, or one combined PDF.
    output_format = models.CharField(max_length=16, default="jpg")
    total_rows = models.PositiveIntegerField(default=0)
    generated = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    log = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "certificate batches"

    def __str__(self):
        return f"{self.name} ({self.generated}/{self.total_rows})"

    @property
    def log_lines(self):
        return [line for line in self.log.splitlines() if line.strip()]

    @property
    def is_single_pdf(self):
        return self.output_format == "pdf_single"

    @property
    def download_label(self):
        return {
            "pdf": "Download all as PDF (ZIP)",
            "pdf_single": "Download the combined PDF",
        }.get(self.output_format, "Download all as ZIP")


class GeneratedCertificate(models.Model):
    template = models.ForeignKey(
        CertificateTemplate, on_delete=models.SET_NULL, null=True, blank=True
    )
    batch = models.ForeignKey(
        CertificateBatch,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="certificates",
    )

    sl_no = models.CharField("SL. No.", max_length=60, blank=True)
    student_name = models.CharField(max_length=120)
    guardian_name = models.CharField(max_length=120, blank=True)
    registration_number = models.CharField(max_length=60, blank=True)
    year = models.CharField(max_length=12, blank=True)

    photo = models.ImageField(upload_to="photos/", blank=True, null=True)
    hod_signature = models.ImageField(upload_to="signatures/", blank=True, null=True)
    image = models.ImageField(upload_to="certificates/")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.student_name} - {self.registration_number or 'no reg. no.'}"

    @property
    def field_data(self):
        return {name: getattr(self, name) for name in layout_module.FIELD_ORDER}

    @property
    def download_name(self):
        from django.utils.text import slugify

        stem = slugify(self.registration_number) or slugify(self.student_name) or "certificate"
        return f"{stem}.jpg"
