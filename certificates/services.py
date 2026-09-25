"""Turning form input and spreadsheets into finished certificates."""

import io
import zipfile
from pathlib import PurePosixPath

from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.text import slugify

from PIL import Image

from .excel import read_rows
from .layout import PAGE_INCHES
from .models import CertificateBatch, GeneratedCertificate
from .renderer import render_certificate

JPEG_QUALITY = 95

# What a finished certificate can be handed over as.
JPG = "jpg"
PDF = "pdf"
PDF_SINGLE = "pdf_single"
OUTPUT_FORMATS = [
    (JPG, "JPG images - one file per student, in a ZIP"),
    (PDF, "PDF files - one file per student, in a ZIP"),
    (PDF_SINGLE, "One PDF holding every certificate, ready to print"),
]


def image_to_file(image, filename):
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, subsampling=0, dpi=(300, 300))
    return ContentFile(buffer.getvalue(), name=filename)


def _page_resolution(image):
    """Pixels per inch that makes the page the card's real printed size."""
    return image.width / PAGE_INCHES[0]


def image_to_pdf_bytes(image):
    """One certificate as a single-page PDF at the card's printed size."""
    page = image if image.mode == "RGB" else image.convert("RGB")
    buffer = io.BytesIO()
    page.save(buffer, format="PDF", resolution=_page_resolution(page))
    return buffer.getvalue()


def images_to_pdf_bytes(images):
    """Several certificates as one PDF, a page each.

    ``images`` may be lazily opened -- Pillow reads each page's pixels as it
    writes it, so a whole cohort is never held in memory at once.
    """
    pages = [im if im.mode == "RGB" else im.convert("RGB") for im in images]
    first, rest = pages[0], pages[1:]
    buffer = io.BytesIO()
    first.save(
        buffer, format="PDF", save_all=True, append_images=rest,
        resolution=_page_resolution(first),
    )
    return buffer.getvalue()


def certificate_filename(data, fallback="certificate", suffix="jpg"):
    stem = (
        slugify(data.get("registration_number"))
        or slugify(data.get("sl_no"))
        or slugify(data.get("student_name"))
        or fallback
    )
    return f"{stem}.{suffix}"


def _keep_source(field, uploaded, fallback_name):
    """Store the file an image came from alongside the certificate."""
    if uploaded is None:
        return
    try:
        uploaded.seek(0)
        name = PurePosixPath(getattr(uploaded, "name", fallback_name)).name
        field.save(name, ContentFile(uploaded.read()), save=False)
    except (AttributeError, OSError, ValueError):
        pass


def generate_single(
    data, template, photo=None, batch=None, save_photo=True, hod_signature=None
):
    """Render one certificate and store it.

    ``photo`` and ``hod_signature`` may each be an uploaded file, a file-like
    object or ``None``.
    """
    image = render_certificate(
        data,
        template.image.path,
        layout_overrides=template.layout_overrides,
        photo=photo,
        hod_signature=hod_signature,
    )

    certificate = GeneratedCertificate(
        template=template,
        batch=batch,
        sl_no=data.get("sl_no", ""),
        student_name=data.get("student_name", ""),
        guardian_name=data.get("guardian_name", ""),
        registration_number=data.get("registration_number", ""),
        year=data.get("year", ""),
    )

    if save_photo:
        # Keeping the source images is a convenience, not a requirement --
        # never lose the rendered certificate over one.
        _keep_source(certificate.photo, photo, "photo.jpg")
        _keep_source(certificate.hod_signature, hod_signature, "signature.png")

    filename = certificate_filename(data)
    certificate.image.save(filename, image_to_file(image, filename), save=False)
    certificate.save()
    return certificate


def run_bulk(excel_file, template, batch_name="", hod_signature=None,
             output_format=JPG):
    """Generate a certificate per spreadsheet row and bundle them into a ZIP.

    The workbook carries both the text and the embedded photographs. One HOD
    signs a whole cohort, so ``hod_signature`` is uploaded once and stamped on
    every certificate in the batch. A row that fails is logged and skipped --
    one bad picture should not cost the whole run.

    ``output_format`` picks what comes back: a ZIP of JPGs, a ZIP of PDFs, or
    a single PDF with one certificate per page.
    """
    rows, warnings = read_rows(excel_file)

    batch = CertificateBatch(
        name=batch_name or f"Batch {timezone.localtime():%d %b %Y %H:%M}",
        template=template,
        total_rows=len(rows),
        output_format=output_format,
    )
    excel_file.seek(0)
    batch.source_excel.save(
        getattr(excel_file, "name", "students.xlsx"), ContentFile(excel_file.read()), save=False
    )
    _keep_source(batch.hod_signature, hod_signature, "signature.png")
    batch.save()

    log = [w.as_line() for w in warnings]
    log.append(
        "HOD signature applied to every certificate."
        if hod_signature is not None else
        "No HOD signature was uploaded, so that line is left blank."
    )
    log.append(f"{len(rows)} student(s) read, each with a photo.")

    used_names = {}
    generated = failed = 0

    made = []                       # (name without suffix, stored certificate)

    for row in rows:
        row_number = row.get("row_number")
        photo = row.get("photo")
        try:
            certificate = generate_single(
                row, template, photo=photo, batch=batch,
                hod_signature=hod_signature,
            )

            # Two students can share a registration number in a badly filled
            # sheet; make sure neither silently overwrites the other.
            stem = certificate.download_name.rsplit(".", 1)[0]
            count = used_names.get(stem.lower(), 0) + 1
            used_names[stem.lower()] = count
            if count > 1:
                stem = f"{stem}-{count}"

            made.append((stem, certificate))
            generated += 1
        except Exception as error:  # noqa: BLE001 - reported per row
            failed += 1
            log.append(
                f"Row {row_number} ({row.get('student_name', '?')}): "
                f"failed - {error}"
            )
        finally:
            if photo is not None:
                photo.close()

    batch.generated = generated
    batch.failed = failed

    if generated:
        bundle_name = slugify(batch.name) or "certificates"
        if output_format == PDF_SINGLE:
            # One page per certificate. Pillow reads each page's pixels as it
            # writes it, so the whole cohort is never in memory at once.
            pages = [Image.open(c.image.path) for _, c in made]
            try:
                data = images_to_pdf_bytes(pages)
            finally:
                for page in pages:
                    page.close()
            batch.output_archive.save(f"{bundle_name}.pdf", ContentFile(data), save=False)
            log.append(f"Bundled as one PDF of {generated} page(s).")
        else:
            suffix = "pdf" if output_format == PDF else "jpg"
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
                for stem, certificate in made:
                    if output_format == PDF:
                        with Image.open(certificate.image.path) as page:
                            payload = image_to_pdf_bytes(page)
                    else:
                        with certificate.image.open("rb") as handle:
                            payload = handle.read()
                    bundle.writestr(f"{stem}.{suffix}", payload)
            batch.output_archive.save(
                f"{bundle_name}.zip", ContentFile(buffer.getvalue()), save=False
            )
            log.append(f"Bundled as a ZIP of {generated} {suffix.upper()} file(s).")

    batch.log = "\n".join(log)
    batch.save()
    return batch
