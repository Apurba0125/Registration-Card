"""Forms for the single-certificate editor and the bulk generator."""

from django import forms
from PIL import Image, UnidentifiedImageError

from .layout import HOD_SIGNATURE_BOX
from .models import CertificateTemplate
from .services import OUTPUT_FORMATS

# No signature size is required: whatever is uploaded is fitted to the space
# above the printed rule. These are only used for guidance in the help text.
SIGNATURE_BOX_W = HOD_SIGNATURE_BOX[2] - HOD_SIGNATURE_BOX[0]
SIGNATURE_BOX_H = HOD_SIGNATURE_BOX[3] - HOD_SIGNATURE_BOX[1]
RECOMMENDED_SIGNATURE = (600, 150)
MAX_SIGNATURE_BYTES = 10 * 1024 * 1024


class SignatureFieldMixin:
    """Shared checks for the HOD signature upload on both forms.

    Only a file that cannot be used at all is refused -- one that is not an
    image, or too large to handle. Any size or shape is accepted and fitted to
    the space above the printed rule.
    """

    def clean_hod_signature(self):
        uploaded = self.cleaned_data.get("hod_signature")
        if not uploaded:
            return uploaded

        if uploaded.size > MAX_SIGNATURE_BYTES:
            raise forms.ValidationError(
                f"The signature is {uploaded.size / 1024 / 1024:.1f} MB, over the "
                f"{MAX_SIGNATURE_BYTES // 1024 // 1024} MB limit. Save a smaller "
                "copy and upload that."
            )
        try:
            uploaded.seek(0)
            with Image.open(uploaded) as probe:
                probe.load()
                width, height = probe.size
        except (UnidentifiedImageError, OSError, ValueError):
            raise forms.ValidationError(
                "That file could not be read as an image. Upload a JPG or PNG "
                "of the signature."
            ) from None
        finally:
            uploaded.seek(0)

        # Any size is accepted -- it is fitted to the space above the rule.
        # A very small one simply prints softer, which is the uploader's call.
        return uploaded


class CertificateForm(SignatureFieldMixin, forms.Form):
    """The fields printed on one certificate."""

    sl_no = forms.CharField(
        label="SL. No.",
        max_length=60,
        widget=forms.TextInput(attrs={"placeholder": "103/BCS/20/002"}),
    )
    student_name = forms.CharField(
        label="Student Name",
        max_length=120,
        widget=forms.TextInput(attrs={"placeholder": "Apurba Sarkar", "autofocus": "autofocus"}),
    )
    guardian_name = forms.CharField(
        label="Guardian Name",
        max_length=120,
        widget=forms.TextInput(attrs={"placeholder": "Susanta Kumar Sarkar"}),
    )
    registration_number = forms.CharField(
        label="Registration Number",
        max_length=60,
        widget=forms.TextInput(attrs={"placeholder": "002-103-2020-017"}),
    )
    year = forms.CharField(
        label="Year",
        max_length=12,
        widget=forms.TextInput(attrs={"placeholder": "2020"}),
    )
    photo = forms.ImageField(
        label="Student Photo",
        help_text="JPG or PNG, any size. Fitted to the photo box automatically.",
    )
    hod_signature = forms.ImageField(
        label="HOD Signature",
        required=False,
        help_text=(
            "JPG or PNG of the signature, any size. A white background is "
            "removed automatically, so a plain scan works."
        ),
    )
    template = forms.ModelChoiceField(
        queryset=CertificateTemplate.objects.all(),
        required=False,
        empty_label=None,
        help_text="Manage templates in the Django admin.",
    )


class BulkGenerateForm(SignatureFieldMixin, forms.Form):
    batch_name = forms.CharField(
        label="Name",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "B.Tech CSE 2020 - first year"}),
    )
    excel_file = forms.FileField(
        label="Student spreadsheet",
        help_text=(
            "One .xlsx holding every student's details and their photo "
            "embedded on the same row."
        ),
    )
    hod_signature = forms.ImageField(
        label="HOD Signature",
        required=False,
        help_text=(
            "Optional, any size. Applied to every certificate in this batch. "
            "A white background is removed automatically."
        ),
    )
    output_format = forms.ChoiceField(
        label="Download as",
        choices=OUTPUT_FORMATS,
        initial="jpg",
        required=False,          # falls back to JPG rather than refusing a run
        help_text="Every certificate is made either way; this picks the bundle.",
    )
    template = forms.ModelChoiceField(
        queryset=CertificateTemplate.objects.all(), required=False, empty_label=None
    )

    def clean_excel_file(self):
        uploaded = self.cleaned_data["excel_file"]
        if not uploaded.name.lower().endswith((".xlsx", ".xlsm", ".csv")):
            raise forms.ValidationError(
                "Upload a .xlsx or .csv file. Older .xls workbooks are not "
                "supported - open it in Excel and re-save as .xlsx. "
                "A .csv cannot carry photos, so use .xlsx if you need them."
            )
        return uploaded
