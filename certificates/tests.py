"""Tests for the certificate renderer, spreadsheet handling and views."""

import io
import pathlib
import shutil
import tempfile
import zipfile

import numpy as np
from django.test import TestCase, override_settings
from PIL import Image

from .excel import (
    HEADINGS, PHOTO_BOX_H, PHOTO_BOX_W, build_sample_workbook, read_rows,
)
from .validation import SpreadsheetError
from .layout import CANVAS, FIELDS, HOD_SIGNATURE_BOX, PAGE_INCHES, PHOTO_BOX
from .models import CertificateBatch, GeneratedCertificate, get_default_template
from .renderer import render_certificate
from .services import run_bulk

_MEDIA_ROOT = tempfile.mkdtemp(prefix="svu-tests-")


def tearDownModule():
    shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class CertificateTestCase(TestCase):
    """Base case: generated files go to a temporary media root."""


# The details the supplied card already shows, used as ground truth. None of
# them has a descender, so the bottom of the ink is the baseline.
CARD_SAMPLE = {
    "sl_no": "REG/2022/035",
    "student_name": "DISHA GUCHAIT",
    "guardian_name": "MANAS GUCHAIT",
    "registration_number": "001- 134-2022-004",
    "year": "2022",
}

SAMPLE = {
    "sl_no": "103/BCS/20/002",
    "student_name": "Apurba Sarkar",
    "guardian_name": "Susanta Kumar Sarkar",
    "registration_number": "002-103-2020-017",
    "year": "2020",
}


def make_photo(size=(400, 500), colour=(150, 170, 190), fmt="JPEG"):
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, fmt)
    buffer.seek(0)
    buffer.name = f"photo.{'jpg' if fmt == 'JPEG' else fmt.lower()}"
    return buffer


def make_signature(size=(900, 240), ink=(18, 20, 30), paper=(255, 255, 255), fmt="PNG"):
    """A signature scanned on paper: a dark stroke on a plain background."""
    from PIL import ImageDraw

    image = Image.new("RGB", size, paper)
    draw = ImageDraw.Draw(image)
    w, h = size
    draw.line([(w * .10, h * .60), (w * .35, h * .25), (w * .55, h * .70),
               (w * .78, h * .30)], fill=ink, width=max(3, h // 22))
    buffer = io.BytesIO()
    image.save(buffer, fmt)
    buffer.seek(0)
    buffer.name = f"signature.{fmt.lower()}"
    return buffer


def _upload(handle, name=None):
    """Wrap a buffer as Django would wrap a browser upload."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    handle.seek(0)
    return SimpleUploadedFile(name or getattr(handle, "name", "file"), handle.read())


def card_scale(image):
    """How many image pixels there are per unit of the reference canvas."""
    return image.width / CANVAS["width"], image.height / CANVAS["height"]


def to_pixels(image, region):
    sx, sy = card_scale(image)
    x0, y0, x1, y1 = region
    return (int(round(x0 * sx)), int(round(y0 * sy)),
            int(round(x1 * sx)), int(round(y1 * sy)))


def ink_bbox(image, region, threshold=120):
    """Bounding box of black ink inside ``region``, or None if there is none.

    Regions go in, and boxes come out, in the reference coordinate space of
    :mod:`certificates.layout` -- the card itself may be supplied at any
    resolution, exactly as the renderer treats it.

    The card is printed in a dark green that is just as dark as the text in
    greyscale, so a plain luminance threshold would find the border and the
    watermark everywhere. Keying on saturation as well isolates the neutral
    black of the details from the green printing.
    """
    sx, sy = card_scale(image)
    x0, y0, x1, y1 = to_pixels(image, region)
    rgb = np.asarray(image.convert("RGB"), dtype=int)[y0:y1, x0:x1]
    high, low = rgb.max(axis=2), rgb.min(axis=2)
    ink = ((high - low) < 45) & (high < threshold)
    if not ink.any():
        return None
    ys, xs = np.where(ink)
    return (round((x0 + xs.min()) / sx), round((y0 + ys.min()) / sy),
            round((x0 + xs.max()) / sx), round((y0 + ys.max()) / sy))


class BlankTemplateTests(CertificateTestCase):
    def test_sample_details_are_painted_out(self):
        """The blank keeps its rules, watermark and photo box, loses the text."""
        template = get_default_template()
        blank = Image.open(template.image.path)

        # The card's own sample student (REG/2022/035, DISHA GUCHAIT, ...)
        # must be gone from every value region.
        for region in [
            (306, 148, 700, 204),      # SL. NO. value
            (800, 772, 2100, 858),     # student name
            (800, 946, 2100, 1018),    # guardian name
            (1400, 1286, 1860, 1338),  # registration number
            (1960, 1286, 2140, 1338),  # year
        ]:
            self.assertIsNone(ink_bbox(blank, region), f"ink left behind in {region}")

        # ...while the printed dotted rules underneath survive.
        for region in [
            (800, 863, 2200, 873),
            (800, 1022, 2200, 1032),
            (1400, 1342, 1860, 1351),
        ]:
            self.assertIsNotNone(ink_bbox(blank, region), f"dotted rule lost at {region}")

    def test_printed_labels_survive(self):
        """The colon after SL. NO. and the ruled labels must not be eaten."""
        blank = Image.open(get_default_template().image.path)
        for region, what in [
            ((150, 148, 305, 204), "SL. NO. label and colon"),
            ((440, 820, 710, 865), "Sri / Smt."),
            ((440, 980, 660, 1025), "S/D of"),
        ]:
            self.assertIsNotNone(ink_bbox(blank, region), f"{what} was painted out")

    def test_photo_box_rule_is_intact(self):
        """The card's own photo frame is kept, so we never redraw one."""
        blank = Image.open(get_default_template().image.path)
        left, top, right, bottom = PHOTO_BOX
        self.assertIsNotNone(ink_bbox(blank, (left - 12, top - 12, left + 4, bottom)))
        self.assertIsNone(
            ink_bbox(blank, (left + 20, top + 20, right - 20, bottom - 20)),
            "the inside of the photo box should be empty",
        )


class RendererTests(CertificateTestCase):
    def setUp(self):
        self.template = get_default_template()

    def render(self, data=None, **kwargs):
        return render_certificate(
            data if data is not None else SAMPLE, self.template.image.path, **kwargs
        )

    def test_values_land_where_the_card_has_its_own(self):
        """Render the card's own sample student and compare, value by value.

        Using the card's exact strings means position, width and height can all
        be checked directly. Each window is kept clear of the printed labels
        either side, of the dotted rule below, and of the photo box, which
        shares the name row.
        """
        from django.conf import settings

        original = Image.open(settings.SOURCE_TEMPLATE)
        rendered = self.render(CARD_SAMPLE)
        windows = {
            "sl_no": ((306, 148, 700, 204), "left"),
            "student_name": ((800, 772, 2180, 858), "center"),
            "guardian_name": ((800, 946, 2180, 1018), "center"),
            "registration_number": ((1400, 1286, 1860, 1338), "center"),
            "year": ((1960, 1286, 2140, 1338), "center"),
        }
        # One card pixel is worth this many reference units, so a card
        # supplied at lower resolution can only be measured that precisely.
        px = 1 / min(card_scale(rendered))

        for field, (window, align) in windows.items():
            want = ink_bbox(original, window)
            got = ink_bbox(rendered, window)
            self.assertIsNotNone(want, f"{field}: the card has no sample value here")
            self.assertIsNotNone(got, f"{field} was not drawn")

            self.assertLessEqual(
                abs(got[3] - want[3]), 3 * px + 2,
                f"{field} baseline off: {got[3]} vs {want[3]}",
            )
            self.assertLessEqual(
                abs((got[3] - got[1]) - (want[3] - want[1])), 4 * px + 4,
                f"{field} ink height {got[3]-got[1]} vs the card's {want[3]-want[1]}",
            )
            if align == "left":
                self.assertLessEqual(
                    abs(got[0] - want[0]), 4 * px + 4,
                    f"{field} left edge off: {got[0]} vs {want[0]}",
                )
            else:
                self.assertLessEqual(
                    abs((got[0] + got[2]) / 2 - (want[0] + want[2]) / 2),
                    30,
                    f"{field} is not centred like the card: {got} vs {want}",
                )

    def test_descenders_hang_below_the_baseline(self):
        """A name with a descender keeps its baseline on the rule."""
        window = (800, 772, 2180, 862)
        with_tail = ink_bbox(self.render({"student_name": "Apurba Sarkar"}), window)
        without = ink_bbox(self.render({"student_name": "DISHA GUCHAIT"}), window)
        self.assertGreater(with_tail[3], without[3], "the 'p' should hang below")
        self.assertLess(with_tail[3] - without[3], 22)
        self.assertLess(with_tail[3], 862, "the descender reaches the dotted rule")

    def test_blank_fields_are_left_empty(self):
        image = self.render({"student_name": "Rima Das"})
        self.assertIsNone(ink_bbox(image, (1400, 1286, 1860, 1338)))
        self.assertIsNotNone(ink_bbox(image, (800, 772, 2100, 858)))

    def test_long_values_shrink_instead_of_overflowing(self):
        """A long name must stay between the printed label and the rule end."""
        long_name = "Chandrashekhar Venkataraman Subramaniam Iyer"
        image = self.render({**SAMPLE, "student_name": long_name})
        # Stop short of x=2190: the printed photo box shares these rows.
        box = ink_bbox(image, (720, 772, 2180, 858))
        self.assertIsNotNone(box)
        self.assertGreater(box[0], 720, "name runs into the 'Sri / Smt.' label")
        self.assertLess(box[2], 2170, "name runs past the end of the rule")

    def test_long_values_are_shrunk_not_clipped(self):
        window = (720, 772, 2180, 858)   # clear of the printed photo box
        short = ink_bbox(self.render({"student_name": "Ria Sen"}), window)
        long = ink_bbox(
            self.render({"student_name": "Chandrashekhar Venkataraman Subramaniam Iyer"}),
            window,
        )
        self.assertLess(long[3] - long[1], short[3] - short[1])

    def test_photo_fills_the_frame_without_distortion(self):
        image = self.render(photo=make_photo(size=(600, 200), colour=(10, 120, 200)))
        left, top, right, bottom = to_pixels(image, PHOTO_BOX)
        patch = np.array(image)[top + 3:bottom - 3, left + 3:right - 3]
        # The whole frame should be the photo's colour, i.e. fully covered.
        self.assertGreater(patch[:, :, 2].mean(), 150)
        self.assertLess(patch[:, :, 0].mean(), 80)

    def test_layout_overrides_move_a_field(self):
        blank_area = (1200, 1400, 1800, 1560)   # empty on the card
        self.assertIsNone(ink_bbox(self.render(), blank_area))
        shifted = self.render(
            layout_overrides={"fields": {"year": {"x": 1500, "baseline": 1500}}}
        )
        self.assertIsNotNone(ink_bbox(shifted, blank_area))
        self.assertIsNone(ink_bbox(shifted, (1960, 1286, 2140, 1338)))

    def test_photo_frame_is_not_redrawn_over_the_printed_one(self):
        """The card prints its own photo rule, so we must not add a second."""
        image = self.render(photo=make_photo(size=(300, 380), colour=(240, 240, 240)))
        left, top, right, bottom = to_pixels(image, PHOTO_BOX)
        # Just inside the printed rule there should be photo, not a drawn border.
        strip = np.array(image)[top + 2:top + 5, left + 4:right - 4]
        self.assertGreater(strip.mean(), 180, "a second frame was drawn inside the box")


class SignatureTests(CertificateTestCase):
    """The HOD signature is fitted above the printed rule, never cropped."""

    def setUp(self):
        self.template = get_default_template()

    def render(self, **kwargs):
        return render_certificate(SAMPLE, self.template.image.path, **kwargs)

    def test_the_blank_keeps_the_hod_rule_and_label(self):
        blank = Image.open(self.template.image.path)
        # The sample signature is gone...
        self.assertIsNone(ink_bbox(blank, (190, 1450, 620, 1520)))
        # ...but the green rule and the printed "HOD" remain.
        rgb = np.asarray(blank.convert("RGB"), dtype=int)
        for region, what in [((182, 1520, 622, 1532), "HOD rule"),
                             ((330, 1544, 440, 1576), "HOD label")]:
            x0, y0, x1, y1 = to_pixels(blank, region)
            patch = rgb[y0:y1, x0:x1]
            r, g, b = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
            green = ((g > r + 18) & (g > b + 10) & (g < 190)).sum()
            self.assertGreater(green, 40, f"{what} was painted out")

    def test_signature_lands_above_the_rule(self):
        plain = self.render()
        signed = self.render(hod_signature=make_signature())
        window = (190, 1420, 620, 1521)
        self.assertIsNone(ink_bbox(plain, window), "nothing should be there yet")
        box = ink_bbox(signed, window)
        self.assertIsNotNone(box, "the signature was not drawn")
        # It must not spill into the rule (y=1524) or past the box sides.
        self.assertLess(box[3], 1524)
        self.assertGreaterEqual(box[0], HOD_SIGNATURE_BOX[0] - 4)
        self.assertLessEqual(box[2], HOD_SIGNATURE_BOX[2] + 4)

    def test_signature_is_fitted_whole_not_cropped(self):
        """A very wide signature is scaled down, never trimmed to fit."""
        wide = self.render(hod_signature=make_signature(size=(2000, 200)))
        box = ink_bbox(wide, (150, 1400, 660, 1521))
        width = box[2] - box[0]
        height = box[3] - box[1]
        self.assertLessEqual(width, HOD_SIGNATURE_BOX[2] - HOD_SIGNATURE_BOX[0] + 4)
        # 10:1 in, so it should still be far wider than it is tall.
        self.assertGreater(width / height, 5)

    def test_white_paper_does_not_cover_the_watermark(self):
        """A plain scan must not stamp a white rectangle on the card."""
        signed = self.render(hod_signature=make_signature())
        x0, y0, x1, y1 = to_pixels(signed, (200, 1435, 600, 1500))
        patch = np.asarray(signed.convert("RGB"), dtype=int)[y0:y1, x0:x1]
        r, g, b = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
        watermark = ((g > r + 8) & (g > b + 4) & (g < 245)).sum()
        self.assertGreater(watermark, 50, "the watermark was covered over")

    def test_transparent_signature_is_used_as_supplied(self):
        source = Image.new("RGBA", (600, 160), (0, 0, 0, 0))
        from PIL import ImageDraw
        ImageDraw.Draw(source).line([(40, 120), (560, 40)], fill=(12, 14, 26, 255), width=8)
        signed = self.render(hod_signature=source)
        self.assertIsNotNone(ink_bbox(signed, (190, 1420, 620, 1521)))

    def test_no_signature_leaves_the_line_blank(self):
        self.assertIsNone(ink_bbox(self.render(), (190, 1420, 620, 1521)))


class SignatureFormTests(CertificateTestCase):
    def test_a_tiny_signature_is_accepted(self):
        """No signature size is required either -- it is fitted to the space."""
        from .forms import CertificateForm

        form = CertificateForm(
            SAMPLE,
            {"photo": _upload(make_photo()),
             "hod_signature": _upload(make_signature(size=(60, 20)))},
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_a_normal_signature_is_accepted(self):
        from .forms import CertificateForm

        form = CertificateForm(
            SAMPLE,
            {"photo": _upload(make_photo()),
             "hod_signature": _upload(make_signature())},
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_a_file_that_is_not_an_image_is_rejected(self):
        from .forms import BulkGenerateForm

        handle = io.BytesIO(b"this is not a picture")
        handle.name = "signature.png"
        form = BulkGenerateForm(
            {}, {"excel_file": _upload(io.BytesIO(b"STUDENT_NAME\nRia Sen\n"), "s.csv"),
                 "hod_signature": _upload(handle, "signature.png")},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("hod_signature", form.errors)


class SpreadsheetTests(CertificateTestCase):
    def test_sample_workbook_round_trips(self):
        handle = io.BytesIO(build_sample_workbook())
        handle.name = "sample.xlsx"
        rows, warnings = read_rows(handle)
        self.assertEqual(len(rows), 3)
        self.assertEqual(warnings, [])
        self.assertEqual(rows[0]["student_name"], "Apurba Sarkar")
        self.assertIsNotNone(rows[0]["photo"], "the template should ship with a photo")

    def test_headings_match_loosely(self):
        """Case, spaces and underscores are all ignored when matching."""
        handle = workbook_with_photos(
            [["S1", "Ria Sen", "N Sen", "R-1", "2021"]],
            headings=["sl no", "Student Name", "guardian-name",
                      "registration number", "Year", "photo"],
        )
        rows, _ = read_rows(handle)
        self.assertEqual(rows[0]["student_name"], "Ria Sen")
        self.assertEqual(rows[0]["registration_number"], "R-1")

    def test_numeric_year_does_not_gain_a_decimal(self):
        """Excel hands back 2021.0 for a plain number; it must print as 2021."""
        handle = workbook_with_photos([["S1", "Ria Sen", "N Sen", "R-1", 2021]])
        rows, _ = read_rows(handle)
        self.assertEqual(rows[0]["year"], "2021")

    def test_row_without_a_name_blocks_and_names_the_cell(self):
        handle = workbook_with_photos(
            [student_row(1), ["S2", "", "N Sen", "R-2", "2021"]]
        )
        with self.assertRaises(SpreadsheetError) as caught:
            read_rows(handle)
        (issue,) = caught.exception.errors
        self.assertIn("B3", issue.location)
        self.assertIn("STUDENT_NAME is empty", issue.problem)
        self.assertIn("B3", issue.fix)

    def test_missing_required_column_is_reported(self):
        handle = io.BytesIO(b"NAME,YEAR\nRia Sen,2021\n")
        handle.name = "s.csv"
        with self.assertRaises(SpreadsheetError):
            read_rows(handle)

    def test_xls_is_rejected_with_advice(self):
        handle = io.BytesIO(b"anything")
        handle.name = "students.xls"
        with self.assertRaises(SpreadsheetError) as caught:
            read_rows(handle)
        (issue,) = caught.exception.errors
        self.assertIn("Save As", issue.fix)
        self.assertIn(".xlsx", issue.fix)


# Every column is required and every student needs a photo, so a complete row
# is the baseline a test starts from and then breaks deliberately.
def student_row(n=1):
    return [f"S{n}", f"Student {n}", f"Guardian {n}", f"R-{n}", "2021"]


def workbook_with_photos(rows, photo_rows=None, photo_column=6, size=(400, 500),
                         headings=None):
    """Build an .xlsx with the given rows and a picture on each of them.

    ``photo_rows`` defaults to every data row; pass an explicit list to leave
    some without a picture.
    """
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as ExcelImage
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headings or HEADINGS)
    for row in rows:
        sheet.append(row)
    if photo_rows is None:
        photo_rows = range(2, len(rows) + 2)
    for row_number in photo_rows:
        picture = ExcelImage(make_photo(size=size, fmt="PNG"))
        sheet.add_image(picture, f"{get_column_letter(photo_column)}{row_number}")

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    buffer.name = "students.xlsx"
    return buffer


class EmbeddedPhotoTests(CertificateTestCase):
    def test_each_row_gets_its_own_photo(self):
        handle = workbook_with_photos([student_row(1), student_row(2)])
        rows, _ = read_rows(handle)
        self.assertTrue(all(r["photo"] for r in rows))

    def test_photos_are_matched_by_the_row_they_sit_on(self):
        """Distinct sizes prove each row picked up its own picture."""
        from openpyxl import Workbook
        from openpyxl.drawing.image import Image as ExcelImage

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(HEADINGS)
        sheet.append(student_row(1))
        sheet.append(student_row(2))
        sheet.add_image(ExcelImage(make_photo(size=(400, 500), fmt="PNG")), "F2")
        sheet.add_image(ExcelImage(make_photo(size=(640, 480), fmt="PNG")), "F3")
        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        buffer.name = "students.xlsx"

        rows, _ = read_rows(buffer)
        self.assertEqual(Image.open(rows[0]["photo"]).size, (400, 500))
        self.assertEqual(Image.open(rows[1]["photo"]).size, (640, 480))

    def test_embedded_photo_keeps_its_full_resolution(self):
        """Excel displays a picture scaled down; the original must come back."""
        handle = workbook_with_photos([student_row()], size=(400, 520))
        rows, _ = read_rows(handle)
        self.assertEqual(Image.open(rows[0]["photo"]).size, (400, 520))

    def test_picture_outside_the_photo_column_still_matches_its_row(self):
        handle = workbook_with_photos([student_row()], photo_column=2)
        rows, _ = read_rows(handle)
        self.assertIsNotNone(rows[0]["photo"])

    def test_photo_column_wins_when_a_row_holds_two_pictures(self):
        from openpyxl import Workbook
        from openpyxl.drawing.image import Image as ExcelImage

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(HEADINGS)
        sheet.append(student_row())
        sheet.add_image(ExcelImage(make_photo(size=(40, 40), fmt="PNG")), "A2")
        sheet.add_image(ExcelImage(make_photo(size=(300, 390), fmt="PNG")), "F2")
        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        buffer.name = "students.xlsx"

        rows, _ = read_rows(buffer)
        self.assertEqual(Image.open(rows[0]["photo"]).size, (300, 390))

    def test_a_row_without_a_picture_is_blocked(self):
        """A photo is required, so the row is refused with the cell to use."""
        handle = workbook_with_photos([student_row(1), student_row(2)], photo_rows=[2])
        with self.assertRaises(SpreadsheetError) as caught:
            read_rows(handle)
        (issue,) = caught.exception.errors
        self.assertIn("F3", issue.location)
        self.assertIn("no photograph", issue.problem)
        self.assertIn("Insert > Pictures", issue.fix)

    def test_a_csv_is_refused_because_it_cannot_carry_photos(self):
        handle = io.BytesIO(b"SL_NO,STUDENT_NAME\nS1,Ria Sen\n")
        handle.name = "s.csv"
        with self.assertRaises(SpreadsheetError) as caught:
            read_rows(handle)
        (issue,) = caught.exception.errors
        self.assertIn("text only", issue.problem)
        self.assertIn(".xlsx", issue.fix)


class ValidationTests(CertificateTestCase):
    """Every problem must name the cell to edit and say how to fix it."""

    def build(self, rows, pictures=None, headings=None, lead=()):
        """A sheet of complete rows, each with a photo unless told otherwise."""
        from openpyxl import Workbook
        from openpyxl.drawing.image import Image as ExcelImage

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Students"
        for line in lead:
            sheet.append(line)
        sheet.append(headings or HEADINGS)
        for row in rows:
            sheet.append(row)
        if pictures is None:
            first = len(lead) + 2
            pictures = [(f"F{first + i}", make_photo(fmt="PNG"))
                        for i in range(len(rows))]
        for cell, photo in pictures:
            sheet.add_image(ExcelImage(photo), cell)
        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        buffer.name = "students.xlsx"
        return buffer

    def errors_from(self, handle):
        with self.assertRaises(SpreadsheetError) as caught:
            read_rows(handle)
        return caught.exception.errors

    def warnings_from(self, handle):
        _, warnings = read_rows(handle)
        return warnings

    ROW = ["S1", "Ria Sen", "N Sen", "R-1", "2021"]

    def test_a_small_photo_is_used_anyway_with_a_note(self):
        """No photo size is required -- a small one is used and explained."""
        handle = self.build([self.ROW], [("F2", make_photo(size=(90, 110), fmt="PNG"))])
        rows, warnings = read_rows(handle)
        self.assertIsNotNone(rows[0]["photo"], "the photo should still be used")
        (note,) = [w for w in warnings if "enlarged" in w.problem]
        self.assertIn("F2", note.location)
        self.assertIn("90 x 110", note.problem)
        self.assertIn(f"{PHOTO_BOX_W} x {PHOTO_BOX_H}", note.problem)
        self.assertIn("still made", note.problem)
        self.assertIn("Nothing has to change", note.fix)

    def test_an_oddly_shaped_photo_is_used_anyway_with_a_note(self):
        handle = self.build([self.ROW], [("F2", make_photo(size=(1600, 500), fmt="PNG"))])
        rows, warnings = read_rows(handle)
        self.assertIsNotNone(rows[0]["photo"])
        (note,) = [w for w in warnings if "trimmed" in w.problem]
        self.assertIn("F2", note.location)
        self.assertIn("wider", note.problem)
        self.assertIn("still made", note.problem)

    def test_photo_size_never_blocks_a_run(self):
        """Tiny, huge and odd shapes all generate; none is an error."""
        for size in [(40, 40), (90, 110), (900, 200), (3000, 3600)]:
            handle = self.build([self.ROW], [("F2", make_photo(size=size, fmt="PNG"))])
            rows, _ = read_rows(handle)          # must not raise
            self.assertIsNotNone(rows[0]["photo"], f"{size} was not used")

    def test_a_photo_the_right_size_draws_no_comment(self):
        handle = self.build([self.ROW], [("F2", make_photo(size=(600, 700), fmt="PNG"))])
        _, warnings = read_rows(handle)
        self.assertEqual([w for w in warnings if "enlarged" in w.problem or "trimmed" in w.problem], [])

    def test_value_over_the_field_limit_is_blocked(self):
        handle = self.build([["X" * 80, "Ria Sen", "N Sen", "R-1", "2021"]])
        (issue,) = self.errors_from(handle)
        self.assertIn("A2", issue.location)
        self.assertIn("80 characters", issue.problem)
        self.assertIn("A2", issue.fix)

    def test_unreadable_heading_row_says_what_was_found(self):
        handle = self.build([["1", "Ria Sen"]], pictures=[],
                            headings=["Roll", "Pupil", "Parent"])
        (issue,) = self.errors_from(handle)
        self.assertIn("Roll", issue.problem)
        self.assertIn("STUDENT_NAME", issue.fix)

    def test_headings_below_a_title_row_are_still_found(self):
        handle = self.build([self.ROW], lead=[["SVU 2021 admissions"], []])
        rows, _ = read_rows(handle)
        self.assertEqual(rows[0]["student_name"], "Ria Sen")

    def test_instructions_sheet_does_not_hide_the_student_list(self):
        from openpyxl import Workbook

        workbook = Workbook()
        notes = workbook.active
        notes.title = "Instructions"
        notes.append(["Read me first"])
        sheet = workbook.create_sheet("Students")
        sheet.append(HEADINGS)
        sheet.append(self.ROW)
        from openpyxl.drawing.image import Image as ExcelImage
        sheet.add_image(ExcelImage(make_photo(fmt="PNG")), "F2")
        workbook.active = 0            # leave the wrong sheet selected
        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        buffer.name = "students.xlsx"

        rows, _ = read_rows(buffer)
        self.assertEqual(rows[0]["student_name"], "Ria Sen")

    def test_an_extra_column_is_ignored_with_a_warning(self):
        handle = self.build([self.ROW + [""]], headings=HEADINGS + ["REMARKS"])
        (warning,) = [w for w in self.warnings_from(handle) if "REMARKS" in w.problem]
        self.assertIn("G1", warning.location)

    def test_a_mistyped_required_heading_is_blocked_by_name(self):
        """GUARDAIN_NAME means GUARDIAN_NAME is missing, so say exactly that."""
        handle = self.build(
            [self.ROW],
            headings=["SL_NO", "STUDENT_NAME", "GUARDAIN_NAME",
                      "REGISTRATION_NUMBER", "YEAR", "PHOTO"],
        )
        (issue,) = self.errors_from(handle)
        self.assertIn("GUARDIAN_NAME", issue.problem)
        self.assertIn("all are required", issue.fix)

    def test_year_that_is_not_a_year_warns(self):
        handle = self.build([["S1", "Ria Sen", "N Sen", "R-1", "last year"]])
        (warning,) = [w for w in self.warnings_from(handle) if "YEAR" in w.problem]
        self.assertIn("E2", warning.location)

    def test_filename_typed_in_the_photo_column_warns(self):
        handle = self.build([["S1", "Ria Sen", "N Sen", "R-1", "2021", "student1.jpg"]])
        (warning,) = [w for w in self.warnings_from(handle) if "PHOTO column" in w.problem]
        self.assertIn("F2", warning.location)
        self.assertIn("Insert > Pictures", warning.fix)

    def test_duplicate_registration_number_is_blocked(self):
        handle = self.build([self.ROW, ["S2", "Mira Roy", "S Roy", "R-1", "2021"]])
        (issue,) = self.errors_from(handle)
        self.assertIn("D3", issue.location)
        self.assertIn("already used on row 2", issue.problem)
        self.assertIn("must be unique", issue.problem)

    def test_duplicate_sl_no_is_blocked(self):
        handle = self.build([self.ROW, ["S1", "Mira Roy", "S Roy", "R-2", "2021"]])
        (issue,) = self.errors_from(handle)
        self.assertIn("A3", issue.location)
        self.assertIn("must be unique", issue.problem)

    def test_uniqueness_ignores_letter_case(self):
        handle = self.build([self.ROW, ["S2", "Mira Roy", "S Roy", "r-1", "2021"]])
        (issue,) = self.errors_from(handle)
        self.assertIn("D3", issue.location)

    def test_every_column_is_required(self):
        for column, blank_row, cell in [
            ("SL_NO", ["", "Ria Sen", "N Sen", "R-1", "2021"], "A2"),
            ("GUARDIAN_NAME", ["S1", "Ria Sen", "", "R-1", "2021"], "C2"),
            ("REGISTRATION_NUMBER", ["S1", "Ria Sen", "N Sen", "", "2021"], "D2"),
            ("YEAR", ["S1", "Ria Sen", "N Sen", "R-1", ""], "E2"),
        ]:
            with self.subTest(column=column):
                (issue,) = self.errors_from(self.build([blank_row]))
                self.assertIn(cell, issue.location)
                self.assertIn(column + " is empty", issue.problem)
                self.assertIn(cell, issue.fix)

    def test_picture_on_an_empty_row_warns(self):
        handle = self.build(
            [self.ROW],
            [("F2", make_photo(fmt="PNG")), ("F9", make_photo(fmt="PNG"))],
        )
        (warning,) = [w for w in self.warnings_from(handle) if "no student" in w.problem]
        self.assertIn("row 9", warning.location)

    def test_every_problem_is_reported_in_one_pass(self):
        """The user should not have to re-upload to discover the next fault."""
        handle = self.build([
            ["S1", "", "N Sen", "R-1", "2021"],
            ["X" * 80, "Mira Roy", "S Roy", "R-2", "2021"],
        ], pictures=[("F2", make_photo(fmt="PNG"))])
        errors = self.errors_from(handle)
        cells = {e.location.rsplit(" ", 1)[-1] for e in errors}
        self.assertEqual(cells, {"B2", "A3", "F3"})   # name, length, missing photo

    def test_a_good_sheet_raises_nothing(self):
        handle = self.build([self.ROW], [("F2", make_photo(fmt="PNG"))])
        rows, warnings = read_rows(handle)
        self.assertEqual(len(rows), 1)
        self.assertEqual(warnings, [])


class BulkTests(CertificateTestCase):
    def test_bulk_run_generates_and_bundles(self):
        excel = io.BytesIO(build_sample_workbook())
        excel.name = "students.xlsx"

        batch = run_bulk(excel, get_default_template(), batch_name="Test")

        self.assertEqual((batch.total_rows, batch.generated, batch.failed), (3, 3, 0))
        with zipfile.ZipFile(batch.output_archive.path) as bundle:
            self.assertEqual(len(bundle.namelist()), 3)
            self.assertIn("002-103-2020-017.jpg", bundle.namelist())

    def test_embedded_photos_reach_the_certificates(self):
        excel = io.BytesIO(build_sample_workbook())
        excel.name = "students.xlsx"
        batch = run_bulk(excel, get_default_template())

        self.assertEqual(batch.generated, 3)
        self.assertTrue(all(c.photo for c in batch.certificates.all()))
        self.assertTrue(any("3 student(s) read" in line for line in batch.log_lines))

    def test_the_report_says_how_many_students_were_read(self):
        handle = workbook_with_photos([student_row(1), student_row(2)])
        batch = run_bulk(handle, get_default_template())

        self.assertEqual((batch.generated, batch.failed), (2, 0))
        self.assertTrue(any("2 student(s) read" in line for line in batch.log_lines))

    def test_a_duplicate_registration_number_stops_the_run(self):
        """Uniqueness is enforced, so no batch is created at all."""
        handle = workbook_with_photos(
            [["S1", "Ria Sen", "N Sen", "R-1", "2021"],
             ["S2", "Mira Roy", "S Roy", "R-1", "2021"]]
        )
        before = CertificateBatch.objects.count()
        with self.assertRaises(SpreadsheetError):
            run_bulk(handle, get_default_template())
        self.assertEqual(CertificateBatch.objects.count(), before)


def pdf_pages_and_inches(data):
    """Page count and page size, read straight out of the PDF bytes."""
    import re

    pages = len(re.findall(rb"/Type\s*/Page[^s]", data))
    box = re.search(rb"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", data)
    size = tuple(round(float(box.group(i)) / 72, 2) for i in (3, 4)) if box else None
    return pages, size


class PdfOutputTests(CertificateTestCase):
    """A certificate can be had as a JPG or as a PDF at its printed size."""

    def setUp(self):
        self.template = get_default_template()

    def a_certificate(self):
        self.client.post("/", {**SAMPLE, "template": self.template.pk,
                               "photo": make_photo()})
        return GeneratedCertificate.objects.get()

    def test_single_certificate_downloads_as_pdf(self):
        certificate = self.a_certificate()
        response = self.client.get(f"/certificate/{certificate.pk}/download/pdf/")
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("002-103-2020-017.pdf", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_single_certificate_still_downloads_as_jpg(self):
        certificate = self.a_certificate()
        response = self.client.get(f"/certificate/{certificate.pk}/download/")
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertIn("002-103-2020-017.jpg", response["Content-Disposition"])

    def test_pdf_page_is_the_cards_printed_size(self):
        """Nine by six inches, so a printed certificate comes out actual size."""
        certificate = self.a_certificate()
        data = self.client.get(f"/certificate/{certificate.pk}/download/pdf/").content
        pages, inches = pdf_pages_and_inches(data)
        self.assertEqual(pages, 1)
        self.assertEqual(inches, PAGE_INCHES)

    def test_bulk_can_bundle_pdfs(self):
        handle = workbook_with_photos([student_row(1), student_row(2)])
        batch = run_bulk(handle, self.template, output_format="pdf")
        with zipfile.ZipFile(batch.output_archive.path) as bundle:
            names = bundle.namelist()
            first = bundle.read(names[0])
        self.assertEqual(len(names), 2)
        self.assertTrue(all(n.endswith(".pdf") for n in names), names)
        self.assertTrue(first.startswith(b"%PDF-"))
        self.assertEqual(pdf_pages_and_inches(first)[1], PAGE_INCHES)

    def test_bulk_can_make_one_pdf_of_every_certificate(self):
        handle = workbook_with_photos([student_row(i) for i in range(1, 4)])
        batch = run_bulk(handle, self.template, output_format="pdf_single")
        self.assertTrue(batch.output_archive.name.endswith(".pdf"))
        self.assertTrue(batch.is_single_pdf)
        data = pathlib.Path(batch.output_archive.path).read_bytes()
        pages, inches = pdf_pages_and_inches(data)
        self.assertEqual(pages, 3, "one page per certificate")
        self.assertEqual(inches, PAGE_INCHES)

    def test_bulk_still_bundles_jpgs_by_default(self):
        handle = workbook_with_photos([student_row()])
        batch = run_bulk(handle, self.template)
        self.assertEqual(batch.output_format, "jpg")
        with zipfile.ZipFile(batch.output_archive.path) as bundle:
            self.assertTrue(all(n.endswith(".jpg") for n in bundle.namelist()))

    def test_the_batch_page_names_the_format_it_produced(self):
        handle = workbook_with_photos([student_row()])
        batch = run_bulk(handle, self.template, output_format="pdf_single")
        page = self.client.get(f"/bulk/{batch.pk}/").content.decode()
        self.assertIn("combined PDF", page)

    def test_a_duplicate_name_stays_distinct_in_a_pdf_bundle(self):
        """The -2 suffix applies whatever the bundle holds."""
        handle = workbook_with_photos(
            [["S1", "Ria Sen", "N Sen", "R-1", "2021"],
             ["S2", "Ria Sen", "N Sen", "R-1 ", "2021"]]
        )
        with self.assertRaises(SpreadsheetError):
            run_bulk(handle, self.template, output_format="pdf")


class ViewTests(CertificateTestCase):
    def setUp(self):
        self.template = get_default_template()

    def test_pages_load(self):
        for url in ["/", "/bulk/", "/history/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_sample_excel_downloads(self):
        response = self.client.get("/bulk/sample-excel/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response["Content-Type"])

    def test_editor_creates_a_certificate(self):
        response = self.client.post(
            "/", {**SAMPLE, "template": self.template.pk, "photo": make_photo()}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.status_code, 200)
        certificate = GeneratedCertificate.objects.get()
        self.assertEqual(certificate.student_name, "Apurba Sarkar")
        self.assertTrue(certificate.image.name.endswith(".jpg"))
        self.assertTrue(certificate.photo.name)

    def test_editor_requires_a_student_name(self):
        self.client.post("/", {"year": "2020", "template": self.template.pk})
        self.assertEqual(GeneratedCertificate.objects.count(), 0)

    def test_certificate_download_is_an_attachment(self):
        self.client.post("/", {**SAMPLE, "template": self.template.pk,
                               "photo": make_photo()})
        certificate = GeneratedCertificate.objects.get()
        response = self.client.get(f"/certificate/{certificate.pk}/download/")
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertIn("002-103-2020-017.jpg", response["Content-Disposition"])

    def test_bad_spreadsheet_is_reported_not_raised(self):
        handle = io.BytesIO(b"NOPE\n1\n")
        handle.name = "bad.csv"
        response = self.client.post(
            "/bulk/", {"excel_file": handle, "template": self.template.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot carry photographs")
        self.assertEqual(CertificateBatch.objects.count(), 0)

    def test_delete_survives_a_file_that_is_still_open(self):
        """Downloading then deleting must not 500 while the file is locked."""
        self.client.post("/", {**SAMPLE, "template": self.template.pk,
                               "photo": make_photo()})
        certificate = GeneratedCertificate.objects.get()
        # Leave the download stream open, as a browser mid-download would.
        stream = self.client.get(f"/certificate/{certificate.pk}/download/")
        try:
            response = self.client.post(f"/certificate/{certificate.pk}/delete/")
            self.assertEqual(response.status_code, 302)
            self.assertEqual(GeneratedCertificate.objects.count(), 0)
        finally:
            stream.close()

    def test_delete_ignores_an_off_site_next_url(self):
        self.client.post("/", {**SAMPLE, "template": self.template.pk,
                               "photo": make_photo()})
        certificate = GeneratedCertificate.objects.get()
        response = self.client.post(
            f"/certificate/{certificate.pk}/delete/", {"next": "https://evil.example/"}
        )
        self.assertEqual(response["Location"], "/history/")
        self.assertEqual(GeneratedCertificate.objects.count(), 0)
