"""Views: single editor, bulk generation, history and downloads."""

from django.contrib import messages
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from .excel import (
    HEADINGS,
    PHOTO_BOX_H,
    PHOTO_BOX_W,
    RECOMMENDED_PHOTO,
    SAMPLE_ROWS,
    build_sample_workbook,
)
from .validation import SpreadsheetError
from .forms import BulkGenerateForm, CertificateForm
from .models import CertificateBatch, GeneratedCertificate, get_default_template
from .services import generate_single, image_to_pdf_bytes, run_bulk


def _resolve_template(form):
    return form.cleaned_data.get("template") or get_default_template()


def editor(request):
    """Edit and render one certificate at a time."""
    default_template = get_default_template()
    certificate = None

    if request.method == "POST":
        form = CertificateForm(request.POST, request.FILES)
        if form.is_valid():
            template = _resolve_template(form)
            try:
                certificate = generate_single(
                    form.cleaned_data,
                    template,
                    photo=form.cleaned_data.get("photo"),
                    hod_signature=form.cleaned_data.get("hod_signature"),
                )
            except Exception as error:  # noqa: BLE001 - surfaced to the user
                messages.error(request, f"Could not generate the certificate: {error}")
            else:
                messages.success(
                    request, f"Certificate generated for {certificate.student_name}."
                )
    else:
        form = CertificateForm(initial={"template": default_template})

    return render(
        request,
        "certificates/editor.html",
        {
            "form": form,
            "certificate": certificate,
            "template_obj": default_template,
            "recent": GeneratedCertificate.objects.filter(batch__isnull=True)[:6],
        },
    )


def bulk(request):
    """Generate a whole cohort from one workbook of students and photos."""
    get_default_template()  # make sure a template exists before the form renders
    issues = []
    error_count = warning_count = 0

    if request.method == "POST":
        form = BulkGenerateForm(request.POST, request.FILES)
        if form.is_valid():
            template = _resolve_template(form)
            try:
                batch = run_bulk(
                    form.cleaned_data["excel_file"],
                    template,
                    batch_name=form.cleaned_data.get("batch_name", ""),
                    hod_signature=form.cleaned_data.get("hod_signature"),
                    output_format=form.cleaned_data.get("output_format") or "jpg",
                )
            except SpreadsheetError as error:
                # Every problem is reported at once, each against the cell it
                # lives in, so the sheet can be fixed in a single pass.
                # Blocking errors first; warnings are advisory.
                issues = error.errors + error.warnings
                error_count, warning_count = len(error.errors), len(error.warnings)
                messages.error(request, error.message)
            except Exception as error:  # noqa: BLE001 - surfaced to the user
                messages.error(request, f"Bulk generation failed: {error}")
            else:
                if batch.failed:
                    messages.warning(
                        request,
                        f"{batch.generated} certificate(s) generated, "
                        f"{batch.failed} row(s) failed. See the report below.",
                    )
                else:
                    messages.success(
                        request, f"{batch.generated} certificate(s) generated."
                    )
                return redirect("certificates:batch_detail", pk=batch.pk)
    else:
        form = BulkGenerateForm(initial={"template": get_default_template()})

    return render(
        request,
        "certificates/bulk.html",
        {
            "form": form,
            "issues": issues,
            "error_count": error_count,
            "warning_count": warning_count,
            "headings": HEADINGS,
            "sample_rows": SAMPLE_ROWS,
            "photo_box_w": PHOTO_BOX_W,
            "photo_box_h": PHOTO_BOX_H,
            "recommended_photo": RECOMMENDED_PHOTO,
            "batches": CertificateBatch.objects.all()[:10],
        },
    )


def batch_detail(request, pk):
    batch = get_object_or_404(CertificateBatch, pk=pk)
    return render(
        request,
        "certificates/batch_detail.html",
        {"batch": batch, "certificates": batch.certificates.all()},
    )


def batch_download(request, pk):
    batch = get_object_or_404(CertificateBatch, pk=pk)
    if not batch.output_archive:
        raise Http404("This batch has no generated certificates to download.")
    name = batch.output_archive.name.rsplit("/", 1)[-1]
    return FileResponse(
        batch.output_archive.open("rb"),
        as_attachment=True,
        filename=name,
        content_type="application/pdf" if name.endswith(".pdf") else "application/zip",
    )


def certificate_download(request, pk, fmt="jpg"):
    """Hand over one certificate as a JPG or as a single-page PDF.

    The PDF is made on the spot from the stored image, so nothing is kept
    twice on disk and an old certificate can still be had in either form.
    """
    certificate = get_object_or_404(GeneratedCertificate, pk=pk)
    if fmt == "pdf":
        from PIL import Image

        with Image.open(certificate.image.path) as page:
            data = image_to_pdf_bytes(page)
        response = HttpResponse(data, content_type="application/pdf")
        name = certificate.download_name.rsplit(".", 1)[0]
        response["Content-Disposition"] = f'attachment; filename="{name}.pdf"'
        return response

    return FileResponse(
        certificate.image.open("rb"),
        as_attachment=True,
        filename=certificate.download_name,
        content_type="image/jpeg",
    )


def _discard_file(field):
    """Remove a stored file, tolerating one that cannot be removed yet.

    On Windows a file still being streamed to a browser cannot be unlinked.
    The database row is what the history page shows, so losing the record is
    what matters -- a stray file left on disk is the lesser problem, and far
    better than a delete that fails with a server error.
    """
    if not field:
        return
    try:
        field.delete(save=False)
    except OSError:
        pass


def certificate_delete(request, pk):
    if request.method != "POST":
        return redirect("certificates:history")
    certificate = get_object_or_404(GeneratedCertificate, pk=pk)
    name = certificate.student_name
    for stored in (certificate.image, certificate.photo, certificate.hod_signature):
        _discard_file(stored)
    certificate.delete()
    messages.success(request, f"Deleted the certificate for {name}.")

    # Only ever bounce back to a page on this site.
    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("certificates:history")


def history(request):
    query = request.GET.get("q", "").strip()
    certificates = GeneratedCertificate.objects.select_related("batch")
    if query:
        certificates = certificates.filter(
            Q(student_name__icontains=query)
            | Q(guardian_name__icontains=query)
            | Q(registration_number__icontains=query)
            | Q(sl_no__icontains=query)
        )
    return render(
        request,
        "certificates/history.html",
        {"certificates": certificates[:200], "query": query},
    )


def sample_excel(request):
    response = HttpResponse(
        build_sample_workbook(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="svu_students_template.xlsx"'
    return response
